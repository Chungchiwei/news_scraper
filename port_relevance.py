#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
port_relevance.py
海事航運新聞監控系統 — Phase 6 §二十二〜二十四、六十九 Port Call Exposure

職責：
  判斷事件地點是否對應到本公司未來的 Port Call，以及該 Port Call
  距現在有多久（決定曝險權重）。

  ★ §六十九：Geographic Match ≠ Port Match。事件如果只有 sea_area /
  shipping_lane（例如「新加坡海峽」），不能自動當成「新加坡港」的
  Port Call 直接比對 —— 那是 route_relevance.py 的職責。這裡只有在
  event.port 明確指向一個港口、或 event 完全沒有被歸類為海域/航道時，
  才會嘗試用 location 欄位猜港口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from port_normalizer import PortNormalizer


@dataclass
class PortMatchResult:
    port_code: Optional[str] = None
    matches: list = field(default_factory=list)   # list[(PortCall, hours_to_exposure)]，已依時間排序
    score: float = 0.0
    closest_eta_hours: Optional[float] = None
    exposure_types: list = field(default_factory=list)


class PortRelevanceEngine:
    def __init__(self, rules: dict, port_normalizer: Optional[PortNormalizer] = None):
        self.weights = rules.get("weights", {})
        self.eta_windows = rules.get("eta_windows_hours", {})
        self.normalizer = port_normalizer or PortNormalizer()

    def resolve_event_port_code(self, event) -> Optional[str]:
        code = self.normalizer.normalize(getattr(event, "port", None))
        if code:
            return code
        # 只有事件沒有被歸類為 sea_area/shipping_lane 時，才嘗試從
        # location 欄位猜港口（避免把「新加坡海峽」誤判成「新加坡港」）。
        if not getattr(event, "sea_area", None) and not getattr(event, "shipping_lane", None):
            return self.normalizer.normalize(getattr(event, "location", None))
        return None

    def assess(self, event, port_calls: list, now: datetime) -> PortMatchResult:
        port_code = self.resolve_event_port_code(event)
        if not port_code:
            return PortMatchResult()

        matches = []
        for pc in port_calls:
            if pc.port_code != port_code or pc.eta_utc is None:
                continue
            hours = (pc.eta_utc - now).total_seconds() / 3600.0
            if hours < 0:
                continue  # 已過去的 ETA 不計入未來曝險
            matches.append((pc, hours))
        matches.sort(key=lambda pair: pair[1])

        if not matches:
            return PortMatchResult(port_code=port_code)

        closest_hours = matches[0][1]
        score = self._score_for_hours(closest_hours)

        return PortMatchResult(
            port_code=port_code, matches=matches, score=score,
            closest_eta_hours=closest_hours, exposure_types=["PORT_CALL"],
        )

    def _score_for_hours(self, hours: float) -> float:
        w = self.weights
        windows = self.eta_windows
        if hours <= windows.get("immediate", 24):
            return w.get("port_call_immediate", 25)
        if hours <= windows.get("high", 48):
            return w.get("port_call_high", 20)
        if hours <= windows.get("moderate", 72):
            return w.get("port_call_moderate", 15)
        if hours <= windows.get("watch", 120):
            return w.get("port_call_watch", 10)
        return w.get("port_call_low", 5)
