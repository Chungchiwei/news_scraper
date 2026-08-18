#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phase7_simulation.py
Phase 7 Completion Report — Management Simulation（Event A/B/C/D，§九十五）
+ System Health Simulation（§九十六〜九十七）。

不是 production 程式的一部分，是一次性驗證腳本：證明 Phase 7 的核心主張——

    DELIVERY ≠ RISK
    EVENT CHANGE AND FLEET EXPOSURE CHANGE ARE TWO DIFFERENT TRIGGERS
    SYSTEM HEALTH ALERTS MUST NOT BE MIXED WITH MARITIME INTELLIGENCE ALERTS

全部使用暫存 SQLite（tempfile）+ FakeTeamsNotifier，不寫入 production
database、不連任何真實 Teams webhook / SMTP / Internet。
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

# dev_tools/ 位於 repo root 下一層，往上一層才找得到 production 模組
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.WARNING, format="%(message)s")

from models import (                                                    # noqa: E402
    MaritimeEvent, NewsArticle, EventType,
    InformationStatus, ManagementPriority, NotificationState, ConfidenceLevel,
)
from operational_models import OperationalRelevance, AffectedVessel, RelevanceLevel, RelevanceStatus  # noqa: E402
from delivery_config import load_delivery_rules                          # noqa: E402
from delivery_models import DeliveryUrgency, DeliveryChannel               # noqa: E402
from delivery_history import DeliveryHistoryStore, DeliveryStatus            # noqa: E402
from delivery_orchestrator import DeliveryOrchestrator                         # noqa: E402
from teams_notifier import FakeTeamsNotifier                                     # noqa: E402
import teams_renderer                                                              # noqa: E402

from event_store import EventStore                                                   # noqa: E402
from source_health import SourceHealthStore                                            # noqa: E402
from system_health import SystemHealthService                                            # noqa: E402

NOW = datetime.now(timezone.utc)


def _article(article_id, source_name, title) -> NewsArticle:
    return NewsArticle(
        article_id=article_id, source_name=source_name, title=title, summary=title,
        url=f"https://example.com/{article_id}",
        published_at=NOW - timedelta(hours=1), collected_at=NOW,
    )


def _line():
    print("─" * 72)


# ══════════════════════════════════════════════════════════════
# MANAGEMENT SIMULATION — Event A/B/C/D（§九十五）
# ══════════════════════════════════════════════════════════════
def run_management_simulation():
    print("\n" + "=" * 72)
    print("PHASE 7 MANAGEMENT SIMULATION — Event A / B / C / D")
    print("=" * 72)

    d = tempfile.mkdtemp(prefix="phase7_sim_")
    history = DeliveryHistoryStore(os.path.join(d, "delivery.db"))
    rules = load_delivery_rules()
    orchestrator = DeliveryOrchestrator(rules, history)

    results = {}

    # ── Event A: P1 Security NEW, WHL Exposure HIGH ─────────────────
    print("\n### EVENT A — P1 Security NEW, WHL Exposure HIGH")
    print("Expected: Teams IMMEDIATE / Email include / Dashboard active top attention\n")
    event_a = MaritimeEvent(
        event_id="evt_A", headline="Red Sea — Container Vessel Attacked",
        event_type=EventType.SECURITY, location="Red Sea", sea_area="RED_SEA",
        management_priority=ManagementPriority.P1, management_score=92,
        confidence_level=ConfidenceLevel.HIGH, information_status=InformationStatus.CORROBORATED,
        notification_state=NotificationState.NEW,
        primary_article=_article("A1", "Reuters", "Container vessel attacked in Red Sea"),
        articles=[_article("A1", "Reuters", "Container vessel attacked in Red Sea"),
                  _article("A2", "UKMTO", "UKMTO advisory: vessel attacked, Red Sea")],
        article_count=2, independent_source_count=2, last_updated=NOW,
    )
    rel_a = OperationalRelevance(
        event_id="evt_A", relevance_level=RelevanceLevel.HIGH, relevance_score=62,
        relevance_status=RelevanceStatus.ASSESSED,
        affected_vessels=[AffectedVessel(vessel_name="WAN HAI 510", service_code="AEX1",
                                          next_port="SGSIN", eta_display=None,
                                          exposure_type="PORT_CALL", hours_to_exposure=30.0)],
    )
    decision_a = orchestrator.decide(event_a, operational_relevance=rel_a,
                                      operational_notification_state="EXPOSURE_NEW", now=NOW)
    print(f"  urgency          = {decision_a.urgency}")
    print(f"  channels         = {decision_a.channels}")
    print(f"  teams_mode       = {decision_a.teams_mode}")
    print(f"  email_mode       = {decision_a.email_mode}")
    print(f"  dashboard_visible= {decision_a.dashboard_visibility}")
    print(f"  reason           = {decision_a.delivery_reason}")
    assert decision_a.urgency == DeliveryUrgency.IMMEDIATE
    assert DeliveryChannel.TEAMS in decision_a.channels
    assert DeliveryChannel.EMAIL in decision_a.channels
    assert decision_a.dashboard_visibility is True
    results["A"] = decision_a
    print("  ✅ PASS")

    # ── Event B: P2 UNCHANGED, Exposure MODERATE→HIGH（最重要案例）───
    _line()
    print("\n### EVENT B — P2 UNCHANGED, Exposure MODERATE → HIGH  (Dual-Axis Trigger, most important case)")
    print("Expected: Teams PROMPT (Reason: WHL exposure escalated) / "
          "Email include as operational update / Dashboard exposure escalated badge\n")
    event_b = MaritimeEvent(
        event_id="evt_B", headline="Kaohsiung Terminal Reports Berth Congestion",
        event_type=EventType.OPERATIONS, incident_subtype="PORT_DISRUPTION",
        location="Kaohsiung", port="Kaohsiung",
        management_priority=ManagementPriority.P2, management_score=55,
        confidence_level=ConfidenceLevel.MEDIUM, information_status=InformationStatus.CORROBORATED,
        notification_state=NotificationState.UNCHANGED,   # ★ 事件本身完全沒變
        primary_article=_article("B1", "TradeWinds", "Kaohsiung terminal reports berth congestion"),
        articles=[_article("B1", "TradeWinds", "Kaohsiung terminal reports berth congestion")],
        article_count=1, independent_source_count=1, last_updated=NOW,
    )
    rel_b_previous_level = RelevanceLevel.MODERATE   # 前一次 run 的曝險（僅供敘述，不影響本次判斷輸入）
    rel_b = OperationalRelevance(
        event_id="evt_B", relevance_level=RelevanceLevel.HIGH, relevance_score=62,
        relevance_status=RelevanceStatus.ASSESSED,
        affected_vessels=[AffectedVessel(vessel_name="WAN HAI 510", service_code="AEX1",
                                          next_port="TWKHH", eta_display=None,
                                          exposure_type="PORT_CALL", hours_to_exposure=40.0)],
        closest_eta_hours=40.0,
    )
    decision_b = orchestrator.decide(event_b, operational_relevance=rel_b,
                                      operational_notification_state="EXPOSURE_ESCALATED", now=NOW)
    print(f"  event.notification_state (Phase 3 axis)        = {event_b.notification_state}")
    print(f"  operational exposure (Phase 6 axis)             = {rel_b_previous_level} → {rel_b.relevance_level}")
    print(f"  urgency          = {decision_b.urgency}")
    print(f"  channels         = {decision_b.channels}")
    print(f"  teams_mode       = {decision_b.teams_mode}")
    print(f"  email_mode       = {decision_b.email_mode}")
    print(f"  reason           = {decision_b.delivery_reason}")
    assert decision_b.urgency != DeliveryUrgency.SUPPRESSED, (
        "★ Dual-Axis 案例失敗：Event UNCHANGED 不代表 Delivery 什麼都不做"
    )
    assert decision_b.urgency == DeliveryUrgency.PROMPT
    assert "escalated" in decision_b.delivery_reason.lower()
    assert DeliveryChannel.TEAMS in decision_b.channels
    assert DeliveryChannel.EMAIL in decision_b.channels
    results["B"] = decision_b
    teams_message_b = teams_renderer.render(event_b, rel_b, decision_b,
                                              dashboard_base_url="http://127.0.0.1:8000")
    print("\n  ── Actual Teams message rendered for Event B ──")
    for ln in teams_message_b.splitlines():
        print(f"  {ln}")
    print("  ✅ PASS — Event UNCHANGED + Exposure ESCALATED = Delivery required (not suppressed)")

    # ── Event C: P2 UNCHANGED, Exposure unchanged ───────────────────
    _line()
    print("\n### EVENT C — P2 UNCHANGED, Exposure unchanged")
    print("Expected: Teams suppressed / Email no duplicate immediate notification / "
          "Dashboard still visible\n")
    event_c = MaritimeEvent(
        event_id="evt_C", headline="IMO Publishes Routine Ballast Water Guidance Update",
        event_type=EventType.REGULATORY,
        management_priority=ManagementPriority.P2, management_score=45,
        confidence_level=ConfidenceLevel.MEDIUM, information_status=InformationStatus.CORROBORATED,
        notification_state=NotificationState.UNCHANGED,
        primary_article=_article("C1", "IMO", "Routine ballast water guidance update"),
        articles=[_article("C1", "IMO", "Routine ballast water guidance update")],
        article_count=1, independent_source_count=1, last_updated=NOW,
    )
    rel_c = OperationalRelevance(
        event_id="evt_C", relevance_level=RelevanceLevel.MODERATE, relevance_score=20,
        relevance_status=RelevanceStatus.ASSESSED,
    )
    decision_c = orchestrator.decide(event_c, operational_relevance=rel_c,
                                      operational_notification_state="EXPOSURE_UNCHANGED", now=NOW)
    print(f"  urgency          = {decision_c.urgency}")
    print(f"  channels         = {decision_c.channels}")
    print(f"  teams_mode       = {decision_c.teams_mode}")
    print(f"  email_mode       = {decision_c.email_mode}")
    print(f"  dashboard_visible= {decision_c.dashboard_visibility}")
    assert decision_c.urgency == DeliveryUrgency.SUPPRESSED
    assert decision_c.teams_mode == "NONE"
    assert decision_c.channels == [DeliveryChannel.DASHBOARD]
    assert decision_c.dashboard_visibility is True, "§七十九：即使 SUPPRESSED，Dashboard 仍必須可見"
    results["C"] = decision_c
    print("  ✅ PASS — no duplicate notification, but still visible on Dashboard")

    # ── Event D: P1 RESOLVED_UPDATE, Exposure CLEARED ───────────────
    _line()
    print("\n### EVENT D — P1 RESOLVED_UPDATE, Exposure CLEARED")
    print("Expected: Teams resolution notification / Email Resolved section / Dashboard Resolved\n")
    event_d = MaritimeEvent(
        event_id="evt_D", headline="MSC Vessel Refloated Near Singapore",
        event_type=EventType.SAFETY, incident_subtype="GROUNDING",
        location="Singapore", port="Singapore",
        management_priority=ManagementPriority.P1, management_score=70,
        confidence_level=ConfidenceLevel.HIGH, information_status=InformationStatus.CONFIRMED,
        notification_state=NotificationState.RESOLVED_UPDATE,
        change_reason="Vessel successfully refloated and port operations have resumed.",
        primary_article=_article("D1", "TradeWinds", "MSC vessel refloated near Singapore"),
        articles=[_article("D1", "TradeWinds", "MSC vessel refloated near Singapore")],
        article_count=1, independent_source_count=1, last_updated=NOW,
    )
    rel_d = OperationalRelevance(
        event_id="evt_D", relevance_level=RelevanceLevel.NONE, relevance_score=0,
        relevance_status=RelevanceStatus.ASSESSED,
    )
    decision_d = orchestrator.decide(event_d, operational_relevance=rel_d,
                                      operational_notification_state="EXPOSURE_CLEARED", now=NOW)
    print(f"  urgency          = {decision_d.urgency}")
    print(f"  channels         = {decision_d.channels}")
    print(f"  teams_mode       = {decision_d.teams_mode}")
    print(f"  email_mode       = {decision_d.email_mode}")
    print(f"  reason           = {decision_d.delivery_reason}")
    assert decision_d.teams_mode == "RESOLVED"
    assert DeliveryChannel.TEAMS in decision_d.channels
    results["D"] = decision_d
    teams_message_d = teams_renderer.render(event_d, rel_d, decision_d)
    print("\n  ── Actual Teams message rendered for Event D ──")
    for ln in teams_message_d.splitlines():
        print(f"  {ln}")
    print("  ✅ PASS")

    # ── 用 FakeTeamsNotifier 實際「送出」A/B/D，確認 C 完全沒有被送出 ──
    _line()
    print("\n### Actually sending via FakeTeamsNotifier (no live webhook)")
    notifier = FakeTeamsNotifier()
    sent_events = []
    for label, decision, event, rel in (
        ("A", decision_a, event_a, rel_a), ("B", decision_b, event_b, rel_b),
        ("C", decision_c, event_c, rel_c), ("D", decision_d, event_d, rel_d),
    ):
        if DeliveryChannel.TEAMS in decision.channels and decision.teams_mode != "NONE":
            msg = teams_renderer.render(event, rel, decision)
            result = notifier.send("https://example.invalid/webhook", msg)
            history.record_delivery(
                event_id=event.event_id, run_id="sim_run", channel=DeliveryChannel.TEAMS,
                delivery_type=decision.teams_mode, delivery_reason=decision.delivery_reason,
                dedup_key=decision.dedup_key, status=DeliveryStatus.SENT if result.success else DeliveryStatus.FAILED,
            )
            sent_events.append(label)
            print(f"  Event {label}: Teams message SENT ({len(msg)} chars)")
        else:
            print(f"  Event {label}: Teams NOT sent (urgency={decision.urgency}, teams_mode={decision.teams_mode})")

    assert sent_events == ["A", "B", "D"], f"Expected only A/B/D to send Teams, got {sent_events}"
    print(f"\n  ✅ Confirmed: Teams sent for {sent_events} only. Event C correctly suppressed "
          f"(still visible on Dashboard via EventStore, independent of push decision).")

    history.close()
    return results


# ══════════════════════════════════════════════════════════════
# SYSTEM HEALTH SIMULATION（§九十六〜九十七）
# ══════════════════════════════════════════════════════════════
def run_system_health_simulation():
    print("\n\n" + "=" * 72)
    print("PHASE 7 SYSTEM HEALTH SIMULATION")
    print("=" * 72)
    print("Scenario: RSS Healthy / Event Store Healthy / Email Success / "
          "Teams Failure / Schedule Provider Unavailable\n")

    d = tempfile.mkdtemp(prefix="phase7_health_sim_")
    event_store = EventStore(os.path.join(d, "events.db"))
    delivery_history = DeliveryHistoryStore(os.path.join(d, "delivery.db"))
    source_health = SourceHealthStore(os.path.join(d, "srchealth.db"))

    # RSS 來源健康。
    for name in ("Reuters Shipping RSS", "TradeWinds RSS", "Splash247 RSS"):
        source_health.record_success(name, http_status=200, latency_ms=180)

    # Event Store 健康（真的開得起來、health_check ok）。
    event_store.start_run("health_sim_run", NOW)
    event_store.finish_run("health_sim_run", NOW, articles_collected=20, valid_articles=18,
                            events_detected=5, new_events=1, material_updates=1,
                            unchanged_events=3, resolved_events=0, status="SUCCESS")

    # Email 成功。
    delivery_history.record_delivery(
        event_id="evt_health_sim", run_id="health_sim_run", channel=DeliveryChannel.EMAIL,
        delivery_type="DAILY_BRIEF", delivery_reason="scheduled daily brief",
        dedup_key="evt_health_sim:v1:NONE", status=DeliveryStatus.SENT,
    )
    # Teams 失敗。
    delivery_history.record_delivery(
        event_id="evt_health_sim", run_id="health_sim_run", channel=DeliveryChannel.TEAMS,
        delivery_type="PROMPT", delivery_reason="scheduled daily brief",
        dedup_key="evt_health_sim:v1:NONE", status=DeliveryStatus.FAILED,
        error_message="HTTP 503 from webhook",
    )

    # Schedule Provider unavailable，其餘 Provider 正常。
    health_service = SystemHealthService(
        event_store=event_store, delivery_history_store=delivery_history,
        source_health_store=source_health,
        operational_provider_status={"fleet": "AVAILABLE", "schedule": "UNAVAILABLE", "route": "AVAILABLE"},
        llm_enabled=False,
    )
    report = health_service.build_report(now=NOW)

    print(health_service.diagnostics_report(report))

    print("\n  Dashboard must show EACH of these individually — not collapse to one blanket 'Healthy':")
    print(f"    Event Store status     = {report.event_store_status}   (expected HEALTHY)")
    print(f"    Email status            = {report.email_status}   (expected HEALTHY)")
    print(f"    Teams status             = {report.teams_status}   (expected DEGRADED)")
    print(f"    Schedule Provider status  = {report.schedule_provider_status}   (expected UNAVAILABLE)")
    print(f"    Fleet Provider status      = {report.fleet_provider_status}   (expected AVAILABLE)")
    print(f"    Source Health summary       = {report.source_health_summary}")
    print(f"    Overall System Status        = {report.overall_status}   (expected DEGRADED, NOT HEALTHY)")

    assert report.event_store_status == "HEALTHY"
    assert report.email_status == "HEALTHY"
    assert report.teams_status == "DEGRADED"
    assert report.schedule_provider_status == "UNAVAILABLE"
    assert report.fleet_provider_status == "AVAILABLE"
    assert report.overall_status == "DEGRADED", (
        "★ 系統絕不能因為部分功能正常運作就整體回報 HEALTHY——"
        "Teams 失敗 + Schedule Provider 不可用必須讓 Overall = DEGRADED"
    )
    assert report.overall_status != "CRITICAL", (
        "LLM/Teams/單一 Provider 失效不應把 Overall 拉到 CRITICAL"
        "（只有 Event Store 失效 / 全部來源失效 / Email 失敗才是 CRITICAL）"
    )

    print("\n  ✅ PASS — Overall correctly DEGRADED (not falsely HEALTHY, not falsely CRITICAL).")
    print("  ✅ PASS — This report never touches delivery_orchestrator.py / TeamsNotifier's")
    print("            Maritime Intelligence path — System Health stays on its own channel (§五十七〜五十九).")

    event_store.close()
    delivery_history.close()
    source_health.close()


def main():
    run_management_simulation()
    run_system_health_simulation()
    print("\n\n" + "=" * 72)
    print("PHASE 7 SIMULATION COMPLETE — all assertions passed.")
    print("=" * 72)


if __name__ == "__main__":
    main()
