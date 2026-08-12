#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
source_provenance.py  v1.0
海事航運新聞監控系統 — Phase 2.1 Source Independence（§十二〜十七）

問題背景：
  Event.source_count 過去等於「文章數」。但 Reuters 原稿被 Yahoo／MSN
  轉載 3 次，article_count=3，實際上只有 1 個獨立消息來源，不該被當成
  「3 家媒體交叉證實」而給出 HIGH confidence。

本模組職責：
  用 deterministic pattern（不做 NLP／LLM）判斷一篇文章屬於哪個
  「source_family」：
    1. source_name 本身就對得上已知通訊社別名（例如 source_name="Reuters"）
    2. 內文出現「according to Reuters / Reuters reported / via Reuters」
       這類轉載慣用語
    3. 都對不上 → 該文章的 source_name 本身視為獨立的 family
  再彙整成 article_count / source_count / independent_source_count 三個
  不同概念，供 RiskScorer 的 confidence / information_status 使用。
"""

from __future__ import annotations

import re
from typing import Optional

from risk_config import load_risk_rules
from models import NewsArticle, SourceTier

_TIER_RANK = {SourceTier.A: 0, SourceTier.B: 1, SourceTier.C: 2, SourceTier.D: 3}


class SourceProvenanceResolver:

    def __init__(self, rules: Optional[dict] = None):
        self.rules = rules or load_risk_rules()
        patterns_cfg = self.rules.get("source_family_patterns", {})

        self._alias_patterns: dict[str, list[re.Pattern]] = {}
        self._repost_patterns: dict[str, list[re.Pattern]] = {}
        for family, cfg in patterns_cfg.items():
            if family.startswith("_") or not isinstance(cfg, dict):
                continue
            self._alias_patterns[family] = [
                re.compile(re.escape(a), re.IGNORECASE) for a in cfg.get("aliases", [])
            ]
            self._repost_patterns[family] = [
                re.compile(re.escape(p), re.IGNORECASE) for p in cfg.get("repost_patterns", [])
            ]

    # ── 判斷單篇文章屬於哪個 source_family ─────────────────────
    def resolve_family(self, article: NewsArticle) -> str:
        source_name = (article.source_name or "").strip()
        source_name_lower = source_name.lower()

        # 1. source_name 本身就是已知通訊社
        for family, patterns in self._alias_patterns.items():
            if any(p.search(source_name_lower) for p in patterns):
                return family

        # 2. 內文有「轉載自 XXX」的慣用語
        text_lower = f"{article.title} {article.summary}".lower()
        for family, patterns in self._repost_patterns.items():
            if any(p.search(text_lower) for p in patterns):
                return family

        # 3. 都對不上：自己的 source_name 正規化後就是獨立的 family
        return source_name.upper() if source_name else "UNKNOWN"

    def annotate(self, articles: list[NewsArticle]) -> list[NewsArticle]:
        for a in articles:
            a.source_family = self.resolve_family(a)
            # source_domain 目前沒有實際網域資訊來源（RSS entry 沒有保留），
            # 暫時留 None，是 Phase 2.1 §十三 明確允許的「schema 預留、資料不足」情形。
        return articles

    # ── 統計方法 ─────────────────────────────────────────────
    def article_count(self, articles: list[NewsArticle]) -> int:
        return len(articles)

    def source_count(self, articles: list[NewsArticle]) -> int:
        """distinct source_name 數（可能包含轉載）。"""
        return len({(a.source_name or "").strip() for a in articles if a.source_name})

    def independent_source_count(self, articles: list[NewsArticle]) -> int:
        families = {a.source_family or self.resolve_family(a) for a in articles}
        return len(families)

    def independent_source_tiers(self, articles: list[NewsArticle]) -> dict[str, int]:
        """
        依『獨立來源』（而非文章）計的 tier 分布 —— confidence / information_status
        必須用這個，不是原始的逐篇 tier 計數，否則轉載會不當墊高 confidence。
        每個 family 取該 family 底下文章中「最高」的 tier。
        """
        best_tier_per_family: dict[str, str] = {}
        for a in articles:
            fam = a.source_family or self.resolve_family(a)
            tier = a.source_tier or SourceTier.C
            current = best_tier_per_family.get(fam)
            if current is None or _TIER_RANK.get(tier, 2) < _TIER_RANK.get(current, 2):
                best_tier_per_family[fam] = tier

        counts: dict[str, int] = {}
        for tier in best_tier_per_family.values():
            counts[tier] = counts.get(tier, 0) + 1
        return counts
