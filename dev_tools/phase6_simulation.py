#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phase6_simulation.py
Phase 6 Completion Report — Three-Run Exposure Simulation（必要交付項目）。

不是 production 程式的一部分，是一次性驗證腳本：證明 Phase 6 的核心主張——

    EVENT UNCHANGED ≠ OPERATIONAL EXPOSURE UNCHANGED

同一個 MaritimeEvent（同一則新聞、Phase 1-5 判定的 severity/priority/
confidence/notification_state 全程完全不變）在三次 run 之間，只因為
「船期 ETA 逼近」與「船期資料更新」而讓 Operational Exposure 從
MODERATE → HIGH → CLEARED，且 Phase 6 的 operational_notification_state
（EXPOSURE_NEW / EXPOSURE_ESCALATED / EXPOSURE_CLEARED）是完全獨立於
Phase 3 NotificationState 的第二條時間軸。

使用暫存 SQLite（tempfile）+ FakeFleetProvider/FakeScheduleProvider/
FakeRouteProvider，不寫入 production database，不連任何真實系統。
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

# dev_tools/ 位於 repo root 下一層，往上一層才找得到 production 模組
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import (
    MaritimeEvent, NewsArticle, EventType,
    InformationStatus, ManagementPriority, NotificationState, ConfidenceLevel,
)
from operational_config import load_operational_rules
from operational_models import PortCall
from fleet_provider import FakeFleetProvider
from schedule_provider import FakeScheduleProvider
from route_provider import FakeRouteProvider
from operational_relevance import OperationalRelevanceEngine
from operational_history import OperationalHistoryStore, compute_operational_notification_state
from port_normalizer import PortNormalizer

import logging
logging.basicConfig(level=logging.WARNING, format="%(message)s")


def build_engine(rules, port_calls, now_ts):
    fp = FakeFleetProvider(vessels=[], data_timestamp=now_ts)
    sp = FakeScheduleProvider(port_calls=port_calls, data_timestamp=now_ts)
    rp = FakeRouteProvider(services=[], data_timestamp=now_ts)
    return OperationalRelevanceEngine(rules, fp, sp, rp, port_normalizer=PortNormalizer())


def make_sim_event(run_time: datetime) -> MaritimeEvent:
    """
    ★ 刻意在三次 run 之間回傳「完全相同」的事件內容（同一則新聞、
    同一個 headline/summary/priority/confidence/notification_state），
    模擬 Phase 3 判定這個事件本身維持 UNCHANGED——沒有新報導、沒有
    Material Update，主管在 Phase 1-5 的角度看不到任何變化。
    """
    a = NewsArticle(
        article_id="sim_a1", source_name="TradeWinds", source_tier="B",
        title="Kaohsiung terminal reports berth congestion after equipment failure",
        summary="Local terminal operators report a crane malfunction has caused "
                 "berth congestion at Kaohsiung port, with several vessels queuing offshore.",
        url="http://example.com/sim_a1",
        published_at=run_time - timedelta(hours=2), collected_at=run_time,
    )
    return MaritimeEvent(
        event_id="sim_kaohsiung_congestion",
        headline="Kaohsiung terminal reports berth congestion after equipment failure",
        event_type=EventType.OPERATIONS, incident_subtype="PORT_DISRUPTION",
        carrier=None, vessel_name=None, location="Kaohsiung", port="Kaohsiung",
        management_priority=ManagementPriority.P2, management_score=55,
        confidence_level=ConfidenceLevel.MEDIUM, information_status=InformationStatus.CORROBORATED,
        fleet_relevance_score=10, notification_state=NotificationState.UNCHANGED,
        change_reason=None, primary_article=a, articles=[a],
        article_count=1, independent_source_count=1,
        last_updated=run_time, impact_tags=["PORT", "SCHEDULE"],
    )


def print_run(label: str, run_time: datetime, event: MaritimeEvent, rel, op_state: str):
    print(f"\n########## {label} (run_time={run_time.strftime('%Y-%m-%d %H:%M UTC')}) ##########")
    print(f"  EVENT (Phase 1-5, unchanged across all 3 runs):")
    print(f"    event_id             = {event.event_id}")
    print(f"    headline             = {event.headline!r}")
    print(f"    management_priority  = {event.management_priority}")
    print(f"    confidence_level     = {event.confidence_level}")
    print(f"    notification_state   = {event.notification_state}   (Phase 3 axis)")
    print(f"  OPERATIONAL RELEVANCE (Phase 6, independent axis):")
    print(f"    relevance_level          = {rel.relevance_level}")
    print(f"    relevance_score          = {rel.relevance_score}")
    print(f"    relevance_status         = {rel.relevance_status}")
    print(f"    closest_eta_hours        = {rel.closest_eta_hours}")
    print(f"    affected_vessels         = {[v.vessel_name for v in rel.affected_vessels]}")
    print(f"    operational_notif_state  = {op_state}   ← Phase 6 axis, independent of notification_state above")


def main():
    rules = load_operational_rules()
    base = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)

    d = tempfile.mkdtemp()
    db_path = os.path.join(d, "phase6_simulation.db")
    store = OperationalHistoryStore(db_path)
    print(f"Using temporary Operational Relevance history database: {db_path}")

    # ── Run 1 (08:00): Port Call ETA 96h out → MODERATE ────────────
    t1 = base
    e1 = make_sim_event(t1)
    pc1 = PortCall(vessel_id="WH510", vessel_name="WAN HAI 510", service_code="AEX1",
                    port_code="TWKHH", eta_utc=t1 + timedelta(hours=96))
    engine1 = build_engine(rules, [pc1], t1)
    rel1 = engine1.assess(e1, now=t1, run_id="sim_run_1")
    prev1 = store.get_latest(e1.event_id)
    state1 = compute_operational_notification_state(prev1, rel1)
    store.save_snapshot(rel1)
    print_run("RUN 1 — 08:00 (ETA 96h)", t1, e1, rel1, state1)
    assert rel1.relevance_level == "MODERATE"
    assert state1 == "EXPOSURE_NEW"

    # ── Run 2 (12:00): same event (still UNCHANGED per Phase 3),
    #    but WAN HAI 510's ETA has closed to 40h → HIGH, ESCALATED ──
    t2 = base + timedelta(hours=4)
    e2 = make_sim_event(t2)
    pc2 = PortCall(vessel_id="WH510", vessel_name="WAN HAI 510", service_code="AEX1",
                    port_code="TWKHH", eta_utc=t2 + timedelta(hours=40))
    engine2 = build_engine(rules, [pc2], t2)
    rel2 = engine2.assess(e2, now=t2, run_id="sim_run_2")
    prev2 = store.get_latest(e2.event_id)
    state2 = compute_operational_notification_state(prev2, rel2)
    store.save_snapshot(rel2)
    print_run("RUN 2 — 12:00 (ETA 40h, event itself still UNCHANGED)", t2, e2, rel2, state2)
    assert e2.notification_state == e1.notification_state == "UNCHANGED"
    assert rel2.relevance_level == "HIGH"
    assert state2 == "EXPOSURE_ESCALATED"

    # ── Run 3 (18:00): schedule updated, WAN HAI 510's Kaohsiung call
    #    removed (vessel no longer scheduled) → NONE, CLEARED ───────
    t3 = base + timedelta(hours=10)
    e3 = make_sim_event(t3)
    engine3 = build_engine(rules, [], t3)   # port call removed from schedule data
    rel3 = engine3.assess(e3, now=t3, run_id="sim_run_3")
    prev3 = store.get_latest(e3.event_id)
    state3 = compute_operational_notification_state(prev3, rel3)
    store.save_snapshot(rel3)
    print_run("RUN 3 — 18:00 (port call removed from schedule)", t3, e3, rel3, state3)
    assert e3.notification_state == "UNCHANGED"
    assert rel3.relevance_level == "NONE"
    assert state3 == "EXPOSURE_CLEARED"

    store.close()

    print("\n\n========== SUMMARY ==========")
    print("Event Phase 3 notification_state across all 3 runs: "
          f"{e1.notification_state} → {e2.notification_state} → {e3.notification_state}  (unchanged throughout)")
    print(f"Operational relevance_level across all 3 runs:        "
          f"{rel1.relevance_level} → {rel2.relevance_level} → {rel3.relevance_level}")
    print(f"Operational notification_state across all 3 runs:     "
          f"{state1} → {state2} → {state3}")
    print("\nProof point: the event itself never changed (no new article, no Material Update),")
    print("yet Operational Exposure independently escalated (approaching ETA) and then cleared")
    print("(schedule update) — confirming EVENT RISK ≠ COMPANY EXPOSURE and")
    print("EVENT UNCHANGED ≠ OPERATIONAL EXPOSURE UNCHANGED.")
    print("\nSimulation complete — all assertions passed.")


if __name__ == "__main__":
    main()
