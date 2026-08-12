#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/health_check.py
WHL Maritime Intelligence System — Phase 8 §二十一〜二十三 System Health Check

用途：不執行任何爬蟲／不寄送任何 Email／不呼叫任何 Teams Webhook／不呼叫
任何 LLM API，純粹檢查「這台機器上的環境、設定、資料庫、模組是否就緒」，
供本機驗證、IT 部署後驗證、或排程前手動確認使用。

★ 絕對不做的事（刻意，見 §二十二）：
  - 不會真的寄出 Email（只檢查環境變數是否齊全）。
  - 不會真的送出 Teams 訊息（只檢查是否啟用/webhook 是否設定）。
  - 不會真的呼叫 LLM API（只檢查是否啟用/金鑰是否設定）。
  - 不會修改任何既有資料庫內容（EventStore 等的「開啟」動作本身即為
    冪等的 CREATE TABLE IF NOT EXISTS，與 maritime_news.py 正常啟動
    時的行為一致，不是本工具額外造成的副作用）。

用法：
    python scripts/health_check.py
Exit code：0 = OVERALL READY／DEGRADED，1 = OVERALL NOT READY
（讓排程或 CI 可以用 exit code 判斷是否要繼續）。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# ★ 必須在 import 任何專案模組之前就設定好 root logger（WARNING 以上）。
# maritime_news.py 等模組的 loader 函式會在 import 當下印出 INFO 訊息
# （例如「✅ 已載入關鍵字設定檔」），這對一般使用者跑健康檢查來說是雜訊
# ——這裡的目標是乾淨的 PASS/WARNING/FAIL/DISABLED 報表，不是除錯輸出。
# Python 的 logging.basicConfig() 若 root logger 已有 handler 則是
# no-op，所以先呼叫一次即可讓後續模組內的 basicConfig() 呼叫失效。
logging.basicConfig(level=logging.WARNING)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() not in ("false", "0", "no", "")


class Report:
    def __init__(self):
        self.rows: list[tuple[str, str]] = []
        self.fatal_count = 0
        self.warning_count = 0

    def add(self, label: str, status: str, fatal: bool = False, warning: bool = False):
        self.rows.append((label, status))
        if fatal:
            self.fatal_count += 1
        if warning:
            self.warning_count += 1


def check_python(report: Report):
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 10):
        report.add("Python", "PASS")
    else:
        report.add("Python", f"WARNING (running {major}.{minor}, recommend 3.10+)", warning=True)


def check_configuration(report: Report):
    """
    載入所有規則設定檔（不含 .env 機密值），確認格式正確、可被解析。
    對應 maritime_news.py 啟動時實際會用到的 loader（§三十二：Critical
    Config 若壞掉必須讓程式無法啟動，這裡先幫使用者提前抓出來）。
    """
    try:
        from risk_config import load_risk_rules
        from memory_config import load_memory_rules
        from email_config import load_email_rules
        from operational_config import load_operational_rules
        from delivery_config import load_delivery_rules
        from llm_config import load_llm_rules
        from maritime_news import load_keywords_config

        load_risk_rules()
        load_memory_rules()
        load_email_rules()
        load_operational_rules()
        load_delivery_rules()
        load_llm_rules()
        load_keywords_config()
        report.add("Configuration", "PASS")
    except Exception as e:
        report.add("Configuration", f"FAIL ({e})", fatal=True)


def check_event_store(report: Report):
    """Event DB（Phase 3）——唯一「開不了就是 Fatal」的資料庫（§三十二〜三十三）。"""
    try:
        from event_store import EventStore
        from memory_pipeline import resolve_db_path
        store = EventStore(resolve_db_path())
        store.close()
        report.add("Event Store", "PASS")
    except Exception as e:
        report.add("Event Store", f"FAIL ({e})", fatal=True)


def check_operational_store(report: Report):
    """Operational History（Phase 6）——失敗會安全退化成 NullStore，非 Fatal。"""
    try:
        from operational_history import open_operational_history, DEFAULT_OPERATIONAL_HISTORY_DB_PATH
        store = open_operational_history(
            os.environ.get("MARITIME_OPERATIONAL_HISTORY_DB_PATH", DEFAULT_OPERATIONAL_HISTORY_DB_PATH))
        store.close()
        report.add("Operational Store", "PASS")
    except Exception as e:
        report.add("Operational Store", f"WARNING ({e}) — degrades to unavailable exposure", warning=True)


def check_delivery_store(report: Report):
    """Delivery History（Phase 7）——失敗會安全退化成 NullStore，非 Fatal。"""
    try:
        from delivery_history import open_delivery_history, DEFAULT_DELIVERY_HISTORY_DB_PATH
        store = open_delivery_history(
            os.environ.get("MARITIME_DELIVERY_HISTORY_DB_PATH", DEFAULT_DELIVERY_HISTORY_DB_PATH))
        store.close()
        report.add("Delivery Store", "PASS")
    except Exception as e:
        report.add("Delivery Store", f"WARNING ({e})", warning=True)


def check_dashboard(report: Report):
    """只確認 dashboard/app.py 可以被 import（不啟動 uvicorn server）。"""
    try:
        import dashboard.app  # noqa: F401
        report.add("Dashboard", "PASS")
    except Exception as e:
        report.add("Dashboard", f"FAIL ({e})", fatal=True)


def check_email(report: Report):
    """只檢查必要環境變數是否齊全，絕不實際連線 SMTP、絕不寄信。"""
    mail_user = os.environ.get("MAIL_USER", "")
    mail_pass = os.environ.get("MAIL_PASSWORD", "")
    target = os.environ.get("TARGET_EMAIL", "")
    missing = [n for n, v in (("MAIL_USER", mail_user), ("MAIL_PASSWORD", mail_pass),
                               ("TARGET_EMAIL", target)) if not v]
    if not missing:
        report.add("Email", "CONFIGURED")
    else:
        report.add("Email", f"NOT CONFIGURED (missing: {', '.join(missing)})", fatal=True)


def check_teams(report: Report):
    """只檢查設定，絕不實際呼叫 webhook。"""
    try:
        from teams_config import load_teams_config
        cfg = load_teams_config()
        if not _bool_env("TEAMS_ENABLED", False):
            report.add("Teams", "DISABLED")
        elif cfg.enabled:
            report.add("Teams", "CONFIGURED")
        else:
            report.add("Teams", "WARNING (enabled but no webhook URL set)", warning=True)
    except Exception as e:
        report.add("Teams", f"WARNING ({e})", warning=True)


def check_llm(report: Report):
    """只檢查設定，絕不實際呼叫 LLM API。"""
    try:
        from llm_config import load_llm_config
        cfg = load_llm_config()
        if not _bool_env("LLM_ENABLED", False):
            report.add("LLM", "DISABLED")
        elif cfg.enabled and (cfg.anthropic_api_key or cfg.openai_api_key):
            report.add("LLM", "CONFIGURED")
        else:
            report.add("LLM", "WARNING (enabled but no API key set — falls back to rule-based)", warning=True)
    except Exception as e:
        report.add("LLM", f"WARNING ({e})", warning=True)


def check_operational_providers(report: Report):
    """Fleet / Schedule / Route Provider —— 只確認本機 config JSON 可載入。"""
    try:
        from fleet_provider import ConfigFleetProvider
        vessels = ConfigFleetProvider().get_vessels()
        report.add("Fleet Provider", f"READY ({len(vessels)} vessels)")
    except Exception as e:
        report.add("Fleet Provider", f"WARNING ({e}) — exposure will show UNAVAILABLE", warning=True)

    try:
        from schedule_provider import ConfigScheduleProvider
        calls = ConfigScheduleProvider().get_port_calls()
        report.add("Schedule Provider", f"READY ({len(calls)} port calls)")
    except Exception as e:
        report.add("Schedule Provider", f"WARNING ({e}) — exposure will show UNAVAILABLE", warning=True)

    try:
        from route_provider import ConfigRouteProvider
        services = ConfigRouteProvider().get_services()
        report.add("Route Provider", f"READY ({len(services)} services)")
    except Exception as e:
        report.add("Route Provider", f"WARNING ({e}) — exposure will show UNAVAILABLE", warning=True)


def main() -> int:
    report = Report()

    check_python(report)
    check_configuration(report)
    check_event_store(report)
    check_operational_store(report)
    check_delivery_store(report)
    check_dashboard(report)
    check_email(report)
    check_teams(report)
    check_llm(report)
    check_operational_providers(report)

    print("WHL Maritime Intelligence")
    print("System Health Check")
    for label, status in report.rows:
        print(f"{label:<24}{status}")

    print("OVERALL:")
    if report.fatal_count > 0:
        print("NOT READY")
        overall_ok = False
    elif report.warning_count > 0:
        print("DEGRADED")
        overall_ok = True
    else:
        print("READY")
        overall_ok = True

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
