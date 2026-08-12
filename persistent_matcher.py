#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
persistent_matcher.py
海事航運新聞監控系統 — Phase 3 §十五〜十七 Event Matching Before New Event Creation

流程（§十五）：
    Incoming Event（這次 run 剛 cluster+score 完的 MaritimeEvent）
      ↓
    Search Active Memory（ACTIVE / MONITORING / 最近 RESOLVED 的既有事件，
    依 event_type 決定搜尋窗口天數，見 memory_rules.json matching_windows）
      ↓
    Possible Match?
      ├── YES → 回傳既有 event_id（不建立新 ID，即使 canonical_key 因為
      │         得知船名而升級，也只是更新 canonical_key 欄位，
      │         event_id 保持不變）
      └── NO  → 呼叫端負責用 EventIdentityBuilder.generate_event_id() 建立新 ID

沿用 Phase 2.1 EventClusterer 的核心設計哲學：Positive Signal + Negative/
Conflict Signal + Hard Reject，而不是單純「JSON 有沒有變」。
"""

from __future__ import annotations

import difflib
import logging
from datetime import datetime, timezone
from typing import Optional

from risk_config import load_risk_rules
from event_extractor import normalize_title
from event_identity import EventIdentityBuilder, IdentitySignals, normalize_vessel_name, _get

logger = logging.getLogger(__name__)


class PersistentEventMatcher:

    def __init__(self, memory_rules: dict, risk_rules: Optional[dict] = None,
                 identity_builder: Optional[EventIdentityBuilder] = None):
        self.memory_rules = memory_rules
        self.risk_rules = risk_rules or load_risk_rules()
        self.identity = identity_builder or EventIdentityBuilder()

        mw = memory_rules.get("matching_windows", {})
        self.default_window_days = mw.get("default_days", 7)
        self.window_by_event_type = mw.get("by_event_type", {})
        self.threshold = mw.get("match_score_threshold", 50)
        self.hard_reject_score = mw.get("hard_reject_score", -9999)
        self.weights = memory_rules.get("matching_weights", {})

        self._area_region_group: dict[str, Optional[str]] = {
            a["key"]: a.get("region_group")
            for a in self.risk_rules.get("major_shipping_areas", [])
        }

        self.diagnostics = {
            "candidates_evaluated": 0,
            "hard_rejects": 0,
            "canonical_key_fast_path_hits": 0,
        }

    # ── 搜尋窗口 ──────────────────────────────────────────────
    def window_days_for(self, event_type: Optional[str]) -> int:
        return self.window_by_event_type.get(event_type or "", self.default_window_days)

    def _is_hard_reject(self, score: float) -> bool:
        return score <= self.hard_reject_score

    # ── 跨 run 事件層級 pair score ────────────────────────────
    def pair_score(self, incoming, candidate: dict) -> float:
        """incoming 可以是 MaritimeEvent，candidate 是 DB dict row（見 event_store.py）。"""
        w = self.weights
        self.diagnostics["candidates_evaluated"] += 1

        imo_a = _get(incoming, "imo_number")
        imo_b = candidate.get("imo_number")
        if imo_a and imo_b:
            if imo_a == imo_b:
                return float(w.get("same_imo", 200))
            self.diagnostics["hard_rejects"] += 1
            return self.hard_reject_score

        vessel_a = normalize_vessel_name(_get(incoming, "vessel_name"))
        vessel_b = normalize_vessel_name(candidate.get("vessel_name"))
        if vessel_a and vessel_b and vessel_a != vessel_b:
            self.diagnostics["hard_rejects"] += 1
            return self.hard_reject_score

        score = 0.0
        if vessel_a and vessel_b and vessel_a == vessel_b:
            score += w.get("same_vessel_name", 60)

        vtype_a = _get(incoming, "vessel_type")
        vtype_b = candidate.get("vessel_type")
        if vtype_a and vtype_b and vtype_a != vtype_b:
            score += w.get("different_vessel_type", -70)

        carrier_a = _get(incoming, "carrier")
        carrier_b = candidate.get("carrier")
        if carrier_a and carrier_b and carrier_a == carrier_b:
            score += w.get("same_carrier", 15)

        subtype_a = _get(incoming, "incident_subtype")
        subtype_b = candidate.get("incident_subtype")
        subtype_agree = subtype_conflict = False
        if subtype_a and subtype_b:
            if subtype_a == subtype_b:
                score += w.get("same_incident_subtype", 30)
                subtype_agree = True
            else:
                score += w.get("different_incident_subtype", -35)
                subtype_conflict = True

        # event_type：跟 incident_subtype 一樣，缺資料（含籠統的 OTHER，
        # 例如後續 refloated/reopened 這類單純『進度更新』文章常常抓不到
        # 具體 event_type 關鍵字）不當作衝突訊號，避免合理的後續報導
        # 因為用詞不同、事件粗分類抓不到而配對不到既有事件。
        etype_a = _get(incoming, "event_type")
        etype_b = candidate.get("event_type")
        etype_a_specific = etype_a if etype_a and etype_a != "OTHER" else None
        etype_b_specific = etype_b if etype_b and etype_b != "OTHER" else None
        if etype_a_specific and etype_b_specific:
            if etype_a_specific == etype_b_specific:
                score += w.get("same_event_type", 15)
            elif not subtype_agree:
                score += w.get("different_event_type", -10)

        area_a = _get(incoming, "sea_area")
        area_b = candidate.get("sea_area")
        location_related = False
        if area_a and area_b:
            if area_a == area_b:
                score += w.get("same_sea_area", 15)
                location_related = True
            else:
                g_a = self._area_region_group.get(area_a)
                g_b = self._area_region_group.get(area_b)
                if g_a and g_a == g_b:
                    score += w.get("related_region_group", 10)
                    location_related = True

        # Level 3 identity（§十二）：Carrier + Location 同時吻合是獨立於
        # 個別訊號相加的強身份證據，尤其是後續進度報導（refloated/reopened
        # 之類）常常抓不到具體 event_type/incident_subtype 關鍵字，
        # 這個加分讓「同航商 + 同地點」在跨 run 比對時仍能穿過門檻。
        # ★ 但如果 incident_subtype 明確衝突（例如 collision vs grounding，
        # 對應 Phase 2.1 CASE 2.1-C 在跨 run 版本），不能再給這個加分——
        # 否則會把同一航商在同海域發生的兩起不同事故錯誤合併成一個事件。
        carrier_matched = bool(carrier_a and carrier_b and carrier_a == carrier_b)
        if carrier_matched and location_related and not subtype_conflict:
            score += w.get("carrier_and_location_bonus", 15)

        title_a = normalize_title(_get(incoming, "headline") or "")
        title_b = normalize_title(candidate.get("headline") or "")
        if title_a and title_b:
            ratio = difflib.SequenceMatcher(None, title_a, title_b).ratio()
            if ratio >= w.get("title_similarity_cutoff", 0.7) and not subtype_conflict:
                score += w.get("title_similarity_high", 15)

        first_seen_a = _get(incoming, "first_seen")
        first_seen_b = candidate.get("first_seen_utc")
        if first_seen_a and first_seen_b:
            from event_store import parse_iso
            fb = parse_iso(first_seen_b) if isinstance(first_seen_b, str) else first_seen_b
            fa = first_seen_a
            if fa and fb:
                if fa.tzinfo is None:
                    fa = fa.replace(tzinfo=timezone.utc)
                if fb.tzinfo is None:
                    fb = fb.replace(tzinfo=timezone.utc)
                diff_hours = abs((fa - fb).total_seconds()) / 3600.0
                if diff_hours <= w.get("time_proximity_hours", 24):
                    score += w.get("time_proximity_bonus", 5)

        if subtype_conflict:
            # incident_subtype 明確衝突時，即使其他訊號堆疊也不應輕易穿過門檻，
            # 這裡不做 hard reject（畢竟同事件仍可能因報導錯誤出現子類型誤判），
            # 但保留 subtype_conflict 供上層診斷使用。
            pass

        return score

    # ── 主要進入點：Search Active Memory → Possible Match? ───────
    def find_match(self, incoming_event, store, now: Optional[datetime] = None):
        """
        回傳 (matched_event_id: Optional[str], signals: IdentitySignals)。
        matched_event_id 為 None 代表資料庫中找不到合理匹配，呼叫端應該
        視為 NEW EVENT，用 signals.canonical_key 產生新的 event_id。
        """
        now = now or datetime.now(timezone.utc)
        signals = self.identity.build_signals(incoming_event)

        # Fast path：canonical_key 完全相同（例如同一天、同船名、同 event_type）
        exact = store.get_event_by_canonical_key(signals.canonical_key)
        if exact is not None:
            score = self.pair_score(incoming_event, exact)
            if not self._is_hard_reject(score):
                self.diagnostics["canonical_key_fast_path_hits"] += 1
                return exact["event_id"], signals

        # Fuzzy path：在 event_type 對應的搜尋窗口內找 ACTIVE/MONITORING/RESOLVED 事件
        window_days = self.window_days_for(_get(incoming_event, "event_type"))
        candidates = store.get_candidate_events(now, window_days)

        best_id, best_score = None, 0.0
        for cand in candidates:
            if exact is not None and cand["event_id"] == exact["event_id"]:
                continue   # 已經在 fast path 評估過
            score = self.pair_score(incoming_event, cand)
            if self._is_hard_reject(score):
                continue
            if score > best_score:
                best_score, best_id = score, cand["event_id"]

        if best_id is not None and best_score >= self.threshold:
            return best_id, signals

        return None, signals
