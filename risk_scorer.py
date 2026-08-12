#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
risk_scorer.py  v1.0
海事航運新聞監控系統 — Phase 2 Risk Scoring Engine（§七〜§十三）

Management Score = Severity(30) + Fleet Relevance(25) + Immediacy(20)
                   + Operational Impact(15) + Source Confidence(10)
                   = 0-100

每個 component 都是 deterministic、可單獨呼叫、可單獨測試，
完全不依賴 LLM。所有 threshold / weight / keyword 皆讀自 risk_rules.json。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from risk_config import load_risk_rules
from models import (NewsArticle, MaritimeEvent, SourceTier, ConfidenceLevel,
                    ManagementPriority, InformationStatus)
from event_extractor import EventExtractor, normalize_title

logger = logging.getLogger(__name__)


class RiskScorer:

    def __init__(self, rules: Optional[dict] = None,
                 extractor: Optional[EventExtractor] = None):
        self.rules = rules or load_risk_rules()
        self.extractor = extractor or EventExtractor(self.rules)

        self.severity_tiers   = self.rules.get("severity_score_tiers", [])
        self.severity_default = self.rules.get("severity_default_by_event_type", {})
        self.fleet_cfg         = self.rules.get("fleet_relevance", {})
        self.immediacy_cfg     = self.rules.get("immediacy_score_rules", {})
        self.impact_cfg        = self.rules.get("operational_impact_rules", {})
        self.priority_thresholds = self.rules.get(
            "priority_thresholds", {"P1": 80, "P2": 60, "P3": 40, "P4": 0}
        )
        self.critical_cfg = self.rules.get("critical_override", {})

        self._major_carrier_keys = {
            c["key"] for c in self.rules.get("major_carriers", [])
        }
        self._major_area_keys = {
            a["key"] for a in self.rules.get("major_shipping_areas", [])
        }

    # ═══════════════════════════════════════════════════════
    # 1. Severity 0-30
    # ═══════════════════════════════════════════════════════
    def score_severity(self, title: str, summary: str,
                       event_type: Optional[str] = None) -> float:
        text = normalize_title(f"{title} {summary}")
        for tier in self.severity_tiers:      # json 內已由高到低排列
            kws = [k.lower() for k in
                   tier.get("keywords_en", []) + tier.get("keywords_zh", [])]
            if any(kw in text for kw in kws):
                return float(tier["score"])
        if event_type:
            return float(self.severity_default.get(event_type, 5))
        return 5.0

    # ═══════════════════════════════════════════════════════
    # 2. Fleet Relevance 0-25（單一分支回傳，不會疊加超過上限）
    # ═══════════════════════════════════════════════════════
    def score_fleet_relevance(self, text: str,
                              carrier_key: Optional[str],
                              vessel_type_key: Optional[str],
                              sea_area_key: Optional[str]) -> float:
        cfg = self.fleet_cfg
        text_norm = normalize_title(text)

        own_fleet_kw = [k.lower() for k in cfg.get("own_fleet_keywords", [])]
        if (carrier_key and self.extractor.is_own_fleet_carrier(carrier_key)) \
           or any(kw in text_norm for kw in own_fleet_kw):
            return float(cfg.get("own_fleet_score", 25))

        is_major_carrier = carrier_key in self._major_carrier_keys
        is_major_area     = sea_area_key in self._major_area_keys

        if vessel_type_key == "CONTAINER_SHIP" and (is_major_carrier or is_major_area):
            return float(cfg.get("major_liner_route_score", 20))

        if is_major_carrier or is_major_area:
            return float(cfg.get("major_carrier_or_area_score", 15))

        merchant_kw = [k.lower() for k in
                       cfg.get("general_merchant_keywords_en", []) +
                       cfg.get("general_merchant_keywords_zh", [])]
        if any(kw in text_norm for kw in merchant_kw):
            return float(cfg.get("general_merchant_score", 10))

        indirect_kw = [k.lower() for k in
                       cfg.get("indirect_market_keywords_en", []) +
                       cfg.get("indirect_market_keywords_zh", [])]
        if any(kw in text_norm for kw in indirect_kw):
            return float(cfg.get("indirect_market_score", 5))

        return float(cfg.get("no_relevance_score", 0))

    # ═══════════════════════════════════════════════════════
    # 3. Immediacy 0-20
    # ═══════════════════════════════════════════════════════
    def score_immediacy(self, text: str,
                        published_at: Optional[datetime],
                        now: Optional[datetime] = None) -> float:
        cfg = self.immediacy_cfg
        now = now or datetime.now(timezone.utc)

        if published_at is None:
            score = float(cfg.get("unknown_time_score", 5))
        else:
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            hours = max(0.0, (now - published_at).total_seconds() / 3600.0)
            score = None
            for rule in cfg.get("hours_thresholds", []):
                max_h = rule.get("max_hours")
                if max_h is None or hours <= max_h:
                    score = float(rule["score"])
                    break
            if score is None:
                score = 2.0

        text_norm = normalize_title(text)
        ongoing_kw = [k.lower() for k in
                      cfg.get("ongoing_keywords_en", []) +
                      cfg.get("ongoing_keywords_zh", [])]
        if any(kw in text_norm for kw in ongoing_kw):
            score += float(cfg.get("ongoing_bonus", 0))

        return min(score, float(cfg.get("max_score", 20)))

    # ═══════════════════════════════════════════════════════
    # 4. Operational Impact 0-15（回傳 score + impact tags）
    # ═══════════════════════════════════════════════════════
    def score_operational_impact(self, text: str) -> tuple[float, list[str]]:
        cfg = self.impact_cfg
        text_norm = normalize_title(text)
        for tier in cfg.get("score_tiers", []):
            kws = [k.lower() for k in
                   tier.get("keywords_en", []) + tier.get("keywords_zh", [])]
            if any(kw in text_norm for kw in kws):
                return float(tier["score"]), list(tier.get("tags", []))
        return float(cfg.get("default_score", 0)), []

    # ═══════════════════════════════════════════════════════
    # 5. Source Confidence 0-10
    # ═══════════════════════════════════════════════════════
    def score_source_confidence_article(self, source_tier: Optional[str]) -> float:
        return float(SourceTier.SCORE.get(source_tier, SourceTier.SCORE[SourceTier.C]))

    def score_source_confidence_event(self, source_tiers: dict[str, int]) -> float:
        if source_tiers.get(SourceTier.A, 0) > 0:
            return 10.0
        b = source_tiers.get(SourceTier.B, 0)
        if b >= 2:
            return 10.0
        if b == 1:
            return 8.0
        c = source_tiers.get(SourceTier.C, 0)
        if c >= 2:
            return 6.0
        if c == 1:
            return 5.0
        if source_tiers.get(SourceTier.D, 0) > 0:
            return 2.0
        return 5.0

    def confidence_level(self, source_tiers: dict[str, int]) -> str:
        """
        ★ Phase 2.1：呼叫端必須傳 independent_source_tiers（已排除轉載的
        獨立來源 tier 分布），不是逐篇文章的 raw source_tiers，
        否則同一篇 Reuters 稿被轉載 3 次會被誤判成 3 個來源交叉證實。
        """
        if source_tiers.get(SourceTier.A, 0) > 0:
            return ConfidenceLevel.HIGH
        if source_tiers.get(SourceTier.B, 0) >= 2:
            return ConfidenceLevel.HIGH
        if source_tiers.get(SourceTier.B, 0) == 1:
            return ConfidenceLevel.MEDIUM
        if source_tiers.get(SourceTier.C, 0) >= 2:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    def determine_information_status(self, source_tiers: dict[str, int]) -> str:
        """
        Phase 2.1 §六：information_status 與 management_priority 完全獨立，
        回答的是「這件事有多確定是真的」，不是「主管要多快知道」。
        同樣必須傳 independent_source_tiers。
        """
        if source_tiers.get(SourceTier.A, 0) > 0:
            return InformationStatus.CONFIRMED
        if source_tiers.get(SourceTier.B, 0) >= 2:
            return InformationStatus.CORROBORATED
        if source_tiers.get(SourceTier.B, 0) >= 1 or source_tiers.get(SourceTier.C, 0) >= 1:
            return InformationStatus.UNCONFIRMED
        return InformationStatus.EARLY_SIGNAL

    # ═══════════════════════════════════════════════════════
    # Management Priority + Critical Override（§十三）
    # ═══════════════════════════════════════════════════════
    def determine_priority(self, management_score: float,
                           event_type: Optional[str],
                           severity_score: float,
                           text: str,
                           is_own_fleet: bool) -> tuple[str, bool]:
        th = self.priority_thresholds
        if management_score >= th.get("P1", 80):
            base = ManagementPriority.P1
        elif management_score >= th.get("P2", 60):
            base = ManagementPriority.P2
        elif management_score >= th.get("P3", 40):
            base = ManagementPriority.P3
        else:
            base = ManagementPriority.P4

        if base == ManagementPriority.P1:
            return base, False

        if self._check_critical_override(
            management_score, event_type, severity_score, text, is_own_fleet
        ):
            return ManagementPriority.P1, True

        return base, False

    def _check_critical_override(self, management_score: float,
                                 event_type: Optional[str],
                                 severity_score: float,
                                 text: str,
                                 is_own_fleet: bool) -> bool:
        cfg = self.critical_cfg
        if management_score < cfg.get("min_score_to_consider", 55):
            return False

        text_norm = normalize_title(text)

        if is_own_fleet and severity_score >= cfg.get("own_fleet_min_severity", 15):
            return True

        triggers = cfg.get("event_type_triggers", {}).get(event_type or "", [])
        if any(k.lower() in text_norm for k in triggers):
            return True

        closure_kw = [k.lower() for k in cfg.get("location_closure_keywords", [])]
        if any(kw in text_norm for kw in closure_kw):
            return True

        return False

    # ═══════════════════════════════════════════════════════
    # 便利方法：單篇 Article 層級評分
    # ═══════════════════════════════════════════════════════
    def _article_confidence_label(self, tier: Optional[str]) -> str:
        if tier == SourceTier.A:
            return ConfidenceLevel.HIGH
        if tier in (SourceTier.B, SourceTier.C):
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    def score_article(self, article: NewsArticle,
                      now: Optional[datetime] = None) -> NewsArticle:
        now = now or datetime.now(timezone.utc)
        text = f"{article.title} {article.summary}"

        article.severity_score = self.score_severity(
            article.title, article.summary, article.event_type
        )
        article.relevance_score = self.score_fleet_relevance(
            text, article.carrier, article.vessel_type, article.sea_area
        )
        article.immediacy_score = self.score_immediacy(
            text, article.published_at, now
        )
        impact_score, tags = self.score_operational_impact(text)
        article.operational_impact_score = impact_score
        article._impact_tags = tags

        article.source_confidence_score = self.score_source_confidence_article(
            article.source_tier
        )

        total = (article.severity_score + article.relevance_score +
                 article.immediacy_score + article.operational_impact_score +
                 article.source_confidence_score)
        article.management_score = round(min(total, 100.0), 1)

        is_own_fleet = self.extractor.is_own_fleet_carrier(article.carrier)
        priority, override = self.determine_priority(
            article.management_score, article.event_type,
            article.severity_score, text, is_own_fleet
        )
        article.management_priority = priority
        article._critical_override = override
        article.confidence = self._article_confidence_label(article.source_tier)

        return article

    # ═══════════════════════════════════════════════════════
    # 便利方法：Event 層級評分（彙整多篇 Article）
    # ═══════════════════════════════════════════════════════
    def score_event(self, event: MaritimeEvent,
                    now: Optional[datetime] = None) -> MaritimeEvent:
        now = now or datetime.now(timezone.utc)
        articles = event.articles

        severity = max((a.severity_score or 0.0) for a in articles) if articles else 0.0
        operational_impact = max(
            (a.operational_impact_score or 0.0) for a in articles
        ) if articles else 0.0

        primary = event.primary_article or (articles[0] if articles else None)
        rep_text = f"{event.headline} {(primary.summary if primary else '')}"

        fleet_relevance = self.score_fleet_relevance(
            rep_text, event.carrier, event.vessel_type, event.sea_area
        )
        if articles:
            fleet_relevance = max(
                fleet_relevance,
                max((a.relevance_score or 0.0) for a in articles)
            )

        immediacy = self.score_immediacy(rep_text, event.first_seen, now)

        # ★ Phase 2.1：confidence / information_status 都用「獨立來源」
        # 的 tier 分布計算，不是逐篇文章的 raw source_tiers（見 §十五）。
        indep_tiers = event.independent_source_tiers or event.source_tiers
        source_confidence = self.score_source_confidence_event(indep_tiers)
        conf_level = self.confidence_level(indep_tiers)
        info_status = self.determine_information_status(indep_tiers)

        total = (severity + fleet_relevance + immediacy +
                 operational_impact + source_confidence)
        management_score = round(min(total, 100.0), 1)

        is_own_fleet = (
            self.extractor.is_own_fleet_carrier(event.carrier)
            or any(self.extractor.is_own_fleet_carrier(a.carrier) for a in articles)
        )
        priority, override = self.determine_priority(
            management_score, event.event_type, severity, rep_text, is_own_fleet
        )

        event.severity_score           = severity
        event.fleet_relevance_score    = fleet_relevance
        event.immediacy_score          = immediacy
        event.operational_impact_score = operational_impact
        event.source_confidence_score  = source_confidence
        event.management_score         = management_score
        event.management_priority      = priority
        event.confidence_level         = conf_level
        event.information_status       = info_status
        event._critical_override       = override

        tags: set[str] = set(event.impact_tags)
        for a in articles:
            tags.update(getattr(a, "_impact_tags", []) or [])
        event.impact_tags = sorted(tags)

        return event

    def score_events(self, events: list[MaritimeEvent],
                     now: Optional[datetime] = None) -> list[MaritimeEvent]:
        now = now or datetime.now(timezone.utc)
        for e in events:
            self.score_event(e, now=now)
        return events


# ══════════════════════════════════════════════════════════════
# 排序（§二十二）：priority → score → last_updated，禁止再用 published time 排主序
# ══════════════════════════════════════════════════════════════
def sort_events(events: list[MaritimeEvent]) -> list[MaritimeEvent]:
    def _key(e: MaritimeEvent):
        rank = ManagementPriority.RANK.get(e.management_priority, 99)
        score = -(e.management_score or 0.0)
        updated = e.last_updated or e.first_seen or datetime.min.replace(tzinfo=timezone.utc)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return (rank, score, -updated.timestamp())
    return sorted(events, key=_key)
