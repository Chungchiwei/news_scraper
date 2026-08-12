#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
carrier_news_filter.py  v1.0
海事航運新聞監控系統 — Phase 2 Carrier PR Filtering（§六）

問題背景：
  舊邏輯只要 source_category == "航商動態" 且沒踩到 finance noise
  幾乎都會通過，導致大量企業 PR / CSR / 得獎 / 品牌活動被送給主管。

本模組職責：
  針對「航商官方來源」或「新分類為 COMPETITOR」的文章，
  用 keep/exclude 關鍵字判斷是否具有 operational significance。

決策三態：
  KEEP_OPERATIONAL — 命中 keep 關鍵字，視為有營運意義，正常進入後續流程
  KEEP_LOW_VALUE    — 兩邊都沒命中（不確定），保留但不得被抬高優先級
  DROP              — 命中 exclude 關鍵字且沒有 keep 關鍵字，直接濾除

不確定時寧可保留並讓 RiskScorer 自然評低分（COMPETITOR 預設 severity=5、
operational_impact 預設 0），也不要武斷丟棄或武斷升級。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from risk_config import load_risk_rules
from models import NewsArticle, EventType

logger = logging.getLogger(__name__)


@dataclass
class CarrierFilterResult:
    decision: str          # KEEP_OPERATIONAL / KEEP_LOW_VALUE / DROP
    reason: str
    matched_keep:    list[str]
    matched_exclude: list[str]


class CarrierNewsFilter:
    KEEP_OPERATIONAL = "KEEP_OPERATIONAL"
    KEEP_LOW_VALUE   = "KEEP_LOW_VALUE"
    DROP             = "DROP"

    def __init__(self, rules: Optional[dict] = None):
        self.rules = rules or load_risk_rules()
        cfg = self.rules.get("carrier_pr_filter", {})
        self.keep_keywords = [
            k.lower() for k in
            cfg.get("keep_keywords_en", []) + cfg.get("keep_keywords_zh", [])
        ]
        self.exclude_keywords = [
            k.lower() for k in
            cfg.get("exclude_keywords_en", []) + cfg.get("exclude_keywords_zh", [])
        ]

    # ── 是否需要套用此 filter ───────────────────────────────
    @staticmethod
    def applies_to(article: NewsArticle) -> bool:
        return (
            article.source_category == "航商動態"
            or article.event_type == EventType.COMPETITOR
        )

    # ── 核心判斷 ─────────────────────────────────────────────
    def decide(self, article: NewsArticle) -> CarrierFilterResult:
        text_lower = f"{article.title} {article.summary}".lower()

        matched_keep = [k for k in self.keep_keywords if k in text_lower]
        matched_exclude = [k for k in self.exclude_keywords if k in text_lower]

        if matched_keep:
            return CarrierFilterResult(
                decision=self.KEEP_OPERATIONAL,
                reason=f"命中 operational 關鍵字: {matched_keep[:3]}",
                matched_keep=matched_keep,
                matched_exclude=matched_exclude,
            )

        if matched_exclude:
            return CarrierFilterResult(
                decision=self.DROP,
                reason=f"命中 PR/行銷關鍵字且無 operational 關鍵字: {matched_exclude[:3]}",
                matched_keep=matched_keep,
                matched_exclude=matched_exclude,
            )

        return CarrierFilterResult(
            decision=self.KEEP_LOW_VALUE,
            reason="無法判斷是否具 operational significance，保留但不升級",
            matched_keep=matched_keep,
            matched_exclude=matched_exclude,
        )

    # ── 批次套用 ─────────────────────────────────────────────
    def filter_articles(
        self, articles: list[NewsArticle]
    ) -> tuple[list[NewsArticle], list[tuple[NewsArticle, CarrierFilterResult]]]:
        """
        回傳 (kept, dropped)。
        dropped 為 (article, result) tuple，方便 console report / log 說明原因。
        非「航商官方來源／COMPETITOR」的文章不受影響，直接保留。
        """
        kept: list[NewsArticle] = []
        dropped: list[tuple[NewsArticle, CarrierFilterResult]] = []

        for a in articles:
            if not self.applies_to(a):
                kept.append(a)
                continue

            result = self.decide(a)
            a._carrier_filter_decision = result.decision  # 供測試/報表查詢

            if result.decision == self.DROP:
                dropped.append((a, result))
                logger.info(
                    f"    🚫 濾除航商 PR：「{a.title[:40]}」— {result.reason}"
                )
            else:
                kept.append(a)

        return kept, dropped
