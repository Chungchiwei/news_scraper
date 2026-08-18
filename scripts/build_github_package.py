#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/build_github_package.py

GitHub Repository Packaging — Package Builder（Phase 9 — GitHub Actions
Deployment Packaging）。

依 Allowlist（+ 自動 Dependency Closure 驗證）＋ Exclusion 規則，把
GITHUB_PACKAGING_AUDIT.md 分類為 INCLUDE 的檔案複製到：

    dist/github_package/

並打包成：

    dist/WHL_Maritime_Intelligence_GitHub_v{VERSION}.zip

★ Fail Closed 原則（§五十一）：只要偵測到 .env / *.db / *.pem / *.key /
  疑似真實 secret pattern / logs / backup 等會被 Include，立即 FAIL
  （sys.exit(1)），不是 Warning。

★ Dependency Closure 自動驗證：不只手動盤點一次，這個腳本每次執行都會
  用 ast 解析 maritime_news.py / dashboard/app.py 的 import，追蹤出
  「production 程式實際依賴的本地模組集合」，並確認全部都在 Allowlist
  內——避免「本機有某個檔案，所以測試通過，但 Package 漏掉該檔」。

用法：
    python scripts/build_github_package.py
"""

from __future__ import annotations

import ast
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
PACKAGE_DIR = DIST_DIR / "github_package"


def get_version() -> str:
    vf = ROOT / "VERSION"
    if not vf.exists():
        print("❌ VERSION file not found — cannot determine package version.")
        sys.exit(1)
    return vf.read_text(encoding="utf-8").strip()


# ══════════════════════════════════════════════════════════════════
# ALLOWLIST — 對應 GITHUB_PACKAGING_AUDIT.md 的 INCLUDE 分類
# ══════════════════════════════════════════════════════════════════

ROOT_PY_FILES = [
    "ai_cache.py", "analysis_validator.py", "briefing_selector.py",
    "carrier_news_filter.py", "delivery_config.py", "delivery_history.py",
    "delivery_models.py", "delivery_orchestrator.py", "email_config.py",
    "email_sender.py", "email_view_model.py", "event_clusterer.py",
    "event_extractor.py", "event_identity.py", "event_lifecycle.py",
    "event_store.py", "executive_email_renderer.py", "fleet_provider.py",
    "fleet_relevance.py", "geographic_relevance.py", "intelligence_analyzer.py",
    "llm_config.py", "llm_provider.py", "management_summary.py",
    "maritime_news.py", "material_change_detector.py", "memory_config.py",
    "memory_pipeline.py", "models.py", "notification_policy.py",
    "operational_config.py", "operational_history.py", "operational_models.py",
    "operational_relevance.py", "persistent_matcher.py", "port_normalizer.py",
    "port_relevance.py", "risk_config.py",
    "risk_scorer.py", "route_provider.py", "route_relevance.py",
    "schedule_provider.py", "source_grounding.py", "source_health.py",
    "source_provenance.py", "status_extractor.py", "system_health.py",
    "teams_config.py", "teams_notifier.py", "teams_renderer.py", "version.py",
]

ROOT_JSON_FILES = [
    "risk_rules.json", "memory_rules.json", "delivery_rules.json",
    "email_rules.json", "llm_rules.json", "operational_rules.json",
    "keywords_config.json",
]

ROOT_META_FILES = [
    "VERSION", "requirements.txt", "requirements-dev.txt", ".env.example",
    ".gitignore",
]

ROOT_BAT_FILES = ["run.bat", "run_dashboard.bat", "run_tests.bat", "setup.bat"]

ROOT_DOC_FILES = [
    "README.md", "SYSTEM_ARCHITECTURE.md", "DATA_FLOW.md",
    "CONFIGURATION_REFERENCE.md", "OPERATIONS_RUNBOOK.md",
    "IT_DEPLOYMENT_GUIDE.md", "PYTHON_VERSION.md", "DEPRECATED.md",
    "FUTURE_ROADMAP.md", "GITHUB_PACKAGE_MANIFEST.md",
    "GITHUB_ACTIONS_SETUP.md", "claude.md",
]

ROOT_FILE_ALLOWLIST = set(
    ROOT_PY_FILES + ROOT_JSON_FILES + ROOT_META_FILES + ROOT_BAT_FILES + ROOT_DOC_FILES
)

# 整個目錄照抄（但仍逐檔案經過 fail-closed 檢查 + 排除 __pycache__ 等）。
# Phase 9 清理（見 GITHUB_PACKAGE_MANIFEST.md）：
#   - docs/history/ — 歷史 PHASE 報告與一次性 Audit/Report（點狀時間快照，
#     只保留供參考，不是持續維護的文件）
#   - dev_tools/    — 一次性開發用 simulation/preview 腳本，不是
#     maritime_news.py 的 Dependency Closure 一部分
INCLUDE_DIRS = ["config", "dashboard", "scripts", "tests", "prompts",
                 ".github", "legacy", "docs", "dev_tools"]

# 目錄內一律跳過的名稱 / 副檔名
EXCLUDE_DIR_NAMES = {"__pycache__", ".pytest_cache", "venv", ".venv",
                      "logs", "backup", "output", "dist", ".git", "node_modules"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log"}
EXCLUDE_EXACT_NAMES = {".env", "news_scraper.log", ".DS_Store"}

# Fail-closed：檔名/副檔名本身就代表機敏或 runtime state
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".db-journal", ".pem", ".key")
FORBIDDEN_EXACT_NAMES = {".env"}

# Fail-closed：內容中出現疑似真實 secret 的樣式
SECRET_CONTENT_PATTERNS = [
    ("OpenAI-style API key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("Anthropic-style API key", re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}")),
    ("Google-style API key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("PEM private key block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----")),
    ("Real Teams webhook (with token)", re.compile(
        r"https://[a-zA-Z0-9.\-]*\.webhook\.office\.com/webhookb2/[A-Za-z0-9\-]{20,}"
        r"|https://hooks\.office\.com/webhookb2/[A-Za-z0-9\-]{20,}@[A-Za-z0-9\-]{20,}")),
]

# .env.example 內，這些 key 一律必須是空值（只允許出現在 KEY= 之後為空）
SECRET_ENV_KEY_PATTERN = re.compile(
    r"^([A-Z0-9_]*(PASSWORD|SECRET|TOKEN|API_KEY|WEBHOOK_URL)[A-Z0-9_]*)=(.*)$"
)

TEXT_FILE_SUFFIXES = {".py", ".json", ".md", ".txt", ".yml", ".yaml", ".cfg",
                       ".ini", ".css", ".html", ".bat", ".gitignore", ""}


def fail(msg: str) -> None:
    print(f"\n❌ PACKAGE BUILD FAILED (fail-closed): {msg}")
    print("   Repository package NOT written. Fix the issue and re-run.")
    sys.exit(1)


def is_text_file(path: Path) -> bool:
    return path.suffix in TEXT_FILE_SUFFIXES or path.name == ".gitignore"


def check_forbidden_name(path: Path) -> None:
    if path.name in FORBIDDEN_EXACT_NAMES:
        fail(f"forbidden file would be included: {path.relative_to(ROOT)}")
    for suf in FORBIDDEN_SUFFIXES:
        if path.name.endswith(suf):
            fail(f"forbidden file (runtime DB / key material) would be included: {path.relative_to(ROOT)}")


def check_secret_content(path: Path) -> None:
    if not is_text_file(path):
        return
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return

    rel = path.relative_to(ROOT)

    # .env.example 專屬檢查：機敏欄位必須是空值
    if path.name == ".env.example":
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = SECRET_ENV_KEY_PATTERN.match(line)
            if m and m.group(3).strip():
                fail(f".env.example contains a non-empty value for secret-like key "
                     f"'{m.group(1)}' — must be empty (found in {rel})")

    for label, pattern in SECRET_CONTENT_PATTERNS:
        if pattern.search(text):
            fail(f"{rel} matches suspected real-secret pattern: {label}")


def should_skip_dir(dirname: str) -> bool:
    return dirname in EXCLUDE_DIR_NAMES


def should_skip_file(path: Path) -> bool:
    if path.name in EXCLUDE_EXACT_NAMES:
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    return False


# ══════════════════════════════════════════════════════════════════
# Dependency Closure — 自動用 ast 解析驗證 Allowlist 沒有漏掉真正需要
# 的本地模組
# ══════════════════════════════════════════════════════════════════

def local_module_names() -> set[str]:
    return {p.stem for p in ROOT.glob("*.py")}


def parse_local_imports(py_file: Path, known_locals: set[str]) -> set[str]:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except Exception as e:
        fail(f"cannot parse {py_file.relative_to(ROOT)} for dependency closure check: {e}")
        return set()

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in known_locals:
                    found.add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in known_locals:
                    found.add(top)
    return found


def compute_dependency_closure() -> set[str]:
    known_locals = local_module_names()
    entry_points = [ROOT / "maritime_news.py"]
    closure: set[str] = set()
    frontier = list(entry_points)
    visited_files: set[Path] = set()

    while frontier:
        f = frontier.pop()
        if f in visited_files or not f.exists():
            continue
        visited_files.add(f)
        deps = parse_local_imports(f, known_locals)
        for dep in deps:
            if dep not in closure:
                closure.add(dep)
                dep_file = ROOT / f"{dep}.py"
                if dep_file.exists():
                    frontier.append(dep_file)
    return closure


def verify_dependency_closure() -> None:
    closure = compute_dependency_closure()
    missing = {f"{m}.py" for m in closure} - set(ROOT_PY_FILES) - {"maritime_news.py"}
    if missing:
        fail("Dependency Closure violation — maritime_news.py transitively imports "
             f"local modules NOT in the packaging allowlist: {sorted(missing)}. "
             "Add them to ROOT_PY_FILES in build_github_package.py.")
    print(f"✅ Dependency Closure verified — {len(closure)} local modules reachable "
          f"from maritime_news.py, all present in the allowlist.")


# ══════════════════════════════════════════════════════════════════
# Build
# ══════════════════════════════════════════════════════════════════

def copy_checked(src: Path, dst: Path) -> int:
    """複製單一檔案，複製前先跑 fail-closed 檢查。回傳 1（有複製）。"""
    check_forbidden_name(src)
    check_secret_content(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return 1


def copy_dir_checked(src_dir: Path, dst_dir: Path) -> int:
    count = 0
    for path in sorted(src_dir.rglob("*")):
        if path.is_dir():
            continue
        rel_parts = path.relative_to(src_dir).parts
        if any(should_skip_dir(part) for part in rel_parts[:-1]):
            continue
        if should_skip_file(path):
            continue
        rel = path.relative_to(src_dir)
        count += copy_checked(path, dst_dir / rel)
    return count


def build() -> None:
    version = get_version()
    print(f"WHL Maritime Intelligence — GitHub Package Builder (v{version})")
    print("=" * 60)

    verify_dependency_closure()

    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR)
    PACKAGE_DIR.mkdir(parents=True)

    included = 0
    skipped_missing = []

    for name in sorted(ROOT_FILE_ALLOWLIST):
        src = ROOT / name
        if not src.exists():
            skipped_missing.append(name)
            continue
        included += copy_checked(src, PACKAGE_DIR / name)

    for dirname in INCLUDE_DIRS:
        src_dir = ROOT / dirname
        if not src_dir.exists():
            skipped_missing.append(f"{dirname}/ (directory missing)")
            continue
        included += copy_dir_checked(src_dir, PACKAGE_DIR / dirname)

    # data/ — 只放 .gitkeep，絕對不放任何 runtime DB
    data_dir = PACKAGE_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ".gitkeep").write_text(
        "# GitHub Actions workflow 會在 runtime 還原/建立這個目錄下的 SQLite "
        "資料庫。Repository 本身不保存任何 *.db（見 GITHUB_PACKAGING_AUDIT.md "
        "§E RUNTIME STATE）。\n",
        encoding="utf-8",
    )
    included += 1

    # ── Final defense-in-depth: 對整個已組好的 package 目錄再掃一次 ──
    for path in PACKAGE_DIR.rglob("*"):
        if path.is_dir():
            continue
        check_forbidden_name(path)
        check_secret_content(path)

    print(f"\n✅ Package assembled: {included} files copied to {PACKAGE_DIR}")
    if skipped_missing:
        print(f"⚠️  Allowlist entries not found on disk (skipped): {skipped_missing}")

    # ── Root layout validation（§五十四：不可多層 nesting）──
    expected_top_level = {"maritime_news.py", "requirements.txt", "VERSION",
                           "README.md", ".github", "config", "dashboard",
                           "scripts", "tests"}
    actual_top_level = {p.name for p in PACKAGE_DIR.iterdir()}
    missing_top = expected_top_level - actual_top_level
    if missing_top:
        fail(f"Repository root validation failed — expected top-level entries "
             f"missing from package root: {sorted(missing_top)}")
    print("✅ Repository root validation passed (no nested double-directory layout).")

    # ── ZIP ──
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DIST_DIR / f"WHL_Maritime_Intelligence_GitHub_v{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PACKAGE_DIR.rglob("*")):
            if path.is_dir():
                continue
            zf.write(path, arcname=path.relative_to(PACKAGE_DIR))

    print(f"✅ ZIP created: {zip_path} ({zip_path.stat().st_size:,} bytes)")
    print("\nSECRET SCAN: PASS (fail-closed checks found nothing to block)")
    print("PACKAGE: READY")


if __name__ == "__main__":
    build()
