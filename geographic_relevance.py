#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
geographic_relevance.py
海事航運新聞監控系統 — Phase 6 §二十一（geographic exposure 部分）、四十一

職責：
  比 route_relevance.py 更寬鬆的一層比對：事件的 region（例如 "ASIA"、
  "MIDDLE_EAST"）是否落在某條 Service 營運的大區域範圍內。用於事件
  沒有明確 sea_area/shipping_lane、但仍屬於同一大區域的情況（§九：
  「同產業／同區域」）。

  也承載 §四十一 Regulatory Global Fleet 判斷的關鍵字比對邏輯 ——
  Rule-Based，不由 LLM 判斷法規適用範圍。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GeographicMatchResult:
    matched_services: list = field(default_factory=list)
    score: float = 0.0
    exposure_types: list = field(default_factory=list)


class GeographicRelevanceEngine:
    def __init__(self, rules: dict):
        self.weights = rules.get("weights", {})

    def assess(self, event, services: list) -> GeographicMatchResult:
        region = getattr(event, "region", None)
        if not region or not services:
            return GeographicMatchResult()

        matched = [s for s in services if region in (s.regions or [])]
        if not matched:
            return GeographicMatchResult()

        return GeographicMatchResult(
            matched_services=matched,
            score=self.weights.get("route_same_region", 5),
            exposure_types=["REGIONAL"],
        )


class GlobalFleetRegulatoryEngine:
    """
    §四十一〜四十二：Regulatory 事件不能只用地理比對 —— IMO/SOLAS/MARPOL
    這類法規通常適用整個船隊，不需要事件發生在特定港口/航道附近。
    用 llm_rules 風格的關鍵字白名單做 rule-based 判斷（不是 LLM 判斷）。
    """

    def __init__(self, rules: dict):
        self.weights = rules.get("weights", {})
        self.keywords = [k.lower() for k in rules.get("global_fleet_regulatory_keywords", [])]

    def assess(self, event) -> GeographicMatchResult:
        if getattr(event, "event_type", None) != "REGULATORY":
            return GeographicMatchResult()

        primary = getattr(event, "primary_article", None)
        text = f"{getattr(event, 'headline', '') or ''} {getattr(primary, 'summary', '') if primary else ''}".lower()
        if any(kw in text for kw in self.keywords):
            return GeographicMatchResult(
                matched_services=[], score=self.weights.get("global_fleet_regulatory", 65),
                exposure_types=["GLOBAL_FLEET"],
            )
        return GeographicMatchResult()
