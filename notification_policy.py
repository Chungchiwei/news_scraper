#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notification_policy.py
海事航運新聞監控系統 — Phase 3 §三十三〜三十五 Notification Decision

輸入：notification_state / management_priority / confidence_level /
     information_status / event_status
輸出：should_notify（bool）+ notification_reason（人類可讀）

★ 本階段不拆 Immediate Alert vs Daily Brief 兩套 Email template（§三十五），
  只產生 should_notify / notification_reason / eligible_events，
  Phase 4 再決定怎麼呈現。
"""

from __future__ import annotations

from typing import Optional

from models import NotificationState


class NotificationPolicy:

    def __init__(self, memory_rules: dict):
        self.rules = memory_rules.get("notification", {})

    def _lookup(self, notification_state: str, priority: Optional[str]) -> bool:
        key = notification_state.lower()
        table = self.rules.get(key, {})
        return bool(table.get(priority or "P4", False))

    def decide(self, notification_state: str, management_priority: Optional[str],
              confidence_level: Optional[str] = None,
              information_status: Optional[str] = None,
              event_status: Optional[str] = None,
              change_reasons: Optional[list[str]] = None) -> tuple[bool, str]:
        """回傳 (should_notify, notification_reason)。"""
        priority = management_priority or "P4"
        should_notify = self._lookup(notification_state, priority)

        reasons = change_reasons or []

        if notification_state == NotificationState.NEW:
            reason = f"New {priority} event detected"
            if not should_notify:
                reason += " (reserved for daily brief)"
            return should_notify, reason

        if notification_state == NotificationState.MATERIAL_UPDATE:
            detail = "; ".join(reasons) if reasons else "Material change detected"
            reason = f"Material update on {priority} event: {detail}"
            if not should_notify:
                reason += " (reserved for daily brief)"
            return should_notify, reason

        if notification_state == NotificationState.RESOLVED_UPDATE:
            reason = f"Event resolved ({priority}): " + (
                "; ".join(reasons) if reasons else "resolution confirmed"
            )
            if not should_notify:
                reason += " (reserved for daily brief)"
            return should_notify, reason

        if notification_state == NotificationState.MINOR_UPDATE:
            return False, "Minor update only, no management-relevant change"

        return False, "No change since last run"
