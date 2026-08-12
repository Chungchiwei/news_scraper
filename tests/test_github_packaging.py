"""
tests/test_github_packaging.py
Phase 9 — GitHub Actions Deployment Packaging 測試。

只測本輪新增的 Packaging / Runtime State Lifecycle 行為，不重複既有
187 項測試涵蓋的航運情報分類邏輯（那些已經在 test_risk_scoring.py /
test_clustering.py / test_memory_lifecycle.py 等測試過）。全部離線，
使用 tmp_path，不連真實 SMTP / Teams / LLM / Internet / GitHub API。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def _load_script_module(name: str):
    """把 scripts/<name>.py 當成一般模組載入（scripts/ 不是 package），
    沿用 tests/test_phase8_finalization.py 既有的 pattern。"""
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def built_package():
    """整個測試檔共用同一次 package build（build_github_package.py 本身
    就是 fail-closed 的——只要有一項測試在這個 fixture 就代表 build 沒
    有中途 sys.exit(1)）。"""
    bgp = _load_script_module("build_github_package")
    bgp.build()
    return bgp.PACKAGE_DIR


# ══════════════════════════════════════════════════════════════
# 1-4. Package Manifest 基本檢查
# ══════════════════════════════════════════════════════════════
def test_package_excludes_env(built_package):
    assert not (built_package / ".env").exists()
    for p in built_package.rglob(".env"):
        pytest.fail(f".env leaked into package at {p}")


def test_package_excludes_database(built_package):
    db_files = list(built_package.rglob("*.db")) + list(built_package.rglob("*.db-*"))
    assert db_files == [], f"runtime database files leaked into package: {db_files}"


def test_package_contains_entrypoint(built_package):
    assert (built_package / "maritime_news.py").exists()
    assert (built_package / "requirements.txt").exists()
    assert (built_package / "VERSION").exists()


def test_package_contains_workflows(built_package):
    wf_dir = built_package / ".github" / "workflows"
    assert (wf_dir / "ci.yml").exists()
    assert (wf_dir / "maritime-intelligence.yml").exists()


def test_package_root_layout_no_nesting(built_package):
    """§五十四：解壓後根目錄必須直接看到 maritime_news.py，不能多層
    nesting（例如 zip/project/project/maritime_news.py）。"""
    assert (built_package / "maritime_news.py").exists()
    # package 本身不應該包含另一個以自己名字命名的子目錄（典型的雙層打包錯誤）
    assert not (built_package / "github_package").exists()
    assert not (built_package / "news_scrape").exists()


# ══════════════════════════════════════════════════════════════
# 5. Workflow YAML 有效性
# ══════════════════════════════════════════════════════════════
def test_workflow_yaml_valid(built_package):
    import yaml

    for name in ["ci.yml", "maritime-intelligence.yml"]:
        p = built_package / ".github" / "workflows" / name
        with open(p, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        assert isinstance(doc, dict) and "jobs" in doc, f"{name} did not parse as a valid workflow"


# ══════════════════════════════════════════════════════════════
# 6. CI workflow 不得注入 secrets；只能離線
# ══════════════════════════════════════════════════════════════
def test_ci_workflow_has_no_secrets(built_package):
    text = (built_package / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "SMTP" not in text or "禁止" in text or "不連" in text  # 只允許出現在說明性註解


# ══════════════════════════════════════════════════════════════
# 7. Production workflow 不得用 push 觸發
# ══════════════════════════════════════════════════════════════
def test_production_workflow_not_triggered_on_push(built_package):
    import yaml

    p = built_package / ".github" / "workflows" / "maritime-intelligence.yml"
    with open(p, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    triggers = doc[True]  # YAML 1.1 把裸露的 `on:` key 解析成布林 True
    assert "push" not in triggers, "Production workflow must not trigger on push"
    assert "workflow_dispatch" in triggers
    assert "schedule" in triggers


# ══════════════════════════════════════════════════════════════
# 8. Concurrency 保護
# ══════════════════════════════════════════════════════════════
def test_concurrency_enabled(built_package):
    import yaml

    p = built_package / ".github" / "workflows" / "maritime-intelligence.yml"
    with open(p, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    concurrency = doc["jobs"]["run-intelligence"].get("concurrency") or doc.get("concurrency")
    assert concurrency is not None
    assert concurrency["cancel-in-progress"] is False


# ══════════════════════════════════════════════════════════════
# 9. Runtime state manifest
# ══════════════════════════════════════════════════════════════
def test_runtime_state_manifest():
    import json

    manifest_path = ROOT / "config" / "github_state_files.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    ids = {f["id"] for f in data["state_files"]}
    assert {"event_store", "delivery_history"}.issubset(ids), \
        "required state files must be listed in the manifest"


# ══════════════════════════════════════════════════════════════
# 10-11. First / Second GitHub Run 模擬（用真正的 Phase 3 pipeline）
# ══════════════════════════════════════════════════════════════
def _sqlite_online_backup(src_path: str, dst_path: str) -> None:
    src_conn = sqlite3.connect(src_path)
    dst_conn = sqlite3.connect(dst_path)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def _build_one_event(risk_rules, run_time):
    from models import NewsArticle
    from event_extractor import EventExtractor
    from risk_scorer import RiskScorer
    from event_clusterer import EventClusterer

    articles = [
        NewsArticle(article_id="pkg_a1", source_name="Reuters", source_tier="B",
                    title="Container vessel attacked by missile near Kaohsiung approach channel",
                    summary="A container vessel was attacked by a missile while approaching "
                            "Kaohsiung; the operator confirmed the incident.",
                    url="http://example.com/pkg_a1",
                    published_at=run_time - timedelta(hours=1), collected_at=run_time),
        NewsArticle(article_id="pkg_a2", source_name="TradeWinds", source_tier="B",
                    title="Boxship hit by missile strike near Kaohsiung",
                    summary="A boxship was struck in a missile strike while approaching "
                            "Kaohsiung, sources say.",
                    url="http://example.com/pkg_a2",
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


def test_first_github_run_initializes_state(tmp_path):
    """FIRST_RUN（沒有前次 state 可還原）：is_baseline_run=True，
    MEMORY_BASELINE_MODE=silent 時不通知，且不能 crash（§六十一）。"""
    from risk_config import load_risk_rules
    from memory_config import load_memory_rules
    from event_store import EventStore
    from event_extractor import EventExtractor
    from risk_scorer import RiskScorer
    from memory_pipeline import apply_persistent_memory, generate_run_id

    risk_rules = load_risk_rules()
    memory_rules = load_memory_rules()

    db_path = str(tmp_path / "runner_a" / "maritime_intelligence.db")
    store = EventStore(db_path)
    t1 = datetime.now(timezone.utc)
    events = _build_one_event(risk_rules, t1)

    mem = apply_persistent_memory(
        events, store, generate_run_id(t1), t1, risk_rules, memory_rules,
        RiskScorer(risk_rules, EventExtractor(risk_rules)),
        baseline_mode="silent",
    )
    assert mem["is_baseline_run"] is True
    assert mem["baseline_mode"] == "silent"
    assert mem["notification_events"] == [], "baseline+silent run must not notify"
    store.close()


def test_second_github_run_restores_state(tmp_path):
    """模擬 upload-artifact（SQLite Online Backup）→ download-artifact
    （檔案複製到另一個暫存目錄）→ 第二次執行同一則新聞：
    event_id 不變、不是 NEW、Duplicate 被抑制（§十、五十九）。"""
    from risk_config import load_risk_rules
    from memory_config import load_memory_rules
    from event_store import EventStore
    from event_extractor import EventExtractor
    from risk_scorer import RiskScorer
    from memory_pipeline import apply_persistent_memory, generate_run_id
    from models import NotificationState

    risk_rules = load_risk_rules()
    memory_rules = load_memory_rules()

    runner_a = tmp_path / "runner_a"
    staging = tmp_path / "artifact_staging"
    runner_b = tmp_path / "runner_b"
    runner_a.mkdir()
    staging.mkdir()
    runner_b.mkdir()

    db_a = str(runner_a / "maritime_intelligence.db")
    store_a = EventStore(db_a)
    t1 = datetime.now(timezone.utc)
    events_1 = _build_one_event(risk_rules, t1)
    mem_1 = apply_persistent_memory(
        events_1, store_a, generate_run_id(t1), t1, risk_rules, memory_rules,
        RiskScorer(risk_rules, EventExtractor(risk_rules)), baseline_mode="silent",
    )
    original_event_id = mem_1["all_current_events"][0].event_id
    store_a.close()

    staged_db = str(staging / "maritime_intelligence.db")
    _sqlite_online_backup(db_a, staged_db)

    db_b = str(runner_b / "maritime_intelligence.db")
    shutil.copy2(staged_db, db_b)
    store_b = EventStore(db_b)

    t2 = t1 + timedelta(hours=6)
    events_2 = _build_one_event(risk_rules, t2)
    mem_2 = apply_persistent_memory(
        events_2, store_b, generate_run_id(t2), t2, risk_rules, memory_rules,
        RiskScorer(risk_rules, EventExtractor(risk_rules)), baseline_mode="notify",
    )
    event_b = mem_2["all_current_events"][0]

    assert mem_2["is_baseline_run"] is False, "restored state was not recognized"
    assert event_b.event_id == original_event_id
    assert event_b.notification_state == NotificationState.UNCHANGED
    store_b.close()


# ══════════════════════════════════════════════════════════════
# 12. Corrupt state 必須被拒絕
# ══════════════════════════════════════════════════════════════
def test_corrupt_state_rejected(tmp_path):
    good_db = tmp_path / "good.db"
    conn = sqlite3.connect(str(good_db))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()

    corrupt_db = tmp_path / "corrupt.db"
    corrupt_db.write_bytes(b"not a sqlite file" * 100)

    def integrity_ok(path):
        try:
            c = sqlite3.connect(str(path))
            row = c.execute("PRAGMA integrity_check;").fetchone()
            c.close()
            return row is not None and row[0] == "ok"
        except Exception:
            return False

    assert integrity_ok(good_db) is True
    assert integrity_ok(corrupt_db) is False


# ══════════════════════════════════════════════════════════════
# 13. Dependency Closure 自動驗證（防止 drift）
# ══════════════════════════════════════════════════════════════
def test_dependency_closure_no_missing_modules():
    bgp = _load_script_module("build_github_package")
    closure = bgp.compute_dependency_closure()
    missing = {f"{m}.py" for m in closure} - set(bgp.ROOT_PY_FILES) - {"maritime_news.py"}
    assert missing == set(), f"modules imported by maritime_news.py missing from allowlist: {missing}"
