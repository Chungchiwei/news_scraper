#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
teams_config.py
海事航運新聞監控系統 — Phase 7 §二十二〜二十四、五十九〜六十 Teams 設定

★ 安全性：TEAMS_ENABLED / webhook URL 一律從環境變數讀取，絕不寫死在
  原始碼、絕不記錄到 log（redacted() 只回傳 True/False）。

★ 預設值：TEAMS_ENABLED 預設 false（§二十三）。停用時 teams_notifier.py
  完全 skip，不是 Error（§二十四）。

★ Management vs System 分離（§五十九〜六十）：兩個獨立的 webhook URL，
  即使實際上兩者都指向同一個 Teams 頻道，程式邏輯上也必須視為兩個
  獨立設定——系統錯誤（RSS 掛了、DB 打不開）絕不能跟主管情報通知共用
  同一個 renderer/webhook 判斷路徑。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from delivery_config import load_delivery_rules


def _bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() not in ("false", "0", "no", "")


@dataclass
class TeamsConfig:
    enabled: bool
    management_webhook_url: str
    system_webhook_url: str
    dashboard_base_url: Optional[str]

    max_retries: int
    retry_wait_seconds: int
    timeout_seconds: int

    max_message_chars: int
    max_events_per_message: int
    consolidate_same_run: bool
    own_fleet_p1_separate: bool
    max_sources_shown: int

    def redacted(self) -> dict:
        """供 log/診斷輸出使用，絕不包含實際 webhook URL（§八延續 Phase 5 慣例）。"""
        return {
            "enabled": self.enabled,
            "management_webhook_set": bool(self.management_webhook_url),
            "system_webhook_set": bool(self.system_webhook_url),
            "dashboard_base_url_set": bool(self.dashboard_base_url),
            "max_retries": self.max_retries,
            "max_message_chars": self.max_message_chars,
        }


def load_teams_config(rules: Optional[dict] = None) -> TeamsConfig:
    rules = rules or load_delivery_rules()
    teams_rules = rules.get("teams", {})

    enabled = _bool_env(teams_rules.get("enabled_env_var", "TEAMS_ENABLED"), False)

    # 相容：若只設定了舊版單一 TEAMS_WEBHOOK_URL，視為 Management webhook。
    management_url = (os.environ.get("TEAMS_MANAGEMENT_WEBHOOK_URL", "").strip()
                       or os.environ.get("TEAMS_WEBHOOK_URL", "").strip())
    system_url = os.environ.get("TEAMS_SYSTEM_WEBHOOK_URL", "").strip()
    dashboard_base_url = os.environ.get("DASHBOARD_BASE_URL", "").strip() or None

    return TeamsConfig(
        enabled=enabled and bool(management_url or system_url),
        management_webhook_url=management_url,
        system_webhook_url=system_url,
        dashboard_base_url=dashboard_base_url,
        max_retries=int(os.environ.get("TEAMS_MAX_RETRIES", teams_rules.get("max_retries", 3))),
        retry_wait_seconds=int(os.environ.get("TEAMS_RETRY_WAIT_SECONDS", teams_rules.get("retry_wait_seconds", 5))),
        timeout_seconds=int(os.environ.get("TEAMS_TIMEOUT_SECONDS", 10)),
        max_message_chars=teams_rules.get("max_message_chars", 2200),
        max_events_per_message=teams_rules.get("max_events_per_message", 5),
        consolidate_same_run=teams_rules.get("consolidate_same_run", True),
        own_fleet_p1_separate=teams_rules.get("own_fleet_p1_separate", True),
        max_sources_shown=teams_rules.get("max_sources_shown", 2),
    )
