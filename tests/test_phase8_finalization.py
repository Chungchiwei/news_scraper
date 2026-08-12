"""
tests/test_phase8_finalization.py
Phase 8 — Finalization 測試（§三十九〜四十一，12 項指定測試）。

只測 Phase 8 新增的收尾行為（VERSION／.env.example／backup／health_check／
graceful degradation／final acceptance）。不重複 Phase 1-7 既有的 175 項
測試涵蓋範圍，也不修改任何一項既有測試。全部離線、使用 tmp_path，
不連真實 SMTP / Teams / LLM / Internet。
"""

import importlib.util
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def _load_script_module(name: str):
    """把 scripts/<name>.py 當成一般模組載入（scripts/ 不是 package）。"""
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ══════════════════════════════════════════════════════════════
# 1. .env.example 不得含真實機密
# ══════════════════════════════════════════════════════════════
def test_env_example_has_no_secrets():
    from dotenv import dotenv_values

    env_example = ROOT / ".env.example"
    assert env_example.exists(), ".env.example must exist"

    values = dotenv_values(str(env_example))
    secret_keys = [
        "MAIL_PASSWORD", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "TEAMS_MANAGEMENT_WEBHOOK_URL", "TEAMS_SYSTEM_WEBHOOK_URL",
        "DASHBOARD_PASSWORD", "MAIL_USER", "TARGET_EMAIL",
    ]
    for key in secret_keys:
        assert key in values, f"{key} should be listed in .env.example (even if empty)"
        assert not values[key], f"{key} must be empty in .env.example, got {values[key]!r}"


# ══════════════════════════════════════════════════════════════
# 2. 必要目錄會自動建立（以 backup_data.py 的 parent.mkdir 行為驗證）
# ══════════════════════════════════════════════════════════════
def test_required_directories_created(tmp_path):
    backup_data = _load_script_module("backup_data")

    src = tmp_path / "src.db"
    conn = sqlite3.connect(str(src))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()

    dst = tmp_path / "backup" / "20260101_0000" / "nested" / "out.db"
    assert not dst.parent.exists()
    backup_data.sqlite_online_backup(src, dst)
    assert dst.parent.exists(), "backup_data.sqlite_online_backup must create missing parent directories"
    assert dst.exists()


# ══════════════════════════════════════════════════════════════
# 3/4. health_check.py — READY / missing critical config
# ══════════════════════════════════════════════════════════════
def test_health_check_ready(tmp_path, monkeypatch):
    health_check = _load_script_module("health_check")

    monkeypatch.setenv("MAIL_USER", "user@example.com")
    monkeypatch.setenv("MAIL_PASSWORD", "app-password")
    monkeypatch.setenv("TARGET_EMAIL", "target@example.com")
    monkeypatch.delenv("TEAMS_ENABLED", raising=False)
    monkeypatch.delenv("LLM_ENABLED", raising=False)

    report = health_check.Report()
    health_check.check_email(report)
    health_check.check_teams(report)
    health_check.check_llm(report)

    assert report.fatal_count == 0
    statuses = dict(report.rows)
    assert statuses["Email"] == "CONFIGURED"
    assert statuses["Teams"] == "DISABLED"
    assert statuses["LLM"] == "DISABLED"


def test_health_check_missing_critical_config(monkeypatch):
    health_check = _load_script_module("health_check")

    monkeypatch.delenv("MAIL_USER", raising=False)
    monkeypatch.delenv("MAIL_PASSWORD", raising=False)
    monkeypatch.delenv("TARGET_EMAIL", raising=False)

    report = health_check.Report()
    health_check.check_email(report)

    assert report.fatal_count == 1
    statuses = dict(report.rows)
    assert statuses["Email"].startswith("NOT CONFIGURED")


# ══════════════════════════════════════════════════════════════
# 5/6. backup_data.py — 使用 SQLite Backup API + Retention
# ══════════════════════════════════════════════════════════════
def test_backup_uses_sqlite_backup(tmp_path):
    """確認 backup 是用 sqlite3 Connection.backup()，不是 shutil.copy 檔案複製
    （§十八：粗暴複製可能在寫入中途拿到損毀檔案）。"""
    import inspect

    backup_data = _load_script_module("backup_data")
    # 只檢查實際負責複製的函式本身有沒有用 shutil 做檔案複製——檔案開頭
    # 的說明文字本身會提到『為什麼不能用 shutil copy』，不能拿整份檔案的
    # 原始碼字串去比對，否則連文件說明都會誤判。
    fn_source = inspect.getsource(backup_data.sqlite_online_backup)
    assert "shutil" not in fn_source, \
        f"sqlite_online_backup() must not use shutil file-copy, got:\n{fn_source}"
    assert "src_conn.backup(dst_conn)" in fn_source, \
        "sqlite_online_backup() must use the SQLite Online Backup API"

    # 功能面驗證：backup 出來的檔案是可讀、內容正確的 SQLite DB。
    src = tmp_path / "src.db"
    conn = sqlite3.connect(str(src))
    conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO t VALUES (1, 'alpha')")
    conn.commit()
    conn.close()

    dst = tmp_path / "out.db"
    backup_data.sqlite_online_backup(src, dst)

    check_conn = sqlite3.connect(str(dst))
    assert check_conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert check_conn.execute("SELECT name FROM t WHERE id=1").fetchone() == ("alpha",)
    check_conn.close()


def test_backup_retention(tmp_path):
    backup_data = _load_script_module("backup_data")

    backup_root = tmp_path / "backup"
    for name in ("20260101_0000", "20260102_0000", "20260103_0000", "20260104_0000"):
        (backup_root / name).mkdir(parents=True)

    removed = backup_data.apply_retention(backup_root, retention=2)
    remaining = sorted(p.name for p in backup_root.iterdir())

    assert removed == ["20260101_0000", "20260102_0000"]
    assert remaining == ["20260103_0000", "20260104_0000"]


# ══════════════════════════════════════════════════════════════
# 7. VERSION / version.py
# ══════════════════════════════════════════════════════════════
def test_version_available():
    import version as v

    assert v.__version__, "version.__version__ must not be empty"
    assert v.__version__.startswith("1.0.0"), f"unexpected version string: {v.__version__}"
    banner = v.version_banner()
    assert "WHL Maritime Intelligence System" in banner
    assert v.__version__ in banner

    version_file = ROOT / "VERSION"
    assert version_file.exists()
    assert version_file.read_text(encoding="utf-8").strip() == v.__version__


# ══════════════════════════════════════════════════════════════
# 8. Dashboard runner 設定 — 預設不對外公開
# ══════════════════════════════════════════════════════════════
def test_dashboard_runner_config():
    bat_text = (ROOT / "run_dashboard.bat").read_text(encoding="utf-8")
    assert "127.0.0.1" in bat_text, "run_dashboard.bat must default DASHBOARD_HOST to localhost"
    assert "0.0.0.0" not in bat_text, "run_dashboard.bat must not default to a public bind address"

    app_text = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert 'os.environ.get("DASHBOARD_HOST", "127.0.0.1")' in app_text


# ══════════════════════════════════════════════════════════════
# 9/10. Graceful Degradation — LLM / Teams 停用時不拋例外
# ══════════════════════════════════════════════════════════════
def test_graceful_degradation_llm(monkeypatch):
    import maritime_news as mn

    monkeypatch.setenv("LLM_ENABLED", "false")
    result = mn._run_llm_enhancement({}, datetime.now(timezone.utc))
    assert result == {}, "LLM disabled must degrade to an empty analysis map, not raise"


def test_graceful_degradation_teams(tmp_path, monkeypatch):
    import maritime_news as mn
    from delivery_history import DeliveryHistoryStore
    from delivery_config import load_delivery_rules

    monkeypatch.setenv("TEAMS_ENABLED", "false")
    monkeypatch.delenv("TEAMS_MANAGEMENT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)

    history = DeliveryHistoryStore(str(tmp_path / "delivery.db"))
    try:
        summary = mn._send_teams_for_decisions({}, {}, {}, history, "run_test", load_delivery_rules())
    finally:
        history.close()

    assert summary["enabled"] is False
    assert summary["sent"] == 0
    assert summary["failed"] == 0


# ══════════════════════════════════════════════════════════════
# 11. Event DB 失敗必須是 Fatal（不可安全降級）
# ══════════════════════════════════════════════════════════════
def test_event_db_failure_fatal():
    from event_store import EventStore, EventStoreError

    unwritable_path = "/root/no_permission_dir_for_test/whatever.db"
    with pytest.raises(EventStoreError):
        EventStore(unwritable_path)


# ══════════════════════════════════════════════════════════════
# 12. Final Acceptance Test 本身必須完全離線、可重複執行、回傳 PASS
# ══════════════════════════════════════════════════════════════
def test_final_acceptance_offline():
    fat = _load_script_module("final_acceptance_test")
    exit_code = fat.main()
    assert exit_code == 0, f"final_acceptance_test.main() should return 0 (PASS), got {exit_code}"
    assert all(fat.RESULTS.values()), f"expected all stages PASS, got {fat.RESULTS}"
