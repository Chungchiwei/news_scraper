#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_phase7_delivery.py
Phase 7 — Delivery Orchestrator 單元測試（§九十一 Delivery Tests，11 項）。

全部使用 tmp_path 暫存 SQLite（DeliveryHistoryStore）+ 手動建構的
MaritimeEvent / OperationalRelevance，不連任何真實 Teams webhook、
真實 SMTP、真實 Internet、真實 LLM——跟 test_phase6_operational.py
同一種慣例。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models import (                                          # noqa: E402
    MaritimeEvent, NewsArticle, InformationStatus,
    ManagementPriority, NotificationState, ConfidenceLevel,
)
from operational_models import OperationalRelevance, RelevanceLevel, RelevanceStatus  # noqa: E402
from delivery_config import load_delivery_rules               # noqa: E402
from delivery_models import DeliveryChannel, DeliveryUrgency, TeamsMode  # noqa: E402
from delivery_history import (                                 # noqa: E402
    DeliveryHistoryStore, DeliveryStatus, build_dedup_key,
)
from delivery_orchestrator import DeliveryOrchestrator          # noqa: E402

NOW = datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def rules():
    return load_delivery_rules(str(ROOT / "delivery_rules.json"))


@pytest.fixture
def history(tmp_path):
    store = DeliveryHistoryStore(str(tmp_path / "delivery.db"))
    yield store
    store.close()


@pytest.fixture
def orchestrator(rules, history):
    return DeliveryOrchestrator(rules, history)


def make_article(article_id="a1", source_name="Reuters") -> NewsArticle:
    return NewsArticle(
        article_id=article_id, source_name=source_name, title="Test headline",
        summary="Test summary", url="https://reuters.com/x",
        published_at=NOW - timedelta(hours=1), collected_at=NOW,
    )


def make_event(event_id="e1", priority=ManagementPriority.P2,
               notification_state=NotificationState.NEW,
               confidence=ConfidenceLevel.MEDIUM,
               information_status=InformationStatus.CORROBORATED,
               carrier=None, vessel_name=None, version=1,
               change_reason=None) -> MaritimeEvent:
    a = make_article(f"{event_id}_a1")
    return MaritimeEvent(
        event_id=event_id, headline=f"Headline {event_id}",
        event_type="SECURITY", incident_subtype=None,
        carrier=carrier, vessel_name=vessel_name, imo_number=None,
        location=None, port=None, sea_area=None, shipping_lane=None, region=None,
        management_priority=priority, management_score=60,
        confidence_level=confidence, information_status=information_status,
        fleet_relevance_score=10, notification_state=notification_state,
        change_reason=change_reason, primary_article=a, articles=[a],
        article_count=1, independent_source_count=1, last_updated=NOW,
        impact_tags=[], version=version,
    )


def make_relevance(event_id="e1", level=RelevanceLevel.NONE,
                    status=RelevanceStatus.ASSESSED, own_fleet=False) -> OperationalRelevance:
    return OperationalRelevance(
        event_id=event_id, relevance_level=level, relevance_score=0.0,
        relevance_status=status, own_fleet_involved=own_fleet,
    )


# ══════════════════════════════════════════════════════════════
# test_p1_new_immediate
# ══════════════════════════════════════════════════════════════
def test_p1_new_immediate(orchestrator):
    e = make_event(priority=ManagementPriority.P1, notification_state=NotificationState.NEW,
                    confidence=ConfidenceLevel.HIGH)
    d = orchestrator.decide(e, now=NOW)
    assert d.urgency == DeliveryUrgency.IMMEDIATE
    assert DeliveryChannel.TEAMS in d.channels
    assert DeliveryChannel.EMAIL in d.channels
    assert DeliveryChannel.DASHBOARD in d.channels


# ══════════════════════════════════════════════════════════════
# test_p2_material_prompt
# ══════════════════════════════════════════════════════════════
def test_p2_material_prompt(orchestrator):
    e = make_event(priority=ManagementPriority.P2, notification_state=NotificationState.MATERIAL_UPDATE)
    d = orchestrator.decide(e, now=NOW)
    assert d.urgency == DeliveryUrgency.PROMPT
    assert DeliveryChannel.TEAMS in d.channels


# ══════════════════════════════════════════════════════════════
# test_unchanged_suppressed
# ══════════════════════════════════════════════════════════════
def test_unchanged_suppressed(orchestrator):
    e = make_event(priority=ManagementPriority.P2, notification_state=NotificationState.UNCHANGED)
    rel = make_relevance(level=RelevanceLevel.NONE)
    d = orchestrator.decide(e, operational_relevance=rel,
                             operational_notification_state="EXPOSURE_UNCHANGED", now=NOW)
    assert d.urgency == DeliveryUrgency.SUPPRESSED
    assert d.channels == [DeliveryChannel.DASHBOARD]
    assert d.dashboard_visibility is True   # §七十九：Dashboard 永遠可見


# ══════════════════════════════════════════════════════════════
# test_unchanged_event_exposure_escalated_delivered（Phase 7 最重要案例）
# ══════════════════════════════════════════════════════════════
def test_unchanged_event_exposure_escalated_delivered(orchestrator):
    e = make_event(priority=ManagementPriority.P2, notification_state=NotificationState.UNCHANGED)
    rel = make_relevance(level=RelevanceLevel.HIGH)
    d = orchestrator.decide(e, operational_relevance=rel,
                             operational_notification_state="EXPOSURE_ESCALATED", now=NOW)
    assert d.urgency != DeliveryUrgency.SUPPRESSED
    assert DeliveryUrgency.RANK[d.urgency] <= DeliveryUrgency.RANK[DeliveryUrgency.PROMPT]
    assert "escalated" in d.delivery_reason.lower()
    assert DeliveryChannel.TEAMS in d.channels
    assert DeliveryChannel.DASHBOARD in d.channels


# ══════════════════════════════════════════════════════════════
# test_own_fleet_override
# ══════════════════════════════════════════════════════════════
def test_own_fleet_override(orchestrator):
    # P2 + NEW + LOW confidence（未達 immediate/prompt 門檻，一般只會是 BRIEF）。
    e = make_event(priority=ManagementPriority.P2, notification_state=NotificationState.NEW,
                    confidence=ConfidenceLevel.LOW)
    rel = make_relevance(level=RelevanceLevel.DIRECT, own_fleet=True)
    d = orchestrator.decide(e, operational_relevance=rel, now=NOW)
    assert d.urgency == DeliveryUrgency.PROMPT   # own_fleet_floor["P2"] = PROMPT
    assert "own fleet" in d.delivery_reason.lower()
    # ★ Urgency 提高不等於 information_status 被覆寫成 CONFIRMED。
    assert e.information_status == InformationStatus.CORROBORATED


def test_own_fleet_override_p1_floors_to_immediate(orchestrator):
    e = make_event(priority=ManagementPriority.P1, notification_state=NotificationState.UNCHANGED)
    rel = make_relevance(level=RelevanceLevel.DIRECT, own_fleet=True)
    d = orchestrator.decide(e, operational_relevance=rel, now=NOW)
    assert d.urgency == DeliveryUrgency.IMMEDIATE


# ══════════════════════════════════════════════════════════════
# test_resolved_notification
# ══════════════════════════════════════════════════════════════
def test_resolved_notification(orchestrator):
    e = make_event(priority=ManagementPriority.P1, notification_state=NotificationState.RESOLVED_UPDATE,
                    change_reason="Fire extinguished, vessel underway.")
    d = orchestrator.decide(e, now=NOW)
    assert d.teams_mode == TeamsMode.RESOLVED
    assert DeliveryChannel.TEAMS in d.channels


# ══════════════════════════════════════════════════════════════
# test_exposure_cleared_notification
# ══════════════════════════════════════════════════════════════
def test_exposure_cleared_notification(orchestrator):
    e = make_event(priority=ManagementPriority.P2, notification_state=NotificationState.UNCHANGED)
    rel = make_relevance(level=RelevanceLevel.NONE)
    d = orchestrator.decide(e, operational_relevance=rel,
                             operational_notification_state="EXPOSURE_CLEARED", now=NOW)
    assert d.urgency != DeliveryUrgency.SUPPRESSED
    assert DeliveryUrgency.RANK[d.urgency] <= DeliveryUrgency.RANK[DeliveryUrgency.BRIEF]
    assert "cleared" in d.delivery_reason.lower()


# ══════════════════════════════════════════════════════════════
# test_channel_dedup
# ══════════════════════════════════════════════════════════════
def test_channel_dedup(history):
    key = build_dedup_key("e1", 1, "EXPOSURE_ESCALATED")
    assert history.already_sent(key, DeliveryChannel.TEAMS) is False
    history.record_delivery(event_id="e1", run_id="r1", channel=DeliveryChannel.TEAMS,
                             delivery_type="PROMPT", delivery_reason="test",
                             dedup_key=key, status=DeliveryStatus.SENT)
    assert history.already_sent(key, DeliveryChannel.TEAMS) is True
    # 同一個 dedup_key 在 EMAIL channel 上沒發過，兩個 channel 各自獨立。
    assert history.already_sent(key, DeliveryChannel.EMAIL) is False
    # 事件版本不同（version 2）→ dedup_key 不同 → 不算已發送。
    other_key = build_dedup_key("e1", 2, "EXPOSURE_ESCALATED")
    assert history.already_sent(other_key, DeliveryChannel.TEAMS) is False


# ══════════════════════════════════════════════════════════════
# test_email_and_teams_independent_history
# ══════════════════════════════════════════════════════════════
def test_email_and_teams_independent_history(history):
    key = build_dedup_key("e1", 1, None)
    history.record_delivery(event_id="e1", run_id="r1", channel=DeliveryChannel.EMAIL,
                             delivery_type="ALERT", delivery_reason="P1 NEW",
                             dedup_key=key, status=DeliveryStatus.SENT)
    history.record_delivery(event_id="e1", run_id="r1", channel=DeliveryChannel.TEAMS,
                             delivery_type="IMMEDIATE", delivery_reason="P1 NEW",
                             dedup_key=key, status=DeliveryStatus.FAILED, error_message="HTTP 500")

    email_last = history.last_delivery("e1", DeliveryChannel.EMAIL)
    teams_last = history.last_delivery("e1", DeliveryChannel.TEAMS)
    assert email_last["status"] == DeliveryStatus.SENT
    assert teams_last["status"] == DeliveryStatus.FAILED
    # Email 已寄成功，Teams 失敗——下次 run Email 不應重寄，Teams 可以 retry。
    assert history.already_sent(key, DeliveryChannel.EMAIL) is True
    assert history.already_sent(key, DeliveryChannel.TEAMS) is False


# ══════════════════════════════════════════════════════════════
# test_cooldown_suppresses_repeat
# ══════════════════════════════════════════════════════════════
def test_cooldown_suppresses_repeat(orchestrator, history):
    e = make_event(event_id="e_cool", priority=ManagementPriority.P2,
                    notification_state=NotificationState.UNCHANGED)
    rel = make_relevance(event_id="e_cool", level=RelevanceLevel.HIGH)

    d1 = orchestrator.decide(e, operational_relevance=rel,
                              operational_notification_state="EXPOSURE_ESCALATED", now=NOW)
    assert DeliveryChannel.TEAMS in d1.channels
    history.record_delivery(event_id="e_cool", run_id="r1", channel=DeliveryChannel.TEAMS,
                             delivery_type=d1.teams_mode, delivery_reason=d1.delivery_reason,
                             dedup_key=d1.dedup_key, status=DeliveryStatus.SENT, sent_at=NOW)

    # 10 分鐘後，同樣是 EXPOSURE_ESCALATED（非 bypass 狀態），P2 cooldown 120 分鐘內。
    later = NOW + timedelta(minutes=10)
    d2 = orchestrator.decide(e, operational_relevance=rel,
                              operational_notification_state="EXPOSURE_ESCALATED", now=later)
    assert d2.teams_suppressed_by_cooldown is True
    assert DeliveryChannel.TEAMS not in d2.channels
    # Email/Dashboard 不受 cooldown 影響。
    assert DeliveryChannel.EMAIL in d2.channels
    assert DeliveryChannel.DASHBOARD in d2.channels


# ══════════════════════════════════════════════════════════════
# test_material_update_bypasses_cooldown
# ══════════════════════════════════════════════════════════════
def test_material_update_bypasses_cooldown(orchestrator, history):
    e = make_event(event_id="e_bypass", priority=ManagementPriority.P1,
                    notification_state=NotificationState.MATERIAL_UPDATE,
                    confidence=ConfidenceLevel.HIGH)

    d1 = orchestrator.decide(e, now=NOW)
    assert DeliveryChannel.TEAMS in d1.channels
    history.record_delivery(event_id="e_bypass", run_id="r1", channel=DeliveryChannel.TEAMS,
                             delivery_type=d1.teams_mode, delivery_reason=d1.delivery_reason,
                             dedup_key=d1.dedup_key, status=DeliveryStatus.SENT, sent_at=NOW)

    # 5 分鐘後，仍是 MATERIAL_UPDATE（真正的新事實）→ 必須 bypass cooldown。
    later = NOW + timedelta(minutes=5)
    e2 = make_event(event_id="e_bypass", priority=ManagementPriority.P1,
                     notification_state=NotificationState.MATERIAL_UPDATE,
                     confidence=ConfidenceLevel.HIGH, version=2)
    d2 = orchestrator.decide(e2, now=later)
    assert d2.teams_suppressed_by_cooldown is False
    assert DeliveryChannel.TEAMS in d2.channels
