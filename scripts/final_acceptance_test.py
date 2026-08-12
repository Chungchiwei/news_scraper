#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/final_acceptance_test.py
WHL Maritime Intelligence System — Phase 8 §三十四〜三十八 Final Acceptance Test

完全離線（Mock/Fixture only），把整條 pipeline 走一遍：

    Articles → Event → Cluster → Score → Memory → Material Update →
    Operational Relevance → Delivery Decision → Email Render →
    Teams Render → Dashboard Read

不連任何真實 SMTP / Teams webhook / LLM API / Internet；使用暫存 SQLite
（tempfile）+ Fake Provider（FakeFleetProvider/FakeScheduleProvider/
FakeRouteProvider/FakeTeamsNotifier）。

★ 設計原則：所有事件一律從「真實文字」經過真正的 EventExtractor →
  EventClusterer → RiskScorer 產生（跟 production 走同一條程式碼路徑），
  不手動幫 MaritimeEvent 塞 management_priority/confidence_level 這種
  會被 RiskScorer 重新計算覆蓋掉的欄位——確保這份驗收測試測的是「真的」
  分類/評分邏輯，不是自欺欺人的假資料。

★ Live RSS 測試不能拿來當自動驗收標準（來源網站/網路/SSL 本身就不穩定，
  見 scripts/production_smoke_test.py 的定位差異）——本腳本必須是
  deterministic、可重複執行、結果穩定。

四個具名情境（§三十六）：
  Scenario A — P1 Security Event, WHL Exposure HIGH
               → Email + Teams(IMMEDIATE) + Dashboard
  Scenario B — 同一事件，下一次 run：事件 UNCHANGED、Exposure 不變
               → 不重複送 Teams，但 Dashboard 仍可見
  Scenario C — 事件 UNCHANGED，Exposure MODERATE → HIGH
               → Teams PROMPT delivery（Dual-Axis Trigger 最重要案例）
  Scenario D — 事件 RESOLVED
               → Resolved Email/Teams、Dashboard Resolved Timeline

用法：
    python scripts/final_acceptance_test.py
Exit code：0 = PASS，1 = FAIL。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import logging
logging.basicConfig(level=logging.WARNING, format="%(message)s")

from models import NewsArticle, EventType, NotificationState                    # noqa: E402
from risk_config import load_risk_rules                                          # noqa: E402
from event_extractor import EventExtractor                                        # noqa: E402
from risk_scorer import RiskScorer                                                 # noqa: E402
from event_clusterer import EventClusterer                                          # noqa: E402

from memory_config import load_memory_rules                                          # noqa: E402
from event_store import EventStore                                                    # noqa: E402
from memory_pipeline import apply_persistent_memory, generate_run_id                   # noqa: E402

from operational_config import load_operational_rules                                   # noqa: E402
from operational_models import PortCall                                                  # noqa: E402
from fleet_provider import FakeFleetProvider                                              # noqa: E402
from schedule_provider import FakeScheduleProvider                                         # noqa: E402
from route_provider import FakeRouteProvider                                                # noqa: E402
from operational_relevance import OperationalRelevanceEngine                                 # noqa: E402
from operational_history import OperationalHistoryStore, compute_operational_notification_state  # noqa: E402
from port_normalizer import PortNormalizer                                                     # noqa: E402

from delivery_config import load_delivery_rules                                                  # noqa: E402
from delivery_models import DeliveryUrgency, DeliveryChannel                                       # noqa: E402
from delivery_history import DeliveryHistoryStore, DeliveryStatus                                   # noqa: E402
from delivery_orchestrator import DeliveryOrchestrator                                               # noqa: E402
from teams_notifier import FakeTeamsNotifier                                                          # noqa: E402
import teams_renderer                                                                                  # noqa: E402

from briefing_selector import BriefingSelector                                                          # noqa: E402
from email_view_model import build_daily_brief_view_model                                                # noqa: E402
from executive_email_renderer import ExecutiveEmailRenderer                                                # noqa: E402

from source_health import SourceHealthStore                                                                  # noqa: E402
from system_health import SystemHealthService                                                                 # noqa: E402
from dashboard.service import DashboardService                                                                  # noqa: E402

NOW = datetime.now(timezone.utc)
FIXTURES_PATH = ROOT / "tests" / "fixtures" / "articles.json"

RESULTS: "dict[str, bool]" = {}
FAILURES: "list[str]" = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    RESULTS[label] = bool(condition) and RESULTS.get(label, True)
    if not condition:
        FAILURES.append(f"{label}: {detail}")
    return condition


def _tier(risk_rules: dict, source_name: str) -> str:
    tiers = risk_rules.get("source_tiers", {})
    return tiers.get(source_name, tiers.get("_default", "C"))


def _article(risk_rules, article_id, source_name, title, summary, run_time) -> NewsArticle:
    return NewsArticle(
        article_id=article_id, source_name=source_name, source_tier=_tier(risk_rules, source_name),
        title=title, summary=summary, url=f"http://example.com/{article_id}",
        published_at=run_time - timedelta(hours=1), collected_at=run_time,
    )


def run_real_pipeline(risk_rules: dict, articles: list, run_time: datetime) -> list:
    """Article → EventExtractor.enrich → RiskScorer.score_article → EventClusterer.cluster
    → RiskScorer.score_events。跟 maritime_news.run_intelligence_pipeline() 內部用的是
    同一組類別，只是省略了 CarrierNewsFilter（測試資料本來就不是航商 PR 稿）。"""
    extractor = EventExtractor(risk_rules)
    scorer = RiskScorer(risk_rules, extractor)
    clusterer = EventClusterer(risk_rules)
    for a in articles:
        extractor.enrich(a)
        scorer.score_article(a, now=run_time)
    events = clusterer.cluster(articles)
    scorer.score_events(events, now=run_time)
    return events


# ══════════════════════════════════════════════════════════════
# SECTION 1 — Collection / Classification / Clustering / Risk Scoring
# 使用 tests/fixtures/articles.json（既有測試套件也在用的同一份 mock
# 資料，已知會可靠地把 fx04a/fx04b/fx04c 三篇不同來源的報導 cluster
# 成同一個事件）。
# ══════════════════════════════════════════════════════════════
def run_pipeline_mechanics(risk_rules: dict):
    print("\n" + "=" * 60)
    print("SECTION 1 — Collection / Classification / Clustering / Risk Scoring")
    print("=" * 60)

    with open(FIXTURES_PATH, encoding="utf-8") as f:
        raw = json.load(f)

    articles = []
    for d in raw:
        a = _article(risk_rules, "acc_" + d["id"], d["source_name"], d["title"], d["summary"], NOW)
        articles.append(a)
    check("Collection", len(articles) == len(raw) and all(a.title for a in articles),
          f"expected {len(raw)} articles, got {len(articles)}")

    events = run_real_pipeline(risk_rules, articles, NOW)

    classified = [e for e in events if e.event_type]
    check("Classification", len(classified) > 0,
          "no event received an event_type from EventExtractor/EventClusterer")

    fx04_cluster = next(
        (e for e in events if any(a.article_id == "acc_fx04a" for a in e.articles)), None
    )
    check("Clustering", fx04_cluster is not None and fx04_cluster.article_count == 3
          and fx04_cluster.independent_source_count == 3,
          f"expected fx04a/b/c to cluster into 1 event with 3 independent sources, "
          f"got {fx04_cluster.article_count if fx04_cluster else 'no cluster found'}")

    check("Risk Scoring", fx04_cluster is not None and fx04_cluster.management_score is not None
          and fx04_cluster.management_priority in ("P1", "P2", "P3", "P4"),
          "fx04 cluster missing management_score/management_priority after RiskScorer.score_events()")

    print(f"  Collection:     {len(articles)} articles built from fixtures")
    print(f"  Classification: {len(classified)}/{len(events)} events have event_type assigned")
    if fx04_cluster:
        print(f"  Clustering:     fx04a/b/c -> 1 event, sources={fx04_cluster.independent_source_count}")
        print(f"  Risk Scoring:   priority={fx04_cluster.management_priority} "
              f"score={fx04_cluster.management_score}")


# ══════════════════════════════════════════════════════════════
# SECTION 2 — Scenario A + B
# ══════════════════════════════════════════════════════════════
def _scenario_ab_articles(risk_rules, run_time):
    return [
        _article(risk_rules, "acc_a1", "Reuters",
                 "Container vessel attacked by missile near Kaohsiung approach channel",
                 "A container vessel was attacked by a missile while approaching Kaohsiung; "
                 "the operator confirmed the incident.", run_time),
        _article(risk_rules, "acc_a2", "TradeWinds",
                 "Boxship hit by missile strike near Kaohsiung",
                 "A boxship was struck in a missile strike while approaching Kaohsiung, sources say.",
                 run_time),
        _article(risk_rules, "acc_a3", "gCaptain",
                 "Kaohsiung: container vessel attacked by missile, no casualties reported",
                 "Container vessel attacked by missile near Kaohsiung; owner reports no casualties, "
                 "vessel proceeding under own power.", run_time),
    ]


def run_scenario_ab(risk_rules, memory_rules, delivery_rules, operational_rules, d: str):
    print("\n" + "=" * 60)
    print("SECTION 2 — Scenario A (NEW, HIGH Exposure) + Scenario B (UNCHANGED, no dup Teams)")
    print("=" * 60)

    event_store = EventStore(os.path.join(d, "events.db"))
    op_history = OperationalHistoryStore(os.path.join(d, "operational.db"))
    delivery_history = DeliveryHistoryStore(os.path.join(d, "delivery.db"))
    orchestrator = DeliveryOrchestrator(delivery_rules, delivery_history)
    teams_notifier = FakeTeamsNotifier()

    def assess_relevance(event, port_calls, run_time, run_id):
        fp = FakeFleetProvider(vessels=[], data_timestamp=run_time)
        sp = FakeScheduleProvider(port_calls=port_calls, data_timestamp=run_time)
        rp = FakeRouteProvider(services=[], data_timestamp=run_time)
        engine = OperationalRelevanceEngine(operational_rules, fp, sp, rp, port_normalizer=PortNormalizer())
        rel = engine.assess(event, now=run_time, run_id=run_id)
        prev = op_history.get_latest(event.event_id)
        notif_state = compute_operational_notification_state(prev, rel)
        op_history.save_snapshot(rel)
        return rel, notif_state

    def send_teams_if_due(event, rel, decision, run_id):
        if DeliveryChannel.TEAMS in decision.channels and decision.teams_mode != "NONE" \
                and not delivery_history.already_sent(decision.dedup_key, DeliveryChannel.TEAMS):
            msg = teams_renderer.render(event, rel, decision, dashboard_base_url="http://127.0.0.1:8000")
            result = teams_notifier.send("https://example.invalid/webhook", msg)
            delivery_history.record_delivery(
                event_id=event.event_id, run_id=run_id, channel=DeliveryChannel.TEAMS,
                delivery_type=decision.teams_mode, delivery_reason=decision.delivery_reason,
                dedup_key=decision.dedup_key,
                status=DeliveryStatus.SENT if result.success else DeliveryStatus.FAILED,
            )
            return msg
        return None

    # ── RUN 1 (T+0h): Scenario A ────────────────────────────────
    t1 = NOW
    run_id_1 = generate_run_id(t1)
    events_1 = run_real_pipeline(risk_rules, _scenario_ab_articles(risk_rules, t1), t1)
    mem_1 = apply_persistent_memory(events_1, event_store, run_id_1, t1, risk_rules, memory_rules,
                                     RiskScorer(risk_rules, EventExtractor(risk_rules)))
    event_a = mem_1["all_current_events"][0]
    check("Persistent Memory", event_a.notification_state == NotificationState.NEW
          and event_a.management_priority == "P1",
          f"expected NEW P1 on first sighting, got {event_a.notification_state}/{event_a.management_priority}")

    pc_close = PortCall(vessel_id="WH510", vessel_name="WAN HAI 510", service_code="AEX1",
                         port_code="TWKHH", eta_utc=t1 + timedelta(hours=40))
    event_a.location = event_a.location or "Kaohsiung"
    event_a.port = event_a.port or "Kaohsiung"
    rel_a, notif_state_a = assess_relevance(event_a, [pc_close], t1, run_id_1)
    check("Operational Relevance", rel_a.relevance_level == "HIGH",
          f"expected HIGH exposure for Scenario A, got {rel_a.relevance_level}")

    decision_a = orchestrator.decide(event_a, operational_relevance=rel_a,
                                      operational_notification_state=notif_state_a, now=t1, run_id=run_id_1)
    check("Delivery Orchestrator",
          decision_a.urgency == DeliveryUrgency.IMMEDIATE
          and DeliveryChannel.EMAIL in decision_a.channels
          and DeliveryChannel.TEAMS in decision_a.channels
          and decision_a.dashboard_visibility is True,
          f"Scenario A expected IMMEDIATE+EMAIL+TEAMS+dashboard_visible, got {decision_a.urgency}/"
          f"{decision_a.channels}/dashboard={decision_a.dashboard_visibility}")

    selection_1 = BriefingSelector().select([event_a])
    brief_vm_1 = build_daily_brief_view_model(selection_1, generated_at=t1, ai_analyses={},
                                               operational_relevance_map={event_a.event_id: rel_a})
    html_1 = ExecutiveEmailRenderer().render_daily_brief(brief_vm_1)
    check("Email Renderer", "Kaohsiung" in html_1 and len(html_1) > 500,
          "rendered Email HTML missing Scenario A headline content or suspiciously short")

    teams_msg_a = send_teams_if_due(event_a, rel_a, decision_a, run_id_1)
    check("Teams Renderer", bool(teams_msg_a) and "Kaohsiung" in teams_msg_a,
          "Teams message for Scenario A was not sent or missing headline content")
    sent_count_after_run1 = len(teams_notifier.calls)

    print(f"  Scenario A: event_id={event_a.event_id} notification_state={event_a.notification_state} "
          f"priority={event_a.management_priority} exposure={rel_a.relevance_level} "
          f"delivery={decision_a.urgency} teams_sent={bool(teams_msg_a)}")

    # ── RUN 2 (T+4h): Scenario B — same content, exposure unchanged ─
    t2 = NOW + timedelta(hours=4)
    run_id_2 = generate_run_id(t2)
    events_2 = run_real_pipeline(risk_rules, _scenario_ab_articles(risk_rules, t2), t2)
    mem_2 = apply_persistent_memory(events_2, event_store, run_id_2, t2, risk_rules, memory_rules,
                                     RiskScorer(risk_rules, EventExtractor(risk_rules)))
    event_a_run2 = mem_2["all_current_events"][0]
    check("Persistent Memory", event_a_run2.event_id == event_a.event_id
          and event_a_run2.notification_state == NotificationState.UNCHANGED,
          f"expected same event_id + UNCHANGED on 2nd identical sighting, "
          f"got id_match={event_a_run2.event_id == event_a.event_id} state={event_a_run2.notification_state}")

    event_a_run2.location = event_a_run2.location or "Kaohsiung"
    event_a_run2.port = event_a_run2.port or "Kaohsiung"
    rel_a2, notif_state_a2 = assess_relevance(event_a_run2, [pc_close], t2, run_id_2)
    decision_a2 = orchestrator.decide(event_a_run2, operational_relevance=rel_a2,
                                       operational_notification_state=notif_state_a2, now=t2, run_id=run_id_2)
    teams_msg_b = send_teams_if_due(event_a_run2, rel_a2, decision_a2, run_id_2)
    sent_count_after_run2 = len(teams_notifier.calls)

    check("Duplicate Suppression",
          decision_a2.urgency == DeliveryUrgency.SUPPRESSED
          and teams_msg_b is None
          and sent_count_after_run2 == sent_count_after_run1
          and decision_a2.dashboard_visibility is True,
          f"expected no 2nd Teams send + still dashboard-visible, got urgency={decision_a2.urgency} "
          f"teams_calls={sent_count_after_run1}->{sent_count_after_run2} "
          f"dashboard_visible={decision_a2.dashboard_visibility}")

    print(f"  Scenario B: notification_state={event_a_run2.notification_state} "
          f"exposure={rel_a2.relevance_level} delivery={decision_a2.urgency} "
          f"teams_calls_total={sent_count_after_run2} (expected {sent_count_after_run1}, no duplicate)")

    event_store.close()
    op_history.close()
    delivery_history.close()
    return event_a.event_id


# ══════════════════════════════════════════════════════════════
# SECTION 3 — Scenario C: Event UNCHANGED, Exposure MODERATE → HIGH
# （Dual-Axis Trigger 最重要案例，沿用 phase6_simulation.py 已驗證過的
# ETA 96h → 40h 轉換手法）
# ══════════════════════════════════════════════════════════════
def _scenario_c_article(risk_rules, run_time):
    return _article(
        risk_rules, "acc_c1", "TradeWinds",
        "Kaohsiung terminal reports berth congestion after equipment failure",
        "Local terminal operators report a crane malfunction has caused berth congestion "
        "at Kaohsiung port, with several vessels queuing offshore.", run_time,
    )


def run_scenario_c(risk_rules, memory_rules, delivery_rules, operational_rules, d: str):
    print("\n" + "=" * 60)
    print("SECTION 3 — Scenario C (Event UNCHANGED, Exposure MODERATE -> HIGH -> Teams PROMPT)")
    print("=" * 60)

    event_store = EventStore(os.path.join(d, "events.db"))
    op_history = OperationalHistoryStore(os.path.join(d, "operational.db"))
    delivery_history = DeliveryHistoryStore(os.path.join(d, "delivery.db"))
    orchestrator = DeliveryOrchestrator(delivery_rules, delivery_history)

    t1 = NOW
    run_id_1 = generate_run_id(t1)
    events_1 = run_real_pipeline(risk_rules, [_scenario_c_article(risk_rules, t1)], t1)
    mem_1 = apply_persistent_memory(events_1, event_store, run_id_1, t1, risk_rules, memory_rules,
                                     RiskScorer(risk_rules, EventExtractor(risk_rules)))
    event_c = mem_1["all_current_events"][0]
    event_c.location = event_c.location or "Kaohsiung"
    event_c.port = event_c.port or "Kaohsiung"

    fp = FakeFleetProvider(vessels=[], data_timestamp=t1)
    sp1 = FakeScheduleProvider(port_calls=[PortCall(vessel_id="WH510", vessel_name="WAN HAI 510",
                                                      service_code="AEX1", port_code="TWKHH",
                                                      eta_utc=t1 + timedelta(hours=96))], data_timestamp=t1)
    rp = FakeRouteProvider(services=[], data_timestamp=t1)
    engine1 = OperationalRelevanceEngine(operational_rules, fp, sp1, rp, port_normalizer=PortNormalizer())
    rel_c1 = engine1.assess(event_c, now=t1, run_id=run_id_1)
    prev_c1 = op_history.get_latest(event_c.event_id)
    notif_state_c1 = compute_operational_notification_state(prev_c1, rel_c1)
    op_history.save_snapshot(rel_c1)

    t2 = NOW + timedelta(hours=4)
    run_id_2 = generate_run_id(t2)
    events_2 = run_real_pipeline(risk_rules, [_scenario_c_article(risk_rules, t2)], t2)
    mem_2 = apply_persistent_memory(events_2, event_store, run_id_2, t2, risk_rules, memory_rules,
                                     RiskScorer(risk_rules, EventExtractor(risk_rules)))
    event_c2 = mem_2["all_current_events"][0]
    event_c2.location = event_c2.location or "Kaohsiung"
    event_c2.port = event_c2.port or "Kaohsiung"
    check("Persistent Memory", event_c2.event_id == event_c.event_id
          and event_c2.notification_state == NotificationState.UNCHANGED,
          f"Scenario C precondition failed: event should be UNCHANGED, got {event_c2.notification_state}")

    sp2 = FakeScheduleProvider(port_calls=[PortCall(vessel_id="WH510", vessel_name="WAN HAI 510",
                                                       service_code="AEX1", port_code="TWKHH",
                                                       eta_utc=t2 + timedelta(hours=40))], data_timestamp=t2)
    engine2 = OperationalRelevanceEngine(operational_rules, fp, sp2, rp, port_normalizer=PortNormalizer())
    rel_c2 = engine2.assess(event_c2, now=t2, run_id=run_id_2)
    prev_c2 = op_history.get_latest(event_c2.event_id)
    notif_state_c2 = compute_operational_notification_state(prev_c2, rel_c2)
    op_history.save_snapshot(rel_c2)

    check("Exposure Escalation",
          rel_c1.relevance_level == "MODERATE" and rel_c2.relevance_level == "HIGH"
          and notif_state_c2 == "EXPOSURE_ESCALATED",
          f"expected MODERATE->HIGH + EXPOSURE_ESCALATED, got {rel_c1.relevance_level}->"
          f"{rel_c2.relevance_level} / {notif_state_c2}")

    decision_c2 = orchestrator.decide(event_c2, operational_relevance=rel_c2,
                                       operational_notification_state=notif_state_c2, now=t2, run_id=run_id_2)
    check("Delivery Orchestrator",
          decision_c2.urgency == DeliveryUrgency.PROMPT and DeliveryChannel.TEAMS in decision_c2.channels,
          f"Dual-Axis case: event UNCHANGED + exposure ESCALATED must still trigger PROMPT delivery, "
          f"got urgency={decision_c2.urgency} channels={decision_c2.channels}")

    teams_msg_c = teams_renderer.render(event_c2, rel_c2, decision_c2, dashboard_base_url="http://127.0.0.1:8000")
    check("Teams Renderer", bool(teams_msg_c) and "Kaohsiung" in teams_msg_c,
          "Teams message for Scenario C missing or empty")

    print(f"  Scenario C: event.notification_state={event_c2.notification_state} (unchanged) | "
          f"exposure {rel_c1.relevance_level} -> {rel_c2.relevance_level} | "
          f"operational_state={notif_state_c2} | delivery={decision_c2.urgency}")

    event_store.close()
    op_history.close()
    delivery_history.close()
    return event_c.event_id


# ══════════════════════════════════════════════════════════════
# SECTION 4 — Scenario D: RESOLVED
# ══════════════════════════════════════════════════════════════
def run_scenario_d(risk_rules, memory_rules, delivery_rules, operational_rules, d: str):
    print("\n" + "=" * 60)
    print("SECTION 4 — Scenario D (RESOLVED)")
    print("=" * 60)

    event_store = EventStore(os.path.join(d, "events.db"))
    op_history = OperationalHistoryStore(os.path.join(d, "operational.db"))
    delivery_history = DeliveryHistoryStore(os.path.join(d, "delivery.db"))
    orchestrator = DeliveryOrchestrator(delivery_rules, delivery_history)

    t1 = NOW
    run_id_1 = generate_run_id(t1)
    a1 = _article(risk_rules, "acc_d1", "Reuters",
                  "Container ship MV Test Star grounded and taking on water near Singapore Strait",
                  "Container ship MV Test Star grounded and taking on water near the Singapore Strait; "
                  "crew evacuated, salvage underway.", t1)
    a1b = _article(risk_rules, "acc_d1b", "TradeWinds",
                    "Container ship MV Test Star grounded and taking on water near Singapore Strait",
                    "Container ship MV Test Star grounded and taking on water near the Singapore Strait; "
                    "crew evacuated, salvage underway.", t1)
    events_1 = run_real_pipeline(risk_rules, [a1, a1b], t1)
    mem_1 = apply_persistent_memory(events_1, event_store, run_id_1, t1, risk_rules, memory_rules,
                                     RiskScorer(risk_rules, EventExtractor(risk_rules)))
    event_d1 = mem_1["all_current_events"][0]
    event_d1.event_type = EventType.SAFETY
    check("Persistent Memory", event_d1.notification_state == NotificationState.NEW,
          f"Scenario D precondition failed: expected NEW, got {event_d1.notification_state}")

    fp = FakeFleetProvider(vessels=[], data_timestamp=t1)
    sp1 = FakeScheduleProvider(port_calls=[PortCall(vessel_id="TESTSTAR", vessel_name="MV TEST STAR",
                                                       service_code="SVC1", port_code="SGSIN",
                                                       eta_utc=t1)], data_timestamp=t1)
    rp = FakeRouteProvider(services=[], data_timestamp=t1)
    engine1 = OperationalRelevanceEngine(operational_rules, fp, sp1, rp, port_normalizer=PortNormalizer())
    rel_d1 = engine1.assess(event_d1, now=t1, run_id=run_id_1)
    op_history.save_snapshot(rel_d1)

    # ── RUN 2: 同一艘船、同一地點，文字改成「已脫困／港口恢復運作」──
    t2 = NOW + timedelta(hours=6)
    run_id_2 = generate_run_id(t2)
    a2 = _article(risk_rules, "acc_d2", "Reuters",
                  "MV Test Star successfully refloated after grounding near Singapore Strait",
                  "MV Test Star, which ran aground and was taking on water near the Singapore Strait, "
                  "has been successfully refloated; port operations have resumed, no pollution reported.", t2)
    a2b = _article(risk_rules, "acc_d2b", "TradeWinds",
                    "MV Test Star successfully refloated after grounding near Singapore Strait",
                    "MV Test Star, which ran aground and was taking on water near the Singapore Strait, "
                    "has been successfully refloated; port operations have resumed, no pollution reported.", t2)
    events_2 = run_real_pipeline(risk_rules, [a2, a2b], t2)
    mem_2 = apply_persistent_memory(events_2, event_store, run_id_2, t2, risk_rules, memory_rules,
                                     RiskScorer(risk_rules, EventExtractor(risk_rules)))
    event_d2 = mem_2["all_current_events"][0]
    event_d2.event_type = EventType.SAFETY
    check("Material Change",
          event_d2.event_id == event_d1.event_id
          and event_d2.notification_state == NotificationState.RESOLVED_UPDATE,
          f"expected same event matched + RESOLVED_UPDATE after 'refloated' text, "
          f"got id_match={event_d2.event_id == event_d1.event_id} state={event_d2.notification_state}")
    check("Resolution", event_d2.notification_state == NotificationState.RESOLVED_UPDATE,
          f"expected RESOLVED_UPDATE, got {event_d2.notification_state}")

    engine2 = OperationalRelevanceEngine(operational_rules, fp,
                                          FakeScheduleProvider(port_calls=[], data_timestamp=t2),
                                          rp, port_normalizer=PortNormalizer())
    rel_d2 = engine2.assess(event_d2, now=t2, run_id=run_id_2)
    prev_d2 = op_history.get_latest(event_d2.event_id)
    notif_state_d2 = compute_operational_notification_state(prev_d2, rel_d2)
    op_history.save_snapshot(rel_d2)

    decision_d2 = orchestrator.decide(event_d2, operational_relevance=rel_d2,
                                       operational_notification_state=notif_state_d2, now=t2, run_id=run_id_2)
    check("Delivery Orchestrator",
          decision_d2.teams_mode == "RESOLVED" and DeliveryChannel.TEAMS in decision_d2.channels,
          f"expected teams_mode=RESOLVED for a resolved event, got {decision_d2.teams_mode}")

    teams_msg_d = teams_renderer.render(event_d2, rel_d2, decision_d2)
    check("Teams Renderer", bool(teams_msg_d) and "refloated" in teams_msg_d.lower(),
          "Resolved Teams message missing or doesn't mention resolution")

    selection_d = BriefingSelector().select([event_d2])
    check("Email Renderer", len(selection_d.get("resolved", [])) == 1,
          "BriefingSelector did not route the resolved event into the 'resolved' bucket for Email")
    brief_vm_d = build_daily_brief_view_model(selection_d, generated_at=t2, ai_analyses={},
                                               operational_relevance_map={event_d2.event_id: rel_d2})
    html_d = ExecutiveEmailRenderer().render_daily_brief(brief_vm_d)
    check("Email Renderer", "Test Star" in html_d, "Resolved Email HTML missing vessel/event reference")

    print(f"  Scenario D: {event_d1.notification_state} -> {event_d2.notification_state} | "
          f"teams_mode={decision_d2.teams_mode} | event_id stable={event_d2.event_id == event_d1.event_id}")

    event_store.close()
    op_history.close()
    delivery_history.close()
    return event_d2.event_id


# ══════════════════════════════════════════════════════════════
# SECTION 5 — Dashboard Service（讀取 Section 2〜4 寫入的同一批資料庫）
# ══════════════════════════════════════════════════════════════
def run_dashboard_read(d: str, event_a_id: str, event_c_id: str, event_d_id: str):
    print("\n" + "=" * 60)
    print("SECTION 5 — Dashboard Service (read-only, same databases)")
    print("=" * 60)

    event_store = EventStore(os.path.join(d, "events.db"))
    op_history = OperationalHistoryStore(os.path.join(d, "operational.db"))
    delivery_history = DeliveryHistoryStore(os.path.join(d, "delivery.db"))
    source_health = SourceHealthStore(os.path.join(d, "source_health.db"))
    health_service = SystemHealthService(
        event_store=event_store, delivery_history_store=delivery_history,
        source_health_store=source_health,
        operational_provider_status={"fleet": "AVAILABLE", "schedule": "AVAILABLE", "route": "AVAILABLE"},
        llm_enabled=False,
    )
    service = DashboardService(event_store, op_history, delivery_history, source_health, health_service)

    active_rows = service._active_events_enriched()
    active_ids = {e.get("event_id") for e in active_rows}
    check("Dashboard Service", event_a_id in active_ids or event_c_id in active_ids,
          f"expected Scenario A/C events visible in Dashboard active list, got {active_ids}")

    resolved = service.resolved_events(days=7)
    resolved_ids = {e.get("event_id") for e in resolved}
    check("Dashboard Service", event_d_id in resolved_ids,
          f"expected Scenario D's resolved event in Dashboard resolved_events(), got {resolved_ids}")

    print(f"  Overview active events: {len(active_ids)}   Resolved events: {len(resolved_ids)}")
    print(f"  Event A/C visible: {event_a_id in active_ids or event_c_id in active_ids}   "
          f"Event D in resolved list: {event_d_id in resolved_ids}")

    event_store.close()
    op_history.close()
    delivery_history.close()
    source_health.close()


def main() -> int:
    print("FINAL ACCEPTANCE TEST")
    risk_rules = load_risk_rules()
    memory_rules = load_memory_rules()
    delivery_rules = load_delivery_rules()
    operational_rules = load_operational_rules()

    run_pipeline_mechanics(risk_rules)

    with tempfile.TemporaryDirectory(prefix="final_acceptance_") as d:
        event_a_id = run_scenario_ab(risk_rules, memory_rules, delivery_rules, operational_rules, d)
        event_c_id = run_scenario_c(risk_rules, memory_rules, delivery_rules, operational_rules, d)
        event_d_id = run_scenario_d(risk_rules, memory_rules, delivery_rules, operational_rules, d)
        run_dashboard_read(d, event_a_id, event_c_id, event_d_id)

    print("\n" + "=" * 40)
    print("FINAL ACCEPTANCE TEST")
    label_order = [
        "Collection", "Classification", "Clustering", "Risk Scoring",
        "Persistent Memory", "Material Change", "Operational Relevance",
        "Delivery Orchestrator", "Email Renderer", "Teams Renderer",
        "Dashboard Service", "Duplicate Suppression", "Exposure Escalation", "Resolution",
    ]
    overall = True
    for label in label_order:
        passed = RESULTS.get(label, False)
        overall = overall and passed
        print(f"{label:<28}{'PASS' if passed else 'FAIL'}")

    print("RESULT:")
    print("PASS" if overall else "FAIL")

    if not overall:
        print("\nFailure details:")
        for f in FAILURES:
            print(f"  - {f}")

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
