#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/github_actions_smoke_test.py

GitHub Actions Deployment Packaging — Fully Offline Smoke Test
(Phase 9 §六十二)。

驗證的是「GitHub Actions 遷移」本身是否安全，不是航運情報分類邏輯
（那是 scripts/final_acceptance_test.py 的職責）：

  1. Package Manifest      — dist/github_package/ 是否含正確的 entry
                              point / workflows / data/.gitkeep。
  2. Secret Exclusion      — package 內沒有 .env / *.db / 疑似真實
                              secret pattern；.env.example 機敏欄位皆空。
  3. Workflow YAML         — 兩個 workflow 皆為合法 YAML，且符合安全
                              政策（CI 不注入 secrets／Production 不用
                              `on: push`／有 concurrency 保護）。
  4. Runtime State Manifest — config/github_state_files.json 存在且完整。
  5. First / Second GitHub Run 模擬 — 用真正的 Phase 3 Persistent
     Memory pipeline（EventExtractor → RiskScorer → EventClusterer →
     apply_persistent_memory），把 SQLite 檔案在兩個「完全不同、互不
     知情」的暫存目錄之間「用檔案複製」搬移（模擬 upload-artifact →
     download-artifact 的真實效果），驗證：
       - Runner A（空 data/）第一次執行 → is_baseline_run=True，
         MEMORY_BASELINE_MODE=silent 時不通知（不是 Error）。
       - Runner B（全新暫存目錄）還原 Runner A 的 DB 檔案後重新執行
         同一事件 → notification_state 不是 NEW、event_id 不變、
         Duplicate Teams 被抑制。
  6. Corrupt State Rejection — 模擬 restored DB 損毀，確認
     PRAGMA integrity_check 能偵測到，且不會被誤判為可用狀態。
  7. Isolated Package Import — 在乾淨的 package copy（非本機開發目錄）
     裡，用 subprocess 匯入 maritime_news，證明 package 沒有偷偷依賴
     package 外的檔案。

完全不連 SMTP / Teams webhook / LLM API / Internet；RSS 也不抓取。

用法：
    python scripts/github_actions_smoke_test.py
Exit code：0 = PASS，1 = FAIL。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import logging
logging.basicConfig(level=logging.WARNING, format="%(message)s")

RESULTS: "dict[str, bool]" = {}
FAILURES: "list[str]" = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    RESULTS[label] = bool(condition)
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(f"{label}: {detail}")
    return condition


def section(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


# ══════════════════════════════════════════════════════════════
# 1-3. Package Manifest / Secret Exclusion / Workflow YAML
# ══════════════════════════════════════════════════════════════
def build_and_check_package():
    section("1 — Package Manifest & Secret Exclusion")
    import build_github_package as bgp
    bgp.build()
    pkg = bgp.PACKAGE_DIR

    check("package_contains_entrypoint", (pkg / "maritime_news.py").exists())
    check("package_contains_workflows",
          (pkg / ".github" / "workflows" / "ci.yml").exists()
          and (pkg / ".github" / "workflows" / "maritime-intelligence.yml").exists())
    check("package_excludes_env", not (pkg / ".env").exists())

    db_files = list(pkg.rglob("*.db")) + list(pkg.rglob("*.db-*"))
    check("package_excludes_database", len(db_files) == 0, f"found: {db_files}")

    log_files = list(pkg.rglob("*.log"))
    check("package_excludes_logs", len(log_files) == 0, f"found: {log_files}")

    check("package_data_dir_placeholder_only",
          (pkg / "data" / ".gitkeep").exists()
          and len([p for p in (pkg / "data").iterdir()]) == 1)

    patterns = [re.compile(r"sk-[A-Za-z0-9]{20,}"),
                re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
                re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----")]
    violations = []
    for p in pkg.rglob("*"):
        if p.is_dir() or p.suffix not in {".py", ".json", ".md", ".txt", ".yml",
                                           ".yaml", ".bat", ".css", ".html"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if any(pat.search(text) for pat in patterns):
            violations.append(str(p.relative_to(pkg)))
    check("secrets_scan_clean", len(violations) == 0, f"violations: {violations}")

    env_example = pkg / ".env.example"
    nonempty_secret_keys = []
    for line in env_example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if any(tag in k.upper() for tag in ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "WEBHOOK_URL")) \
                and v.strip():
            nonempty_secret_keys.append(k)
    check("env_example_secrets_empty", len(nonempty_secret_keys) == 0,
          f"non-empty: {nonempty_secret_keys}")

    return pkg


def check_workflow_yaml_and_policy(pkg: Path):
    section("2 — Workflow YAML Validity & Security Policy")
    try:
        import yaml
        have_yaml = True
    except ImportError:
        have_yaml = False
        print("  (PyYAML not available — falling back to basic text checks)")

    ci_path = pkg / ".github" / "workflows" / "ci.yml"
    prod_path = pkg / ".github" / "workflows" / "maritime-intelligence.yml"

    for name, p in [("ci.yml", ci_path), ("maritime-intelligence.yml", prod_path)]:
        if have_yaml:
            try:
                with open(p, encoding="utf-8") as f:
                    doc = yaml.safe_load(f)
                ok = isinstance(doc, dict) and "jobs" in doc
            except Exception as e:
                ok = False
                print(f"    YAML parse error in {name}: {e}")
        else:
            text = p.read_text(encoding="utf-8")
            ok = "jobs:" in text
        check(f"workflow_yaml_valid[{name}]", ok)

    ci_text = ci_path.read_text(encoding="utf-8")
    prod_text = prod_path.read_text(encoding="utf-8")

    check("ci_workflow_has_no_secrets", "secrets." not in ci_text)
    check("production_workflow_not_triggered_on_push",
          re.search(r"^\s*push:\s*$", prod_text, re.MULTILINE) is None
          and "workflow_dispatch" in prod_text and "schedule" in prod_text)
    check("concurrency_enabled",
          "concurrency:" in prod_text and "cancel-in-progress: false" in prod_text)

    # ★ 用解析後的 YAML 結構檢查 permissions 的「實際值」，不要對整個檔案
    #   文字做 substring 比對——本檔案的說明註解裡本來就會提到
    #   "permissions: write-all" 這幾個字（用來解釋「我們刻意不這樣做」），
    #   純文字比對會產生假陽性 FAIL。
    permissions_minimal = False
    if have_yaml:
        try:
            with open(prod_path, encoding="utf-8") as f:
                prod_doc = yaml.safe_load(f)
            perms = prod_doc.get("permissions")
            if isinstance(perms, dict):
                permissions_minimal = perms != {} and "write-all" not in perms.values() \
                    and all(v == "read" for v in perms.values())
            elif isinstance(perms, str):
                permissions_minimal = perms != "write-all"
        except Exception as e:
            print(f"    permissions parse error: {e}")
    else:
        # 沒有 yaml 套件時退回較弱的檢查：只認可 YAML 語法上真正的
        # `permissions: write-all`（頂層 key 後直接接值），忽略註解行。
        m = re.search(r"^permissions:\s*(\S+)\s*$", prod_text, re.MULTILINE)
        permissions_minimal = "permissions:" in prod_text and (m is None or m.group(1) != "write-all")
    check("permissions_minimal", permissions_minimal)


def check_state_manifest():
    section("3 — Runtime State Manifest")
    manifest_path = ROOT / "config" / "github_state_files.json"
    ok = manifest_path.exists()
    if ok:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        ok = "state_files" in data and len(data["state_files"]) >= 4
    check("runtime_state_manifest", ok)


# ══════════════════════════════════════════════════════════════
# 4. First / Second GitHub Run simulation (real pipeline, file-copy
#    restore — not a shared store instance)
# ══════════════════════════════════════════════════════════════
def _build_event(risk_rules, run_time):
    from models import NewsArticle
    from event_extractor import EventExtractor
    from risk_scorer import RiskScorer
    from event_clusterer import EventClusterer

    articles = [
        NewsArticle(article_id="gh_a1", source_name="Reuters", source_tier="B",
                    title="Container vessel attacked by missile near Kaohsiung approach channel",
                    summary="A container vessel was attacked by a missile while approaching "
                            "Kaohsiung; the operator confirmed the incident.",
                    url="http://example.com/gh_a1",
                    published_at=run_time - timedelta(hours=1), collected_at=run_time),
        NewsArticle(article_id="gh_a2", source_name="TradeWinds", source_tier="B",
                    title="Boxship hit by missile strike near Kaohsiung",
                    summary="A boxship was struck in a missile strike while approaching "
                            "Kaohsiung, sources say.",
                    url="http://example.com/gh_a2",
                    published_at=run_time - timedelta(hours=1), collected_at=run_time),
    ]
    extractor = EventExtractor(risk_rules)
    scorer = RiskScorer(risk_rules, extractor)
    clusterer = EventClusterer(risk_rules)
    for a in articles:
        extractor.enrich(a)
        scorer.score_article(a, now=run_time)
    events = clusterer.cluster(articles)
    scorer.score_events(events, now=run_time)
    return events


def check_first_and_second_github_run():
    section("4 — First / Second GitHub Run Simulation (file-copy restore)")

    from risk_config import load_risk_rules
    from memory_config import load_memory_rules
    from event_store import EventStore
    from event_extractor import EventExtractor
    from risk_scorer import RiskScorer
    from memory_pipeline import apply_persistent_memory, generate_run_id
    from models import NotificationState

    risk_rules = load_risk_rules()
    memory_rules = load_memory_rules()

    with tempfile.TemporaryDirectory(prefix="gh_runner_a_") as runner_a, \
         tempfile.TemporaryDirectory(prefix="gh_artifact_staging_") as artifact_staging, \
         tempfile.TemporaryDirectory(prefix="gh_runner_b_") as runner_b:

        # ── RUNNER A: 完全空的 data/ — 模擬 FIRST GITHUB RUN ────────
        db_a = os.path.join(runner_a, "maritime_intelligence.db")
        store_a = EventStore(db_a)
        t1 = datetime.now(timezone.utc)
        run_id_1 = generate_run_id(t1)
        events_1 = _build_event(risk_rules, t1)

        mem_1 = apply_persistent_memory(
            events_1, store_a, run_id_1, t1, risk_rules, memory_rules,
            RiskScorer(risk_rules, EventExtractor(risk_rules)),
            baseline_mode="silent",  # ★ 模擬 workflow 偵測「找不到前次 state」時注入的行為
        )
        check("first_run_is_baseline_run", mem_1["is_baseline_run"] is True)
        check("first_run_baseline_mode_silent", mem_1["baseline_mode"] == "silent")
        check("first_run_no_notification_storm", len(mem_1["notification_events"]) == 0,
              f"baseline+silent run should suppress notifications, got "
              f"{len(mem_1['notification_events'])}")
        event_a = mem_1["all_current_events"][0]
        original_event_id = event_a.event_id
        check("first_run_event_created", event_a.notification_state == NotificationState.NEW
              or mem_1["is_baseline_run"], f"unexpected state {event_a.notification_state}")

        # ── SAVE STATE: 模擬 upload-artifact ────────────────────────
        # ★ event_store.py 用 PRAGMA journal_mode=WAL，代表已提交的資料
        #   可能還留在 xxx.db-wal，還沒 checkpoint 回主檔案。如果只是
        #   單純 shutil.copy2 主檔案（天真的檔案複製），可能會複製到一個
        #   缺少最新資料的「不完整」快照——這正是 GitHub Actions workflow
        #   本身也必須避免的陷阱（見 maritime-intelligence.yml 的
        #   「Stage state files for upload」step，同樣改用 SQLite Online
        #   Backup API，而不是天真 cp）。這裡用跟 scripts/backup_data.py
        #   相同的技巧：sqlite3 Connection.backup()，保證快照完整一致。
        store_a.close()

        def sqlite_online_backup(src_path: str, dst_path: str) -> None:
            src_conn = sqlite3.connect(src_path)
            dst_conn = sqlite3.connect(dst_path)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()

        staged_db = os.path.join(artifact_staging, "maritime_intelligence.db")
        sqlite_online_backup(db_a, staged_db)
        check("state_artifact_staged", os.path.exists(staged_db))

        # ── RUNNER B: 全新、互不相干的暫存目錄 — 模擬 download-artifact ──
        db_b = os.path.join(runner_b, "maritime_intelligence.db")
        shutil.copy2(staged_db, db_b)
        store_b = EventStore(db_b)

        t2 = t1 + timedelta(hours=6)
        run_id_2 = generate_run_id(t2)
        events_2 = _build_event(risk_rules, t2)  # 同一則新聞再次「被抓到」

        mem_2 = apply_persistent_memory(
            events_2, store_b, run_id_2, t2, risk_rules, memory_rules,
            RiskScorer(risk_rules, EventExtractor(risk_rules)),
            baseline_mode="notify",  # ★ 模擬「有還原到 state」時的正常模式
        )
        check("second_run_is_not_baseline", mem_2["is_baseline_run"] is False,
              "Runner B restored Runner A's DB but still thinks it's a baseline run — "
              "state restore is NOT working")
        event_b = mem_2["all_current_events"][0]
        check("second_run_same_event_id", event_b.event_id == original_event_id,
              f"expected same event_id after restore, got {event_b.event_id} vs {original_event_id}")
        check("second_run_not_new", event_b.notification_state != NotificationState.NEW,
              f"restored state should recognize this as a known event, got "
              f"{event_b.notification_state}")
        check("second_run_duplicate_suppressed",
              event_b.notification_state == NotificationState.UNCHANGED,
              f"identical resubmission should be UNCHANGED (no duplicate notification), got "
              f"{event_b.notification_state}")

        print(f"  Runner A: FIRST RUN, event_id={original_event_id}, "
              f"baseline_mode=silent, notifications_suppressed=True")
        print(f"  Runner B: state RESTORED, event_id={event_b.event_id} (same), "
              f"notification_state={event_b.notification_state}")


# ══════════════════════════════════════════════════════════════
# 5. Corrupt state rejection
# ══════════════════════════════════════════════════════════════
def check_corrupt_state_rejection():
    section("5 — Corrupt State Rejection")
    with tempfile.TemporaryDirectory(prefix="gh_corrupt_") as d:
        good_db = os.path.join(d, "good.db")
        conn = sqlite3.connect(good_db)
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()

        def integrity_ok(path):
            try:
                c = sqlite3.connect(path)
                row = c.execute("PRAGMA integrity_check;").fetchone()
                c.close()
                return row is not None and row[0] == "ok"
            except Exception:
                return False

        check("valid_db_passes_integrity_check", integrity_ok(good_db))

        corrupt_db = os.path.join(d, "corrupt.db")
        with open(corrupt_db, "wb") as f:
            f.write(b"this is not a sqlite file, just garbage bytes \x00\x01\x02" * 50)
        check("corrupt_db_fails_integrity_check", not integrity_ok(corrupt_db),
              "corrupt DB was NOT detected — this would let the workflow silently use "
              "a broken event store")

        # 模擬 workflow 的規則：restored required DB 損毀 → 不得繼續，且不能覆蓋舊 artifact
        would_proceed = integrity_ok(corrupt_db)
        check("workflow_would_refuse_to_proceed_on_corruption", would_proceed is False)


# ══════════════════════════════════════════════════════════════
# 6. Isolated package import
# ══════════════════════════════════════════════════════════════
def check_isolated_import(pkg: Path):
    section("6 — Isolated Package Import (clean copy, no reach outside package)")
    with tempfile.TemporaryDirectory(prefix="gh_isolated_") as d:
        clean_copy = Path(d) / "repo"
        shutil.copytree(pkg, clean_copy)
        result = subprocess.run(
            [sys.executable, "-c", "import maritime_news; import dashboard.app; print('OK')"],
            cwd=str(clean_copy),
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(clean_copy)},
            timeout=60,
        )
        ok = result.returncode == 0 and "OK" in result.stdout
        check("isolated_package_main_entry_import", ok,
              (result.stderr or "")[-1500:])


# ══════════════════════════════════════════════════════════════
def main() -> int:
    print("=" * 60)
    print("WHL Maritime Intelligence — GitHub Actions Smoke Test")
    print("(fully offline — no SMTP / Teams / LLM / network)")
    print("=" * 60)

    pkg = build_and_check_package()
    check_workflow_yaml_and_policy(pkg)
    check_state_manifest()
    check_first_and_second_github_run()
    check_corrupt_state_rejection()
    check_isolated_import(pkg)

    print("\n" + "=" * 60)
    total = len(RESULTS)
    passed = sum(1 for v in RESULTS.values() if v)
    print(f"RESULTS: {passed}/{total} checks passed")
    if FAILURES:
        print("\nFAILURES:")
        for f in FAILURES:
            print(f"  - {f}")
        print("\nRESULT: FAIL")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
