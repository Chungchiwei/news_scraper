#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
operational_relevance.py
海事航運新聞監控系統 — Phase 6 Operational Relevance Engine 主體

職責：
  整合 fleet_relevance / port_relevance / route_relevance /
  geographic_relevance 四個子引擎，計算單一事件的 OperationalRelevance。

  ★ 核心原則（§三、一百零四）：
    EVENT RISK ≠ COMPANY EXPOSURE
    本模組完全不讀取、不修改 event.severity_score / management_priority /
    confidence_level / information_status 等 Phase 1-5 已經決定好的欄位
    語意——只讀取事件的『事實』欄位（vessel_name/imo_number/carrier/
    location/port/sea_area/shipping_lane/region/event_type）來做曝險
    比對，輸出完全獨立的 OperationalRelevance 物件。

  ★ Provider 失敗 ≠ NONE（§六十四〜六十五）：任何一個 Provider（Fleet/
    Schedule/Route）呼叫失敗，整次評估直接回傳 relevance_status=
    UNAVAILABLE、relevance_level=None，不猜測、不降級成『沒有曝險』。

  ★ Own Fleet 精確比對（IMO / 船名）視為 DIRECT 的『定義』本身
    （§六），不是純粹分數門檻的結果——即使該事件缺少其他佐證訊號，
    只要確認是本公司船舶，就一定是 DIRECT。這是 categorical override，
    在 _finalize() 裡明確處理，避免跟一般加權分數邏輯混淆。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from operational_models import (
    OperationalRelevance, RelevanceLevel, RelevanceStatus, ExposureType, AffectedVessel,
)
from fleet_relevance import FleetRelevanceEngine
from port_relevance import PortRelevanceEngine
from route_relevance import RouteRelevanceEngine
from geographic_relevance import GeographicRelevanceEngine, GlobalFleetRegulatoryEngine
from port_normalizer import PortNormalizer

logger = logging.getLogger(__name__)


class OperationalRelevanceEngine:
    def __init__(self, rules: dict, fleet_provider, schedule_provider, route_provider,
                 port_normalizer: Optional[PortNormalizer] = None,
                 own_fleet_carrier_key: str = "WAN_HAI"):
        self.rules = rules
        self.fleet_provider = fleet_provider
        self.schedule_provider = schedule_provider
        self.route_provider = route_provider

        normalizer = port_normalizer or PortNormalizer()
        self.fleet_engine = FleetRelevanceEngine(rules, own_fleet_carrier_key)
        self.port_engine = PortRelevanceEngine(rules, normalizer)
        self.route_engine = RouteRelevanceEngine(rules)
        self.geo_engine = GeographicRelevanceEngine(rules)
        self.global_fleet_engine = GlobalFleetRegulatoryEngine(rules)

        self.thresholds = rules.get("relevance_thresholds", {})
        self.freshness = rules.get("data_freshness", {})
        self.max_vessels_shown = rules.get("email_display", {}).get("max_affected_vessels_shown", 5)

        self.diagnostics = {
            "fleet_vessels_loaded": 0,
            "upcoming_port_calls_loaded": 0,
            "services_loaded": 0,
            "events_assessed": 0,
            "DIRECT": 0, "HIGH": 0, "MODERATE": 0, "LOW": 0, "NONE": 0,
            "UNAVAILABLE": 0,
            "affected_fleet_vessels": 0,
        }
        self._data_loaded = False
        self._load_error = False
        self._vessels: list = []
        self._port_calls: list = []
        self._services: list = []
        self._fleet_ts: Optional[datetime] = None
        self._schedule_ts: Optional[datetime] = None

    # ── Provider 資料載入（一次載入，供整個 run 重複使用）──────────
    def _load_data(self):
        if self._data_loaded or self._load_error:
            return
        try:
            self._vessels = self.fleet_provider.get_vessels()
            self._fleet_ts = self.fleet_provider.data_timestamp()
            self._port_calls = self.schedule_provider.get_port_calls()
            self._schedule_ts = self.schedule_provider.data_timestamp()
            self._services = self.route_provider.get_services()
            self._data_loaded = True
            self.diagnostics["fleet_vessels_loaded"] = len(self._vessels)
            self.diagnostics["upcoming_port_calls_loaded"] = len(self._port_calls)
            self.diagnostics["services_loaded"] = len(self._services)
        except Exception as e:
            # 只記類別/簡短訊息，不把整份船期/船隊資料印進 log（§九十四）。
            logger.warning(f"⚠️  Operational Relevance Provider 載入失敗（{type(e).__name__}），"
                            f"本次 run 全部事件標記為 UNAVAILABLE")
            self._load_error = True

    def _is_stale(self, now: datetime) -> bool:
        if self._schedule_ts is None:
            return False
        stale_after = self.freshness.get("schedule_stale_after_hours", 12)
        age_hours = (now - self._schedule_ts).total_seconds() / 3600.0
        return age_hours > stale_after

    def _level_for_score(self, score: float) -> str:
        t = self.thresholds
        if score >= t.get("DIRECT", 80):
            return RelevanceLevel.DIRECT
        if score >= t.get("HIGH", 60):
            return RelevanceLevel.HIGH
        if score >= t.get("MODERATE", 35):
            return RelevanceLevel.MODERATE
        if score >= t.get("LOW", 15):
            return RelevanceLevel.LOW
        return RelevanceLevel.NONE

    def assess(self, event, now: Optional[datetime] = None, run_id: Optional[str] = None) -> OperationalRelevance:
        now = now or datetime.now(timezone.utc)
        self._load_data()

        if self._load_error:
            self.diagnostics["events_assessed"] += 1
            self.diagnostics["UNAVAILABLE"] += 1
            return OperationalRelevance(
                event_id=event.event_id, relevance_level=None, relevance_score=None,
                relevance_status=RelevanceStatus.UNAVAILABLE,
                assessed_at=now, run_id=run_id,
            )

        fleet_result = self.fleet_engine.match(event, self._vessels)
        port_result = self.port_engine.assess(event, self._port_calls, now)
        route_result = self.route_engine.assess(event, self._services)
        geo_result = self.geo_engine.assess(event, self._services)
        global_result = self.global_fleet_engine.assess(event)

        score = (fleet_result.score + port_result.score + route_result.score
                 + geo_result.score + global_result.score)
        score = min(score, 100)

        # ── Categorical override：確認是本公司船舶 → 一律 DIRECT（§六）──
        if fleet_result.own_fleet_involved:
            level = RelevanceLevel.DIRECT
        else:
            level = self._level_for_score(score)

        exposure_types: list = []
        for r in (fleet_result, port_result, route_result, geo_result, global_result):
            for et in getattr(r, "exposure_types", []):
                if et not in exposure_types:
                    exposure_types.append(et)
        if fleet_result.match_type in ("IMO", "VESSEL_NAME") and ExposureType.OWN_VESSEL not in exposure_types:
            exposure_types.insert(0, ExposureType.OWN_VESSEL)

        reasons = list(fleet_result.reasons)
        affected_vessels: list = []
        affected_ports: list = []
        if port_result.matches:
            affected_ports.append(port_result.port_code)
            reasons.append(
                f"{len(port_result.matches)} fleet vessel(s) scheduled to call {port_result.port_code} "
                f"within {round(port_result.closest_eta_hours)}h (closest)"
            )
            for pc, hours in port_result.matches[: self.max_vessels_shown]:
                affected_vessels.append(AffectedVessel(
                    vessel_name=pc.vessel_name, service_code=pc.service_code,
                    next_port=pc.port_code, eta_display=pc.eta_utc.isoformat() if pc.eta_utc else None,
                    exposure_type=ExposureType.PORT_CALL, hours_to_exposure=round(hours, 1),
                ))

        affected_services: list = []
        if route_result.matched_services:
            affected_services.extend(s.service_code for s in route_result.matched_services)
            reasons.append(f"Service route includes affected sea area/shipping lane "
                            f"({', '.join(s.service_code for s in route_result.matched_services)})")
        if geo_result.matched_services:
            for s in geo_result.matched_services:
                if s.service_code not in affected_services:
                    affected_services.append(s.service_code)
            reasons.append("Service operates in the same broad region as the event")
        if global_result.exposure_types:
            reasons.append("Event is a regulatory development assessed as fleet-wide applicable")

        direct_match_count = 1 if fleet_result.own_fleet_involved else 0
        potential_match_count = len(affected_vessels) + (1 if fleet_result.match_type == "CARRIER_ONLY" else 0)

        is_stale = self._is_stale(now)
        status = RelevanceStatus.DATA_STALE if is_stale else RelevanceStatus.ASSESSED

        self.diagnostics["events_assessed"] += 1
        self.diagnostics[level] = self.diagnostics.get(level, 0) + 1
        if affected_vessels:
            self.diagnostics["affected_fleet_vessels"] += len(affected_vessels)

        return OperationalRelevance(
            event_id=event.event_id,
            relevance_level=level,
            relevance_score=score,
            relevance_status=status,
            own_fleet_involved=fleet_result.own_fleet_involved,
            affected_vessels=affected_vessels,
            affected_services=affected_services,
            affected_ports=affected_ports,
            exposure_types=exposure_types,
            closest_eta_hours=port_result.closest_eta_hours,
            direct_match_count=direct_match_count,
            potential_match_count=potential_match_count,
            relevance_reasons=reasons,
            data_timestamp=self._schedule_ts,
            is_stale=is_stale,
            assessed_at=now,
            run_id=run_id,
        )

    def diagnostics_report(self) -> str:
        d = self.diagnostics
        return (
            "OPERATIONAL RELEVANCE\n"
            f"  Fleet vessels loaded: {d['fleet_vessels_loaded']}\n"
            f"  Upcoming port calls: {d['upcoming_port_calls_loaded']}\n"
            f"  Services loaded: {d['services_loaded']}\n"
            f"  Events assessed: {d['events_assessed']}\n"
            f"  DIRECT: {d['DIRECT']}  HIGH: {d['HIGH']}  MODERATE: {d['MODERATE']}  "
            f"LOW: {d['LOW']}  NONE: {d['NONE']}  UNAVAILABLE: {d['UNAVAILABLE']}\n"
            f"  Affected fleet vessels: {d['affected_fleet_vessels']}"
        )
