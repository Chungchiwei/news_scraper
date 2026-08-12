#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fleet_relevance.py
海事航運新聞監控系統 — Phase 6 §二十六〜三十一 Own Fleet Matching

職責：
  判斷一個 MaritimeEvent 是否「直接」涉及本公司（WAN HAI）船舶。

  比對優先順序（§二十六）：
    1. IMO 精確比對（最可靠，船名再怎麼變動 IMO 不會變）
    2. 正規化後船名精確比對（大小寫/多餘空白正規化，但不做模糊比對——
       "WAN HAI 503" 與 "WAN HAI 505" 是兩艘不同的船，§二十七）
    3. Carrier fallback：新聞只標示 carrier=WAN_HAI、沒有具體船名/IMO
       時，只在事件類型屬於「可能是操作性事故」的範圍內才給予較低的
       關聯分數，且**不**把 own_fleet_involved 設為 True（§三十一：
       Wan Hai 的一般企業公告不代表事故直接涉及本公司船舶）。

  ★ 本模組完全不做模糊字串比對（fuzzy match）、不做 substring 比對——
  這是刻意的設計，避免把 "WAN HAI 503" 誤判成跟 "WAN HAI 5" 或
  "WAN HAI 530" 是同一艘船。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_vessel_name(name: Optional[str]) -> str:
    if not name:
        return ""
    return _WHITESPACE_RE.sub(" ", name.strip().upper())


@dataclass
class FleetMatchResult:
    matched_vessel: Optional[object] = None   # FleetVessel | None
    match_type: Optional[str] = None            # "IMO" / "VESSEL_NAME" / "CARRIER_ONLY" / None
    score: float = 0.0
    own_fleet_involved: bool = False
    reasons: list = field(default_factory=list)


class FleetRelevanceEngine:
    def __init__(self, rules: dict, own_fleet_carrier_key: str = "WAN_HAI"):
        self.weights = rules.get("weights", {})
        self.own_fleet_carrier_key = own_fleet_carrier_key
        self.carrier_context_event_types = set(
            rules.get("own_fleet_carrier_context_event_types", [])
        )

    def match(self, event, vessels: list) -> FleetMatchResult:
        imo = getattr(event, "imo_number", None)
        event_vessel_norm = normalize_vessel_name(getattr(event, "vessel_name", None))

        # ── 1. IMO 精確比對 ──────────────────────────────────────
        if imo:
            for v in vessels:
                if v.imo_number and str(v.imo_number).strip() == str(imo).strip():
                    return FleetMatchResult(
                        matched_vessel=v, match_type="IMO",
                        score=self.weights.get("own_fleet_imo_match", 40),
                        own_fleet_involved=True,
                        reasons=[f"WHL vessel identified in incident (IMO {imo} matches fleet vessel {v.vessel_name})"],
                    )

        # ── 2. 正規化船名精確比對 ─────────────────────────────────
        if event_vessel_norm:
            for v in vessels:
                if normalize_vessel_name(v.vessel_name) == event_vessel_norm:
                    return FleetMatchResult(
                        matched_vessel=v, match_type="VESSEL_NAME",
                        score=self.weights.get("own_fleet_vessel_name_match", 40),
                        own_fleet_involved=True,
                        reasons=[f"WHL vessel identified in incident ({v.vessel_name})"],
                    )

        # ── 3. Carrier fallback（僅限特定 event_type，且不設 own_fleet_involved）──
        carrier = getattr(event, "carrier", None)
        event_type = getattr(event, "event_type", None)
        if carrier == self.own_fleet_carrier_key and event_type in self.carrier_context_event_types:
            return FleetMatchResult(
                matched_vessel=None, match_type="CARRIER_ONLY",
                score=self.weights.get("own_fleet_carrier_only", 25),
                own_fleet_involved=False,
                reasons=["Event attributed to WHL carrier context; no specific vessel identified in source data"],
            )

        return FleetMatchResult()
