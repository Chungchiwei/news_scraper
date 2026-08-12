#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
event_lifecycle.py
海事航運新聞監控系統 — Phase 3 §十八、三十六〜三十九 Event Lifecycle Management

event_status（ACTIVE/MONITORING/RESOLVED/EXPIRED）與 management_priority /
information_status 完全獨立（§十九），只回答「事件現在處於什麼生命週期」。

判斷順序（每次有新文章匹配到既有事件時呼叫 apply_incoming）：
  1. 若目前是 RESOLVED，且新文章文字符合 reopen 關鍵字 → REOPENED（回到 ACTIVE）
  2. 若目前不是 RESOLVED，且新文章文字符合 resolution 關鍵字 → RESOLVED
  3. 否則維持原狀態（是否降級為 MONITORING 由 sweep() 依「多久沒有 Material
     Update」決定，不是每次有新文章就判斷，因為『沒有新文章』本身也是
     MONITORING/EXPIRED 的觸發條件之一，必須對全部既有事件掃描，不能只在
     有新文章匹配時才檢查）。

Expiry（§三十七）：只在明確沒有 RESOLVED、且已經超過該 event_type 的
expiry_days、且最新已知文字沒有『ongoing』字樣時才會標記 EXPIRED。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from models import EventStatus
from event_store import parse_iso


class EventLifecycleManager:

    def __init__(self, memory_rules: dict):
        self.rules = memory_rules
        ed = memory_rules.get("expiry_days", {})
        self.default_expiry_days = ed.get("default", 7)
        self.expiry_by_event_type = ed.get("by_event_type", {})
        self.monitoring_after_days = ed.get("monitoring_after_days", 3)
        self.ongoing_kw_en = [k.lower() for k in ed.get("ongoing_keywords_en", [])]
        self.ongoing_kw_zh = ed.get("ongoing_keywords_zh", [])

        res = memory_rules.get("resolution_keywords", {})
        self.resolution_kw_en = [k.lower() for k in res.get("en", [])]
        self.resolution_kw_zh = res.get("zh", [])

        reopen = memory_rules.get("reopen_keywords", {})
        self.reopen_kw_en = [k.lower() for k in reopen.get("en", [])]
        self.reopen_kw_zh = reopen.get("zh", [])

    def expiry_days_for(self, event_type: Optional[str]) -> int:
        return self.expiry_by_event_type.get(event_type or "", self.default_expiry_days)

    @staticmethod
    def _text_matches(text: str, kw_en: list[str], kw_zh: list[str]) -> bool:
        text_lower = (text or "").lower()
        if any(k in text_lower for k in kw_en):
            return True
        if any(k in (text or "") for k in kw_zh):
            return True
        return False

    def is_resolved_text(self, text: str) -> bool:
        return self._text_matches(text, self.resolution_kw_en, self.resolution_kw_zh)

    def is_reopen_text(self, text: str) -> bool:
        return self._text_matches(text, self.reopen_kw_en, self.reopen_kw_zh)

    def is_ongoing_text(self, text: str) -> bool:
        return self._text_matches(text, self.ongoing_kw_en, self.ongoing_kw_zh)

    # ── 有新文章匹配到既有事件時呼叫 ─────────────────────────────
    def apply_incoming(self, old_status: Optional[str], incoming_text: str
                       ) -> tuple[str, Optional[str]]:
        """回傳 (new_status, change_type)。change_type 為 None 代表生命週期沒變。"""
        old_status = old_status or EventStatus.ACTIVE

        if old_status == EventStatus.RESOLVED and self.is_reopen_text(incoming_text):
            return EventStatus.ACTIVE, "REOPENED"

        if old_status != EventStatus.RESOLVED and self.is_resolved_text(incoming_text):
            return EventStatus.RESOLVED, "RESOLVED"

        if old_status == EventStatus.EXPIRED:
            # 過期事件如果又有新文章匹配到（罕見，例如舊新聞被晚報導），
            # 視為重新活躍，但不算「RESOLVED 後 REOPENED」，只是恢復 ACTIVE。
            return EventStatus.ACTIVE, "REOPENED"

        # 沒有 resolve/reopen 訊號：維持原狀態（ACTIVE 或 MONITORING 都先不變，
        # 是否要從 MONITORING 回到 ACTIVE 由呼叫端依有沒有 Material Update 決定）
        return old_status, None

    # ── 每個 run 對所有既有事件做一次生命週期掃描 ─────────────────
    def sweep(self, store, now: Optional[datetime] = None,
             touched_event_ids: Optional[set] = None) -> list[dict]:
        """
        對這次 run『沒有被匹配到』的既有 ACTIVE/MONITORING 事件做 Expiry 檢查，
        對『有被匹配到但沒有 Material Update』的事件做 MONITORING 降級檢查。
        回傳一份異動清單，由呼叫端負責寫回 DB（維持單一寫入路徑，方便測試）。
        """
        now = now or datetime.now(timezone.utc)
        touched_event_ids = touched_event_ids or set()
        results: list[dict] = []

        for row in store.get_active_or_monitoring():
            event_id = row["event_id"]
            status = row.get("event_status") or EventStatus.ACTIVE
            event_type = row.get("event_type")
            last_seen = parse_iso(row.get("last_seen_utc"))
            last_material = parse_iso(row.get("last_material_update_utc")) or last_seen
            headline = row.get("headline") or ""

            if last_seen is None:
                continue

            age_days = (now - last_seen).total_seconds() / 86400.0
            expiry_days = self.expiry_days_for(event_type)

            if age_days > expiry_days and not self.is_ongoing_text(headline):
                results.append({
                    "event_id": event_id, "old_status": status,
                    "new_status": EventStatus.EXPIRED, "change_type": "EXPIRED",
                })
                continue

            if event_id in touched_event_ids:
                continue   # 這次有更新，MONITORING 判斷交給下次 sweep

            if status == EventStatus.ACTIVE and last_material is not None:
                material_age_days = (now - last_material).total_seconds() / 86400.0
                if material_age_days > self.monitoring_after_days:
                    results.append({
                        "event_id": event_id, "old_status": status,
                        "new_status": EventStatus.MONITORING, "change_type": None,
                    })

        return results
