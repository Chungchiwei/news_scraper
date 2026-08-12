#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
delivery_orchestrator.py
海事航運新聞監控系統 — Phase 7 §一〜十三、十九〜二十一、八十〜八十一
Delivery Orchestrator 主體

職責：
  對每一個事件，同時讀取兩條獨立的軸：

    Event Axis        （Phase 3 NotificationState：NEW/MATERIAL_UPDATE/
                         MINOR_UPDATE/UNCHANGED/RESOLVED_UPDATE）
    Operational Axis   （Phase 6 OperationalNotificationState：
                         EXPOSURE_NEW/ESCALATED/UNCHANGED/REDUCED/
                         CLEARED/UNAVAILABLE）

  決定 Delivery Urgency（IMMEDIATE/PROMPT/BRIEF/DASHBOARD_ONLY/
  SUPPRESSED）與要送去哪些 Channel（EMAIL/TEAMS/DASHBOARD），輸出
  DeliveryDecision（見 delivery_models.py）。

  ★ 核心原則（Phase 7 §一〜二）：
    Delivery ≠ Risk。這裡完全不重新計算/覆寫 event.management_priority /
    event.confidence_level / operational_relevance.relevance_level ——
    只讀取這些既有欄位做路由決策，輸出獨立的 DeliveryDecision 物件。

  ★ Dual-Axis Trigger（§十一〜十二，Phase 7 最重要案例）：
    事件本身可以是 UNCHANGED（Phase 3 認為沒有新資訊），但只要
    Operational Exposure 獨立判定為 ESCALATED，Delivery 絕不能是
    SUPPRESSED——這是 Phase 6 Three-Run Simulation 證明的
    「EVENT UNCHANGED ≠ OPERATIONAL EXPOSURE UNCHANGED」在 Phase 7
    的延伸應用：「EVENT UNCHANGED ≠ NOTHING TO DELIVER」。

  ★ Own Fleet Override（§十三）：own_fleet_involved=True 時，P1/P2
    事件的 Urgency 最低不得低於設定的 floor，但絕不假裝更高的
    confidence——urgency 提高不等於 information_status 被覆寫成
    CONFIRMED，Teams/Email renderer 仍然會誠實顯示 EARLY SIGNAL。

  ★ Cooldown（§八十〜八十一）：只作用在 TEAMS channel（避免 Exposure
    在門檻附近反覆跳動造成 Teams spam），且只壓抑「非真正新事實」的
    重複通知——event.notification_state 屬於 MATERIAL_UPDATE /
    RESOLVED_UPDATE 這種「真的有新事實」時一律 bypass cooldown。
    Cooldown 從不影響 EMAIL / DASHBOARD channel。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from delivery_models import (
    DeliveryDecision, DeliveryUrgency, DeliveryChannel, EmailMode, TeamsMode,
)
from delivery_history import build_dedup_key

logger = logging.getLogger(__name__)

_RANK = DeliveryUrgency.RANK


def _more_urgent(a: str, b: str) -> str:
    """回傳兩個 urgency 之中『比較急』的那一個（rank 數字較小者）。"""
    return a if _RANK.get(a, 9) <= _RANK.get(b, 9) else b


class DeliveryOrchestrator:
    def __init__(self, rules: dict, history_store):
        self.rules = rules
        self.history = history_store
        self.diagnostics = {
            "events_evaluated": 0,
            "IMMEDIATE": 0, "PROMPT": 0, "BRIEF": 0,
            "DASHBOARD_ONLY": 0, "SUPPRESSED": 0,
            "teams_cooldown_suppressed": 0,
        }

    # ══════════════════════════════════════════════════════════
    # 對外入口
    # ══════════════════════════════════════════════════════════
    def decide(self, event, operational_relevance=None,
               operational_notification_state: Optional[str] = None,
               now: Optional[datetime] = None, run_id: Optional[str] = None) -> DeliveryDecision:
        now = now or datetime.now(timezone.utc)

        urgency, reason = self._classify_base_urgency(event)
        urgency, reason = self._apply_operational_axis(
            urgency, reason, operational_relevance, operational_notification_state
        )
        urgency, reason = self._apply_own_fleet_floor(urgency, reason, operational_relevance, event)

        channels = self._channels_for_urgency(urgency)
        email_mode = self._email_mode_for(urgency)
        teams_mode = self._teams_mode_for(urgency, event, channels)

        dedup_key = build_dedup_key(event.event_id, getattr(event, "version", 1),
                                     operational_notification_state)

        cooldown_until = None
        teams_suppressed_by_cooldown = False
        if DeliveryChannel.TEAMS in channels:
            channels, teams_suppressed_by_cooldown, cooldown_until = self._apply_cooldown(
                event, channels, now
            )
            if teams_suppressed_by_cooldown:
                teams_mode = TeamsMode.NONE
                reason = f"{reason}; Teams suppressed by cooldown"

        self._record_diagnostics(urgency, teams_suppressed_by_cooldown)

        return DeliveryDecision(
            event_id=event.event_id,
            delivery_reason=reason,
            urgency=urgency,
            channels=channels,
            email_mode=email_mode,
            teams_mode=teams_mode,
            dashboard_visibility=True,   # §七十九：Dashboard 永遠可見，與 Push 決策分離
            dedup_key=dedup_key,
            cooldown_until=cooldown_until,
            teams_suppressed_by_cooldown=teams_suppressed_by_cooldown,
            created_at=now,
            event_notification_state=getattr(event, "notification_state", None),
            operational_notification_state=operational_notification_state,
            management_priority=getattr(event, "management_priority", None),
            relevance_level=(operational_relevance.relevance_level if operational_relevance else None),
        )

    # ══════════════════════════════════════════════════════════
    # Event Axis — 基礎分級（§六〜十）
    # ══════════════════════════════════════════════════════════
    def _classify_base_urgency(self, event) -> tuple[str, str]:
        priority = getattr(event, "management_priority", None)
        notif = getattr(event, "notification_state", None)
        confidence = getattr(event, "confidence_level", None)

        imm = self.rules.get("immediate", {})
        if (priority in imm.get("priorities", []) and notif in imm.get("notification_states", [])
                and confidence in imm.get("confidence_levels", [])):
            return DeliveryUrgency.IMMEDIATE, f"{priority} {notif} with {confidence} confidence"

        pr = self.rules.get("prompt", {})
        if priority in pr.get("priorities", []) and notif in pr.get("notification_states", []):
            return DeliveryUrgency.PROMPT, f"{priority} {notif}"

        # P1 NEW/MATERIAL_UPDATE 但 Confidence 未達 immediate 門檻
        # （§二範例：P1 + LOW Confidence + EARLY_SIGNAL 不一定要 Teams
        # Immediate Alert，但仍應保留在 PROMPT——不能因為信心不足就
        # SUPPRESSED，主管仍然需要知道有一個未確認的 P1 事件）。
        if priority == "P1" and notif in imm.get("notification_states", []):
            return DeliveryUrgency.PROMPT, f"{priority} {notif} with {confidence} confidence (below immediate threshold)"

        br = self.rules.get("brief", {})
        if priority in br.get("priorities", []) and notif in br.get("notification_states", []):
            return DeliveryUrgency.BRIEF, f"{priority} {notif}"

        if notif == "UNCHANGED":
            return DeliveryUrgency.SUPPRESSED, "event unchanged, no operational escalation"

        if notif == "MINOR_UPDATE":
            return DeliveryUrgency.DASHBOARD_ONLY, "minor update only"

        do = self.rules.get("dashboard_only", {})
        if priority in do.get("priorities", []):
            return DeliveryUrgency.DASHBOARD_ONLY, f"{priority} reference-level event"

        return DeliveryUrgency.DASHBOARD_ONLY, "default: dashboard reference only"

    # ══════════════════════════════════════════════════════════
    # Operational Axis — Dual-Axis Trigger（§十一〜十二，最重要案例）
    # ══════════════════════════════════════════════════════════
    def _apply_operational_axis(self, urgency: str, reason: str,
                                 operational_relevance, operational_notification_state) -> tuple[str, str]:
        if operational_relevance is None or operational_notification_state is None:
            return urgency, reason
        if operational_relevance.relevance_status == "UNAVAILABLE":
            return urgency, reason   # 資料不可用，不能拿來升高或降低 urgency

        if operational_notification_state == "EXPOSURE_ESCALATED":
            floor = self.rules.get("exposure_escalated_min_urgency", "PROMPT")
            merged = _more_urgent(urgency, floor)
            if merged != urgency:
                return merged, "WHL operational exposure escalated"
            return urgency, reason

        if operational_notification_state == "EXPOSURE_CLEARED":
            prev_level_relevant = (operational_relevance.relevance_level is None
                                    or True)  # CLEARED 時 current level 已是 NONE，用『曾經是 DIRECT/HIGH』判斷交給呼叫端傳入 reasons
            floor = self.rules.get("exposure_cleared_min_urgency", "BRIEF")
            # 只有「先前」是 DIRECT/HIGH 才值得額外提升（呼叫端已經在
            # operational_notification_state=EXPOSURE_CLEARED 這個狀態
            # 本身就代表『曾經有曝險、現在沒了』，故直接套用 floor）。
            merged = _more_urgent(urgency, floor)
            if merged != urgency:
                return merged, "WHL operational exposure cleared (previously elevated)"
            return urgency, reason

        return urgency, reason

    # ══════════════════════════════════════════════════════════
    # Own Fleet Override（§十三）
    # ══════════════════════════════════════════════════════════
    def _apply_own_fleet_floor(self, urgency: str, reason: str,
                                operational_relevance, event) -> tuple[str, str]:
        if operational_relevance is None or not getattr(operational_relevance, "own_fleet_involved", False):
            return urgency, reason
        priority = getattr(event, "management_priority", None)
        floor_map = self.rules.get("own_fleet_floor", {})
        floor = floor_map.get(priority)
        if not floor:
            return urgency, reason
        merged = _more_urgent(urgency, floor)
        if merged != urgency:
            return merged, f"Own fleet vessel involved ({priority})"
        return urgency, reason

    # ══════════════════════════════════════════════════════════
    # Channel / Mode 映射
    # ══════════════════════════════════════════════════════════
    def _channels_for_urgency(self, urgency: str) -> list:
        if urgency in (DeliveryUrgency.IMMEDIATE, DeliveryUrgency.PROMPT):
            return [DeliveryChannel.EMAIL, DeliveryChannel.TEAMS, DeliveryChannel.DASHBOARD]
        if urgency == DeliveryUrgency.BRIEF:
            return [DeliveryChannel.EMAIL, DeliveryChannel.DASHBOARD]
        # DASHBOARD_ONLY / SUPPRESSED：Dashboard 永遠可見（§七十九）
        return [DeliveryChannel.DASHBOARD]

    def _email_mode_for(self, urgency: str) -> str:
        alert_urgencies = self.rules.get("email", {}).get("alert_urgencies", ["IMMEDIATE"])
        brief_urgencies = self.rules.get("email", {}).get("brief_urgencies", ["IMMEDIATE", "PROMPT", "BRIEF"])
        if urgency in alert_urgencies:
            return EmailMode.ALERT
        if urgency in brief_urgencies:
            return EmailMode.DAILY_BRIEF
        return EmailMode.NONE

    def _teams_mode_for(self, urgency: str, event, channels: list) -> str:
        if DeliveryChannel.TEAMS not in channels:
            return TeamsMode.NONE
        # RESOLVED_UPDATE 一律用 Resolved 模板，即使 urgency 只有 PROMPT
        # （§八十二：resolution 值得專屬措辭，不是一般 PROMPT 通知）。
        if getattr(event, "notification_state", None) == "RESOLVED_UPDATE":
            return TeamsMode.RESOLVED
        if urgency == DeliveryUrgency.IMMEDIATE:
            return TeamsMode.IMMEDIATE
        if urgency == DeliveryUrgency.PROMPT:
            return TeamsMode.PROMPT
        return TeamsMode.NONE

    # ══════════════════════════════════════════════════════════
    # Cooldown（§八十〜八十一）—— 只作用於 TEAMS channel
    # ══════════════════════════════════════════════════════════
    def _apply_cooldown(self, event, channels: list, now: datetime) -> tuple[list, bool, Optional[datetime]]:
        bypass_states = self.rules.get("cooldown_bypass_notification_states", [])
        notif = getattr(event, "notification_state", None)

        cooldown_minutes_map = self.rules.get("cooldown_minutes", {})
        priority = getattr(event, "management_priority", None)
        minutes = cooldown_minutes_map.get(priority, cooldown_minutes_map.get("default", 240))
        cooldown_until = None

        if notif in bypass_states:
            # 真正的新事實（Material Update / Resolved）一律 bypass cooldown（§八十）。
            return channels, False, None

        last_sent = self.history.last_sent_at(event.event_id, DeliveryChannel.TEAMS)
        if last_sent is not None:
            cooldown_until = last_sent + timedelta(minutes=minutes)
            if now < cooldown_until:
                remaining_channels = [c for c in channels if c != DeliveryChannel.TEAMS]
                return remaining_channels, True, cooldown_until

        return channels, False, cooldown_until

    def _record_diagnostics(self, urgency: str, teams_suppressed: bool) -> None:
        self.diagnostics["events_evaluated"] += 1
        self.diagnostics[urgency] = self.diagnostics.get(urgency, 0) + 1
        if teams_suppressed:
            self.diagnostics["teams_cooldown_suppressed"] += 1

    def diagnostics_report(self) -> str:
        d = self.diagnostics
        return (
            "DELIVERY ORCHESTRATOR\n"
            f"  Events evaluated: {d['events_evaluated']}\n"
            f"  IMMEDIATE: {d['IMMEDIATE']}  PROMPT: {d['PROMPT']}  BRIEF: {d['BRIEF']}  "
            f"DASHBOARD_ONLY: {d['DASHBOARD_ONLY']}  SUPPRESSED: {d['SUPPRESSED']}\n"
            f"  Teams suppressed by cooldown: {d['teams_cooldown_suppressed']}"
        )
