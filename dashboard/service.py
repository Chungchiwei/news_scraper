#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard/service.py
海事航運新聞監控系統 — Phase 7 §六十一〜六十七 Dashboard Data Service

職責：
  統一查詢 Event Store（Phase 3）/ Operational History（Phase 6）/
  Delivery History（Phase 7）/ Source Health（Phase 7），供 Jinja2
  template 與 internal API 使用。

  ★ Dashboard Read-Only（§三十五、六十一〜六十二）：本檔案只有 SELECT，
  沒有任何 UPDATE/INSERT/DELETE——不讓主管透過 Dashboard 修改
  Risk Score / Priority / Confidence / Event Status。Jinja template
  不直接下 SQL（§六十一），全部經過這裡。
  ★ DB Locked ≠ Crash（§六十二）：任何查詢遇到 sqlite3 lock/操作錯誤，
  回傳「temporary unavailable」語意的結果，不拋出例外炸掉整個頁面，
  更不能因為 Dashboard 讀取而影響 production collector 的寫入。
  ★ Provider Unavailable ≠ 0（§六十七）：Fleet/Schedule/Route Provider
  真的不可用時，回傳的欄位是 "UNAVAILABLE" 字串，不是空 list 或 0。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

from management_summary import ManagementSummaryBuilder

logger = logging.getLogger(__name__)

_SUMMARY_BUILDER = None


def _summary_builder() -> ManagementSummaryBuilder:
    """
    延遲建立、行程內共用一份（跟 risk_rules.json 一樣是唯讀設定，不需要
    每次查詢都重新載入）。§四十四：Dashboard Event Detail 頁的 Management
    Summary / Why It Matters / What Changed 沿用 Phase 4 既有的 Rule-Based
    文字生成規則，不重新發明一套——AI Enhanced 文字目前不持久化到 DB，
    Dashboard 顯示的是保底的 Rule-Based 版本（見 get_event_detail()）。
    """
    global _SUMMARY_BUILDER
    if _SUMMARY_BUILDER is None:
        _SUMMARY_BUILDER = ManagementSummaryBuilder()
    return _SUMMARY_BUILDER


def _row_as_event_like(row: dict) -> SimpleNamespace:
    """
    把 EventStore 的 raw row dict 轉成 ManagementSummaryBuilder 期待的
    唯讀屬性物件——這些方法只做 getattr 存取（carrier/location/port/
    vessel_name/incident_subtype/event_type/各 operational status/
    information_status/change_reason），不依賴 MaritimeEvent 的
    articles/primary_article，所以不需要重建完整 dataclass。
    """
    return SimpleNamespace(
        carrier=row.get("carrier"), location=row.get("location"), port=row.get("port"),
        vessel_name=row.get("vessel_name"), vessel_type=row.get("vessel_type"),
        incident_subtype=row.get("incident_subtype"), event_type=row.get("event_type"),
        casualty_status=row.get("casualty_status"), fire_status=row.get("fire_status"),
        vessel_status=row.get("vessel_status"), port_status=row.get("port_status"),
        navigation_status=row.get("navigation_status"), pollution_status=row.get("pollution_status"),
        information_status=row.get("information_status"), change_reason=row.get("change_reason"),
    )

_PRIORITY_RANK = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
_RELEVANCE_RANK = {"DIRECT": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3, "NONE": 4, None: 5}


class DashboardService:
    def __init__(self, event_store, operational_history, delivery_history,
                 source_health_store, health_service):
        self.event_store = event_store
        self.operational_history = operational_history
        self.delivery_history = delivery_history
        self.source_health_store = source_health_store
        self.health_service = health_service

    # ── 安全查詢包裝（§六十二）───────────────────────────────────
    def _safe(self, fn, default):
        try:
            return fn()
        except sqlite3.Error as e:
            logger.warning(f"⚠️  Dashboard 查詢暫時無法使用（{type(e).__name__}）：{e}")
            return default
        except Exception as e:
            logger.warning(f"⚠️  Dashboard 查詢發生非預期錯誤（{type(e).__name__}）：{e}")
            return default

    # ── 內部：把單一 event row 疊上最新 Operational Relevance 快照 ──
    def _enrich(self, event_row: dict) -> dict:
        snapshot = self._safe(
            lambda: self.operational_history.get_latest(event_row["event_id"]), None
        )
        row = dict(event_row)
        if snapshot is None:
            row["relevance_level"] = None
            row["relevance_status"] = "NOT_ASSESSED"
            row["affected_vessels"] = []
            row["affected_services"] = []
            row["affected_ports"] = []
        else:
            row["relevance_level"] = snapshot.get("relevance_level")
            row["relevance_status"] = snapshot.get("relevance_status")
            row["affected_vessels"] = snapshot.get("affected_vessels", [])
            row["affected_services"] = snapshot.get("affected_services", [])
            row["affected_ports"] = snapshot.get("affected_ports", [])
        return row

    def _active_events_enriched(self) -> list:
        rows = self._safe(lambda: self.event_store.get_active_or_monitoring(), [])
        return [self._enrich(r) for r in rows]

    # ══════════════════════════════════════════════════════════
    # Overview（§三十六〜三十九）
    # ══════════════════════════════════════════════════════════
    def overview(self, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(timezone.utc)
        events = self._active_events_enriched()

        p1 = [e for e in events if e.get("management_priority") == "P1"]
        p2 = [e for e in events if e.get("management_priority") == "P2"]
        p3 = [e for e in events if e.get("management_priority") == "P3"]

        direct = [e for e in events if e.get("relevance_level") == "DIRECT"]
        high = [e for e in events if e.get("relevance_level") == "HIGH"]

        affected_vessel_names: set = set()
        affected_port_codes: set = set()
        for e in events:
            for v in e.get("affected_vessels") or []:
                if v.get("vessel_name"):
                    affected_vessel_names.add(v["vessel_name"])
            for p in e.get("affected_ports") or []:
                affected_port_codes.add(p)

        new_count = len([e for e in events if e.get("notification_state") == "NEW"])
        material_count = len([e for e in events if e.get("notification_state") == "MATERIAL_UPDATE"])

        resolved_cutoff = now - timedelta(days=7)
        resolved_events = self._safe(
            lambda: [r for r in self._all_events_by_status(("RESOLVED",))
                     if self._parse_ts(r.get("last_seen_utc")) and self._parse_ts(r.get("last_seen_utc")) >= resolved_cutoff],
            [],
        )

        if p1:
            situation = "HIGH"
        elif p2:
            situation = "ELEVATED"
        elif p3:
            situation = "WATCH"
        else:
            situation = "NORMAL"

        top_attention = self._sort_top_attention(events)[:5]

        return {
            "generated_at": now,
            "overall_situation": situation,
            "active_p1": len(p1),
            "active_p2": len(p2),
            "active_p3": len(p3),
            "direct_exposure": len(direct),
            "high_exposure": len(high),
            "affected_vessel_count": len(affected_vessel_names),
            "affected_port_count": len(affected_port_codes),
            "new_events": new_count,
            "material_updates": material_count,
            "resolved_recent": len(resolved_events),
            "top_attention": top_attention,
        }

    def _all_events_by_status(self, statuses: tuple) -> list:
        # EventStore 沒有現成的『依狀態列全部』方法，這裡用 get_candidate_events
        # 的大窗口變體達到同樣效果（180 天內、指定狀態），避免另外改動
        # Phase 3 既有查詢介面。
        return self.event_store.get_candidate_events(
            datetime.now(timezone.utc), window_days=180, statuses=statuses
        )

    @staticmethod
    def _parse_ts(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _sort_top_attention(self, events: list) -> list:
        """
        §三十九：Dashboard 排序（不改變 Event Priority 本身，只是這個
        清單本身的顯示順序）——
        OWN FLEET → P1 → DIRECT → HIGH → P2 → Management Score → Last Material Update。
        """
        def key(e: dict):
            own_fleet = 0 if (e.get("carrier") == "WAN_HAI" and e.get("relevance_level") == "DIRECT") else 1
            priority_rank = _PRIORITY_RANK.get(e.get("management_priority"), 9)
            relevance_rank = _RELEVANCE_RANK.get(e.get("relevance_level"), 5)
            score = -(e.get("management_score") or 0)
            last_update_str = e.get("last_material_update_utc") or e.get("last_seen_utc")
            last_update = self._parse_ts(last_update_str)
            # 愈新愈優先 → 用負的 timestamp（epoch 秒）排序；缺值視為最舊。
            recency = -last_update.timestamp() if last_update else 0.0
            return (own_fleet, priority_rank, relevance_rank, score, recency)

        return sorted(events, key=key)

    # ══════════════════════════════════════════════════════════
    # Active Event Board（§四十〜四十二）
    # ══════════════════════════════════════════════════════════
    def list_events(self, *, priority: Optional[str] = None, event_type: Optional[str] = None,
                     event_status: Optional[str] = None, confidence: Optional[str] = None,
                     relevance_level: Optional[str] = None, region: Optional[str] = None,
                     own_fleet_only: bool = False, carrier: Optional[str] = None,
                     search: Optional[str] = None) -> list:
        events = self._active_events_enriched()

        def match(e: dict) -> bool:
            if priority and e.get("management_priority") != priority:
                return False
            if event_type and e.get("event_type") != event_type:
                return False
            if event_status and e.get("event_status") != event_status:
                return False
            if confidence and e.get("confidence_level") != confidence:
                return False
            if relevance_level and e.get("relevance_level") != relevance_level:
                return False
            if region and (e.get("region") != region and e.get("location") != region):
                return False
            if own_fleet_only and not (e.get("relevance_level") == "DIRECT"):
                return False
            if carrier and e.get("carrier") != carrier:
                return False
            if search:
                haystack = " ".join(str(e.get(f) or "") for f in
                                     ("vessel_name", "headline", "port", "carrier", "event_id")).lower()
                if search.lower() not in haystack:
                    return False
            return True

        filtered = [e for e in events if match(e)]
        return self._sort_top_attention(filtered)

    # ══════════════════════════════════════════════════════════
    # Event Detail（§四十三〜四十七、五十二）
    # ══════════════════════════════════════════════════════════
    def get_event_detail(self, event_id: str) -> Optional[dict]:
        row = self._safe(lambda: self.event_store.get_event(event_id), None)
        if row is None:
            return None
        row = self._enrich(row)

        event_history = self._safe(lambda: self.event_store.get_history(event_id), [])
        articles = self._safe(lambda: self.event_store.get_articles_for_event(event_id), [])

        operational_snapshot = self._safe(lambda: self.operational_history.get_latest(event_id), None)

        row["event_history"] = event_history
        row["articles"] = articles
        row["operational_snapshot"] = operational_snapshot

        # §四十四：Management Summary / Why It Matters / What Changed
        # （Rule-Based 保底版本；AI Enhanced 文字目前不持久化到 DB）。
        try:
            sb = _summary_builder()
            event_like = _row_as_event_like(row)
            row["management_headline"] = sb.management_headline(event_like)
            row["management_summary_zh"] = sb.management_summary(event_like)
            row["why_it_matters_zh"] = sb.why_it_matters(event_like)
            row["what_changed_zh"] = sb.what_changed(event_like)
        except Exception as e:
            logger.warning(f"⚠️  Dashboard Management Summary 產生失敗（{type(e).__name__}），改用空值")
            row["management_headline"] = row.get("headline")
            row["management_summary_zh"] = None
            row["why_it_matters_zh"] = None
            row["what_changed_zh"] = []

        return row

    # ══════════════════════════════════════════════════════════
    # Fleet Exposure View（§四十九〜五十：只顯示有 Operational Relevance 的船）
    # ══════════════════════════════════════════════════════════
    def fleet_exposure(self) -> list:
        events = self._active_events_enriched()
        by_vessel: dict = {}
        for e in events:
            for v in e.get("affected_vessels") or []:
                name = v.get("vessel_name")
                if not name:
                    continue
                entry = by_vessel.setdefault(name, {
                    "vessel_name": name, "service_code": v.get("service_code"),
                    "next_port": v.get("next_port"), "active_risk_events": [],
                    "highest_exposure": "NONE", "closest_risk_window_hours": None,
                })
                entry["active_risk_events"].append({
                    "event_id": e["event_id"], "headline": e.get("headline"),
                    "priority": e.get("management_priority"),
                })
                if _RELEVANCE_RANK.get(e.get("relevance_level"), 5) < _RELEVANCE_RANK.get(entry["highest_exposure"], 5):
                    entry["highest_exposure"] = e.get("relevance_level")
                hours = v.get("hours_to_exposure")
                if hours is not None and (entry["closest_risk_window_hours"] is None
                                           or hours < entry["closest_risk_window_hours"]):
                    entry["closest_risk_window_hours"] = hours
        return sorted(by_vessel.values(), key=lambda x: _RELEVANCE_RANK.get(x["highest_exposure"], 5))

    # ══════════════════════════════════════════════════════════
    # Port Exposure View（§四十八）
    # ══════════════════════════════════════════════════════════
    def port_exposure(self) -> list:
        events = self._active_events_enriched()
        by_port: dict = {}
        for e in events:
            for port_code in (e.get("affected_ports") or []):
                entry = by_port.setdefault(port_code, {
                    "port": port_code, "active_events": [], "highest_priority": None,
                    "affected_vessels": set(), "closest_eta_hours": None, "exposure_level": "NONE",
                })
                entry["active_events"].append(e["event_id"])
                if entry["highest_priority"] is None or _PRIORITY_RANK.get(e.get("management_priority"), 9) < \
                        _PRIORITY_RANK.get(entry["highest_priority"], 9):
                    entry["highest_priority"] = e.get("management_priority")
                for v in e.get("affected_vessels") or []:
                    if v.get("vessel_name"):
                        entry["affected_vessels"].add(v["vessel_name"])
                    hours = v.get("hours_to_exposure")
                    if hours is not None and (entry["closest_eta_hours"] is None or hours < entry["closest_eta_hours"]):
                        entry["closest_eta_hours"] = hours
                if _RELEVANCE_RANK.get(e.get("relevance_level"), 5) < _RELEVANCE_RANK.get(entry["exposure_level"], 5):
                    entry["exposure_level"] = e.get("relevance_level")
        result = []
        for entry in by_port.values():
            entry = dict(entry)
            entry["affected_vessels"] = sorted(entry["affected_vessels"])
            result.append(entry)
        return sorted(result, key=lambda x: _PRIORITY_RANK.get(x["highest_priority"], 9))

    # ══════════════════════════════════════════════════════════
    # Resolved View（§五十一）
    # ══════════════════════════════════════════════════════════
    def resolved_events(self, days: int = 7) -> list:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = self._safe(lambda: self._all_events_by_status(("RESOLVED",)), [])
        recent = [r for r in rows if self._parse_ts(r.get("last_seen_utc")) and
                  self._parse_ts(r.get("last_seen_utc")) >= cutoff]
        return [self._enrich(r) for r in recent]

    # ══════════════════════════════════════════════════════════
    # System Health（§五十三〜五十四）
    # ══════════════════════════════════════════════════════════
    def system_health(self):
        return self.health_service.build_report()
