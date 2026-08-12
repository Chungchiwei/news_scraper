#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
route_relevance.py
海事航運新聞監控系統 — Phase 6 §二十一 Route / Shipping Lane Exposure

職責：
  判斷事件所在海域／航道（sea_area / shipping_lane，沿用 Phase 2 的
  正規化區域代碼，例如 RED_SEA、SINGAPORE_STRAIT）是否落在某條本公司
  Service 的常態航線（major_shipping_lanes）上。

  ★ 只做「事件海域代碼 是否在 Service 的 major_shipping_lanes 清單裡」
  這種明確比對；如果 config 沒有標示某條 Service 目前已改道
  （§二十一：rerouted via Cape of Good Hope），就不擅自調降 Exposure——
  沒有資料就是沒有資料，不能用來降低已知的曝險評估。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RouteMatchResult:
    matched_services: list = field(default_factory=list)   # list[Service]
    score: float = 0.0
    exposure_types: list = field(default_factory=list)


class RouteRelevanceEngine:
    def __init__(self, rules: dict):
        self.weights = rules.get("weights", {})

    def assess(self, event, services: list) -> RouteMatchResult:
        sea_area = getattr(event, "sea_area", None)
        shipping_lane = getattr(event, "shipping_lane", None)
        candidates = [c for c in (sea_area, shipping_lane) if c]
        if not candidates or not services:
            return RouteMatchResult()

        matched = [
            s for s in services
            if any(c in (s.major_shipping_lanes or []) for c in candidates)
        ]
        if not matched:
            return RouteMatchResult()

        return RouteMatchResult(
            matched_services=matched,
            score=self.weights.get("route_direct_lane", 20),
            exposure_types=["SERVICE_ROUTE", "SHIPPING_LANE"],
        )
