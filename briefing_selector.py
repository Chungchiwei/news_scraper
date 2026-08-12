#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
briefing_selector.py
海事航運新聞監控系統 — Phase 4 §六十一〜六十三 Briefing Selector

職責：
  輸入 Phase 3 的 all_current_events（這次 run 比對/合併/評分後的完整
  事件列表，不是只有 should_notify=True 的那個子集——Daily Brief 需要
  P3 Industry Watch，Alert 才只用 P1），輸出五個分桶：

    immediate   — P1 且 NEW/MATERIAL_UPDATE
    watch       — P2 且 NEW/MATERIAL_UPDATE
    industry    — P3 且 NEW/MATERIAL_UPDATE 且具 fleet/operational/
                  regulatory/重大 competitor relevance
    resolved    — 這次 run 剛轉為 RESOLVED_UPDATE 的事件（不含歷史舊事件）
    suppressed  — UNCHANGED / MINOR_UPDATE / P4 / 不具產業意義的 P3

  Selection Logic 本身不做任何風險判斷（P1/P2/P3 由 Phase 2/2.1 Risk
  Scorer 決定，NEW/MATERIAL_UPDATE 由 Phase 3 Notification Policy 決定），
  這裡只負責「這些已經算好的標籤要進哪個 Email 區塊」。
"""

from __future__ import annotations

from typing import Optional

from models import ManagementPriority, NotificationState
from risk_config import load_risk_rules
from email_config import load_email_rules


class BriefingSelector:

    def __init__(self, email_rules: Optional[dict] = None,
                 risk_rules: Optional[dict] = None):
        self.rules = email_rules or load_email_rules()
        self.risk_rules = risk_rules or load_risk_rules()

        self._own_fleet_keys = {
            c["key"] for c in self.risk_rules.get("major_carriers", [])
            if c.get("is_own_fleet")
        }

        db_cfg = self.rules.get("daily_brief", {})
        self.max_p1 = db_cfg.get("max_p1", 5)
        self.max_p2 = db_cfg.get("max_p2", 8)
        self.max_p3 = db_cfg.get("max_p3", 5)
        self.include_p4 = db_cfg.get("include_p4", False)
        self.include_resolved = db_cfg.get("include_resolved", True)
        self.max_resolved = db_cfg.get("max_resolved", 5)

        iw_cfg = self.rules.get("industry_watch", {})
        self.fleet_relevance_threshold = iw_cfg.get("fleet_relevance_threshold", 15)
        self.always_include_types = set(iw_cfg.get("always_include_event_types", []))
        self._competitor_kw_en = [k.lower() for k in iw_cfg.get("competitor_keywords_en", [])]
        self._competitor_kw_zh = iw_cfg.get("competitor_keywords_zh", [])

    # ── Own Fleet（§五十九、二十）───────────────────────────────
    def is_own_fleet(self, event) -> bool:
        return bool(event.carrier and event.carrier in self._own_fleet_keys)

    # ── P3 Industry Watch 篩選（§三十九）─────────────────────────
    def _is_industry_relevant(self, event) -> bool:
        if (event.fleet_relevance_score or 0) >= self.fleet_relevance_threshold:
            return True
        if event.event_type in self.always_include_types:
            return True
        primary_summary = event.primary_article.summary if event.primary_article else ""
        raw_text = f"{event.headline} {primary_summary}"
        text_lower = raw_text.lower()
        if any(kw in text_lower for kw in self._competitor_kw_en):
            return True
        if any(kw in raw_text for kw in self._competitor_kw_zh):
            return True
        return False

    # ── 排序（§十八、五十九：Priority → Own Fleet → Score）────────
    def _sort_key(self, event):
        rank = ManagementPriority.RANK.get(event.management_priority, 99)
        own_fleet_rank = 0 if self.is_own_fleet(event) else 1
        score = -(event.management_score or 0.0)
        return (rank, own_fleet_rank, score)

    # ── 主要分類邏輯 ─────────────────────────────────────────────
    def select(self, events: list) -> dict:
        immediate: list = []
        watch: list = []
        industry: list = []
        resolved: list = []
        suppressed: list = []

        for e in events:
            state = e.notification_state
            priority = e.management_priority

            if state == NotificationState.RESOLVED_UPDATE:
                if self.include_resolved and priority in (
                    ManagementPriority.P1, ManagementPriority.P2, ManagementPriority.P3,
                ):
                    resolved.append(e)
                else:
                    suppressed.append(e)
                continue

            if state not in (NotificationState.NEW, NotificationState.MATERIAL_UPDATE):
                suppressed.append(e)   # UNCHANGED / MINOR_UPDATE（§二十一：不進通知）
                continue

            if priority == ManagementPriority.P1:
                immediate.append(e)
            elif priority == ManagementPriority.P2:
                watch.append(e)
            elif priority == ManagementPriority.P3:
                if self._is_industry_relevant(e):
                    industry.append(e)
                else:
                    suppressed.append(e)
            else:   # P4
                if self.include_p4:
                    industry.append(e)
                else:
                    suppressed.append(e)

        for bucket in (immediate, watch, industry, resolved):
            bucket.sort(key=self._sort_key)

        overflow: dict[str, int] = {}

        def _cap(bucket: list, limit: int, key: str) -> list:
            if len(bucket) > limit:
                overflow[key] = len(bucket) - limit
                return bucket[:limit]
            return bucket

        immediate = _cap(immediate, self.max_p1, "P1")
        watch = _cap(watch, self.max_p2, "P2")
        industry = _cap(industry, self.max_p3, "P3")
        resolved = _cap(resolved, self.max_resolved, "RESOLVED")

        return {
            "immediate": immediate,
            "watch": watch,
            "industry": industry,
            "resolved": resolved,
            "suppressed": suppressed,
            "overflow": overflow,
        }
