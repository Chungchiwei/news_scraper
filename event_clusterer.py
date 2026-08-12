#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
event_clusterer.py  v2.0
海事航運新聞監控系統 — Event Clustering（Phase 2 + Phase 2.1 Hardening）

Phase 2.1 修正重點（§二〜四）：
  - 不再只累加 positive score，加入 Negative / Conflict Signals。
  - 明確不同的 vessel_name（兩篇都有名字、且不同）→ hard reject，
    不管其他訊號多強都不會被合併。
  - incident_subtype（比 event_type 更細的事故子類型）取代
    「同 event_type 就 +15」作為主要衝突判斷依據：
      · 同 subtype（例如都是 VESSEL_ATTACK）→ 強 positive，
        且會抑制掉 broad event_type 不一致時的 penalty
        （因為 subtype 判斷比 broad category 更可信）。
      · 不同 subtype（例如 collision vs grounding）→ 強 negative，
        即使 carrier / location 都相同也不足以合併
        （CASE 2.1-C：MSC collision 與 MSC grounding 不可合併）。
  - 不同 vessel_type（貨櫃船 vs 油輪）→ negative，防止 CASE 2.1-B
    （同海域、同攻擊描述，但船型不同）被誤判成同一事件。
  - 沒有明確船名/航商時，改靠：
    incident_subtype 一致 + 地理位置相關（含 region_group，不要求
    完全同一個 sea_area）+ 時間相近 + summary 相似度，仍然可以
    cluster 成同一事件（CASE 2.1-A）。
  - Primary Article 選擇規則不變（Tier 優先），但額外整合
    SourceProvenanceResolver，計算 article_count / source_count /
    independent_source_count / independent_source_tiers（§十二〜十七），
    confidence 與 information_status 的計算要用「獨立來源」而非文章數。
"""

from __future__ import annotations

import difflib
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from risk_config import load_risk_rules
from models import NewsArticle, MaritimeEvent, SourceTier
from event_extractor import normalize_title
from source_provenance import SourceProvenanceResolver

logger = logging.getLogger(__name__)

_TIER_RANK = {SourceTier.A: 0, SourceTier.B: 1, SourceTier.C: 2, SourceTier.D: 3}


class EventClusterer:

    def __init__(self, rules: Optional[dict] = None,
                 provenance: Optional[SourceProvenanceResolver] = None):
        self.rules = rules or load_risk_rules()
        cfg = self.rules.get("clustering", {})
        self.time_window_hours = cfg.get("time_window_hours", 48)
        self.threshold = cfg.get("cluster_score_threshold", 50)
        self.title_sim_cutoff = cfg.get("title_similarity_high_cutoff", 0.75)
        self.summary_sim_cutoff = cfg.get("summary_similarity_high_cutoff", 0.5)
        self.hard_reject_score = cfg.get("hard_reject_score", -9999)
        self.weights = cfg.get("weights", {})

        self._major_area_keys = {
            a["key"] for a in self.rules.get("major_shipping_areas", [])
        }
        self._area_region_group: dict[str, Optional[str]] = {
            a["key"]: a.get("region_group")
            for a in self.rules.get("major_shipping_areas", [])
        }

        self.provenance = provenance or SourceProvenanceResolver(self.rules)

        # 這次執行的診斷計數（供 print_validation_diagnostics 使用，見 maritime_news.py）
        self.diagnostics = {
            "pairs_evaluated": 0,
            "hard_rejects": 0,
            "missing_carrier_matches": 0,   # 沒有 carrier 但靠其他訊號成功 cluster 的次數
        }

    # ── 是否為 hard reject 的分數 ────────────────────────────
    def _is_hard_reject(self, score: float) -> bool:
        return score <= self.hard_reject_score

    # ── 兩篇文章的 cluster score ─────────────────────────────
    def pair_score(self, a: NewsArticle, b: NewsArticle) -> float:
        w = self.weights
        self.diagnostics["pairs_evaluated"] += 1

        # ★ Hard reject：兩篇都有明確船名、且船名不同 → 不管其他訊號多強都不合併
        if a.vessel_name and b.vessel_name:
            if a.vessel_name.strip().lower() != b.vessel_name.strip().lower():
                self.diagnostics["hard_rejects"] += 1
                return self.hard_reject_score

        score = 0.0
        used_missing_carrier_path = not (a.carrier and b.carrier)

        if a.vessel_name and b.vessel_name and \
           a.vessel_name.strip().lower() == b.vessel_name.strip().lower():
            score += w.get("same_vessel_name", 50)

        # 不同船型（兩邊都有資料才比較，缺資料不猜測、不扣分）
        if a.vessel_type and b.vessel_type and a.vessel_type != b.vessel_type:
            score += w.get("different_vessel_type", -40)

        if a.carrier and b.carrier and a.carrier == b.carrier:
            score += w.get("same_carrier", 15)

        # incident_subtype：比 event_type 更細，是主要的「同類事故」判斷依據
        subtype_conflict = False
        subtype_agree = False
        if a.incident_subtype and b.incident_subtype:
            if a.incident_subtype == b.incident_subtype:
                score += w.get("same_incident_subtype", 30)
                subtype_agree = True
            else:
                score += w.get("different_incident_subtype", -35)
                subtype_conflict = True

        # broad event_type：較弱的訊號，subtype 已經同意時不重複扣分
        # （避免 SAFETY/SECURITY 這種粗分類在戰時攻擊報導中因用詞差異而互相打架）
        if a.event_type and b.event_type:
            if a.event_type == b.event_type:
                score += w.get("same_event_type", 15)
            elif not subtype_agree:
                score += w.get("different_event_type", -10)

        # 地點：同一個 sea_area 給滿分；不同但屬於同一個 region_group（例如
        # 紅海／曼德海峽／亞丁灣同屬 RED_SEA_CORRIDOR）給較弱的加分
        if a.sea_area and b.sea_area:
            if a.sea_area == b.sea_area:
                score += w.get("same_location", 15)
                if a.sea_area in self._major_area_keys:
                    score += w.get("same_major_sea_area", 10)
            else:
                group_a = self._area_region_group.get(a.sea_area)
                group_b = self._area_region_group.get(b.sea_area)
                if group_a and group_a == group_b:
                    score += w.get("related_region_group", 12)
        elif a.location and b.location and a.location == b.location:
            score += w.get("same_location", 15)

        # 標題相似度
        title_a, title_b = a.normalized_title(), b.normalized_title()
        if title_a and title_b:
            ratio = difflib.SequenceMatcher(None, title_a, title_b).ratio()
            if ratio >= self.title_sim_cutoff:
                score += w.get("title_similarity_high", 20)

        # 摘要相似度（Phase 2.1 新增：沒有 carrier/vessel 可比對時的補強訊號）
        summary_a = normalize_title(a.summary or "")
        summary_b = normalize_title(b.summary or "")
        if summary_a and summary_b:
            ratio = difflib.SequenceMatcher(None, summary_a, summary_b).ratio()
            if ratio >= self.summary_sim_cutoff:
                score += w.get("summary_similarity_high", 15)

        # 時間相近度（額外加分，不只是硬性時間窗）
        if a.published_at and b.published_at:
            pa, pb = a.published_at, b.published_at
            if pa.tzinfo is None:
                pa = pa.replace(tzinfo=timezone.utc)
            if pb.tzinfo is None:
                pb = pb.replace(tzinfo=timezone.utc)
            diff_hours = abs((pa - pb).total_seconds()) / 3600.0
            if diff_hours <= w.get("time_proximity_close_hours", 6):
                score += w.get("time_proximity_close_bonus", 10)

        if (used_missing_carrier_path and not subtype_conflict
                and score >= self.threshold):
            self.diagnostics["missing_carrier_matches"] += 1

        return score

    # ── 時間窗檢查（§十五：只有 ±time_window_hours 內才考慮 cluster）──
    def _within_time_window(self, article: NewsArticle,
                            cluster_articles: list[NewsArticle]) -> bool:
        if article.published_at is None:
            return True   # 時間未知：不因時間排除，交由其他 signal 判斷
        window = timedelta(hours=self.time_window_hours)
        for other in cluster_articles:
            if other.published_at is None:
                return True
            a = article.published_at
            b = other.published_at
            if a.tzinfo is None:
                a = a.replace(tzinfo=timezone.utc)
            if b.tzinfo is None:
                b = b.replace(tzinfo=timezone.utc)
            if abs(a - b) <= window:
                return True
        return False

    # ── 主要分群邏輯（貪婪聚類）─────────────────────────────
    def cluster(self, articles: list[NewsArticle]) -> list[MaritimeEvent]:
        self.diagnostics = {
            "pairs_evaluated": 0, "hard_rejects": 0, "missing_carrier_matches": 0,
        }
        clusters: list[list[NewsArticle]] = []

        for article in articles:
            best_idx, best_score = None, 0.0
            for idx, cluster_articles in enumerate(clusters):
                if not self._within_time_window(article, cluster_articles):
                    continue

                pair_scores = [self.pair_score(article, other) for other in cluster_articles]
                # ★ 只要跟 cluster 內任何一篇文章 hard reject，整個 cluster 就不能加入
                # （不能因為跟 cluster 裡另一篇沒衝突的文章比對分數高，就無視明確的船名衝突）
                if any(self._is_hard_reject(s) for s in pair_scores):
                    continue

                score = max(pair_scores)
                if score > best_score:
                    best_score, best_idx = score, idx

            if best_idx is not None and best_score >= self.threshold:
                clusters[best_idx].append(article)
            else:
                clusters.append([article])

        events = [self._build_event(c) for c in clusters]
        return events

    # ── Primary Article 選擇（§十六）──────────────────────────
    @staticmethod
    def select_primary(articles: list[NewsArticle]) -> NewsArticle:
        def _key(a: NewsArticle):
            tier_rank    = _TIER_RANK.get(a.source_tier, 2)          # 未知一律當 C
            summary_len  = -(len(a.summary or ""))                    # 摘要越完整越優先
            pub          = a.published_at or datetime.max.replace(tzinfo=timezone.utc)
            return (tier_rank, summary_len, pub)
        return sorted(articles, key=_key)[0]

    # ── 從一群 Article 建立 MaritimeEvent ──────────────────────
    def _build_event(self, articles: list[NewsArticle]) -> MaritimeEvent:
        primary = self.select_primary(articles)

        def _first_non_null(attr):
            val = getattr(primary, attr)
            if val is not None:
                return val
            for a in articles:
                v = getattr(a, attr)
                if v is not None:
                    return v
            return None

        published_times = [a.published_at for a in articles if a.published_at]
        first_seen   = min(published_times) if published_times else None
        last_updated = max(published_times) if published_times else first_seen

        # ── §十二〜十七 Source Independence ─────────────────────
        self.provenance.annotate(articles)
        article_count             = self.provenance.article_count(articles)
        source_count               = self.provenance.source_count(articles)
        independent_source_count   = self.provenance.independent_source_count(articles)
        independent_source_tiers   = self.provenance.independent_source_tiers(articles)

        # 逐篇 tier 分布（診斷用，不用於 confidence/information_status 計算）
        raw_source_tiers: dict[str, int] = {}
        for a in articles:
            t = a.source_tier or SourceTier.C
            raw_source_tiers[t] = raw_source_tiers.get(t, 0) + 1

        event_id = "evt_" + hashlib.sha1(
            (primary.article_id or primary.title).encode("utf-8")
        ).hexdigest()[:12]

        event = MaritimeEvent(
            event_id=event_id,
            headline=primary.title,
            event_type=_first_non_null("event_type"),
            incident_subtype=_first_non_null("incident_subtype"),
            incident_category=_first_non_null("incident_category"),
            first_seen=first_seen,
            last_updated=last_updated,
            primary_article=primary,
            articles=list(articles),
            vessel_name=_first_non_null("vessel_name"),
            vessel_type=_first_non_null("vessel_type"),
            carrier=_first_non_null("carrier"),
            location=_first_non_null("location"),
            country=_first_non_null("country"),
            region=_first_non_null("region"),
            port=_first_non_null("port"),
            sea_area=_first_non_null("sea_area"),
            shipping_lane=_first_non_null("shipping_lane"),
            article_count=article_count,
            source_count=source_count,
            independent_source_count=independent_source_count,
            source_tiers=raw_source_tiers,
            independent_source_tiers=independent_source_tiers,
            is_new=True,
            is_update=False,
        )

        for a in articles:
            a.event_id = event_id
            a.cluster_id = event_id

        return event
