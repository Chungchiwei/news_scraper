#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
delivery_models.py
海事航運新聞監控系統 — Phase 7 §四〜五、十五 Delivery Decision Model

職責：
  定義 Delivery Orchestrator 的輸出結構。跟 Phase 6 operational_models.py
  同樣原則：只用標準庫 dataclasses，命名空間類別取代 enum.Enum（避免
  JSON 序列化麻煩）。

  ★ Delivery ≠ Risk（Phase 7 §二）：這裡定義的所有結構只回答「這件事
  要不要送、送去哪裡、多快送」，不重新判斷嚴重度——嚴重度已經由
  Phase 1-5（Priority/Confidence）與 Phase 6（Operational Relevance）
  決定好了，Delivery Orchestrator 只讀取這些既有欄位做路由決策。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class DeliveryUrgency:
    """
    Phase 7 §五：Delivery Urgency —— 五個等級，rank 數字越小越急
    （沿用 Phase 6 RelevanceLevel.RANK 的設計語言）。
    """
    IMMEDIATE       = "IMMEDIATE"
    PROMPT          = "PROMPT"
    BRIEF           = "BRIEF"
    DASHBOARD_ONLY  = "DASHBOARD_ONLY"
    SUPPRESSED      = "SUPPRESSED"

    ORDER = (IMMEDIATE, PROMPT, BRIEF, DASHBOARD_ONLY, SUPPRESSED)
    RANK  = {IMMEDIATE: 0, PROMPT: 1, BRIEF: 2, DASHBOARD_ONLY: 3, SUPPRESSED: 4}


class DeliveryChannel:
    """Phase 7 §十五：一個 Event 可以同時對應多個 Channel。"""
    EMAIL     = "EMAIL"
    TEAMS     = "TEAMS"
    DASHBOARD = "DASHBOARD"

    ALL = (EMAIL, TEAMS, DASHBOARD)


class EmailMode:
    """
    Delivery Orchestrator 對 Email channel 的『建議』模式——不取代、
    不阻擋 Phase 4 BriefingSelector 既有的 P1-P4 分桶邏輯（Email Daily
    Brief 內容仍然 100% 由 Phase 3/4 既有規則決定，§一百零九
    「EMAIL IS FOR CONTEXT」）。這裡只額外標記：urgency=IMMEDIATE 的
    事件應該觸發一封『獨立 Alert Email』（沿用 Phase 4 既有的
    build_alert_view_model() 路徑），而不是等到下一次 Daily Brief。
    """
    ALERT       = "ALERT"        # 觸發獨立 Alert Email（沿用 Phase 4 Alert 路徑）
    DAILY_BRIEF = "DAILY_BRIEF"  # 隨下一次 Daily Brief 一併寄出（Phase 4 既有邏輯已涵蓋）
    NONE        = "NONE"         # 本次不特別標記（Dashboard Only / Suppressed）


class TeamsMode:
    """驅動 teams_renderer.py 要用哪一種訊息模板。"""
    IMMEDIATE = "IMMEDIATE"
    PROMPT    = "PROMPT"
    RESOLVED  = "RESOLVED"
    NONE      = "NONE"


@dataclass
class DeliveryDecision:
    """
    Phase 7 §四：單一事件的 Delivery Decision——這件事要不要送、送去
    哪裡、多快送。完全獨立於 event.management_priority / event.
    confidence_level / operational_relevance.relevance_level 本身的定義
    （只讀取，不改寫）。
    """
    event_id: str

    delivery_reason: str
    urgency: str                         # DeliveryUrgency

    channels: list = field(default_factory=list)   # list[DeliveryChannel]，含 DASHBOARD

    email_mode: str = EmailMode.NONE
    teams_mode: str = TeamsMode.NONE
    dashboard_visibility: bool = True    # §七十九：Dashboard 永遠顯示目前 Active 事件，與 Push 決策分離

    dedup_key: Optional[str] = None
    cooldown_until: Optional[datetime] = None
    teams_suppressed_by_cooldown: bool = False   # 記錄「原本該送但被 cooldown 擋下」，供診斷/測試使用

    created_at: Optional[datetime] = None

    # ── 決策當下讀取的兩條軸（唯讀快照，供 History / Dashboard 追溯）──
    event_notification_state: Optional[str] = None        # Phase 3 NotificationState
    operational_notification_state: Optional[str] = None  # Phase 6 OperationalNotificationState
    management_priority: Optional[str] = None              # Phase 1-5 ManagementPriority
    relevance_level: Optional[str] = None                   # Phase 6 RelevanceLevel

    def has_channel(self, channel: str) -> bool:
        return channel in self.channels
