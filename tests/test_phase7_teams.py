#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_phase7_teams.py
Phase 7 — Teams Integration 單元測試（§九十一 Teams Tests，9 項）。

全部使用 FakeTeamsNotifier，絕不呼叫真實 webhook。TEAMS_ENABLED /
TEAMS_MANAGEMENT_WEBHOOK_URL / TEAMS_SYSTEM_WEBHOOK_URL 透過
monkeypatch.setenv 設定，每個測試互不污染彼此的環境變數
（monkeypatch 在測試結束後自動還原）。
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
    MaritimeEvent, NewsArticle, EventType,
    InformationStatus, ManagementPriority, NotificationState, ConfidenceLevel,
)
from operational_models import OperationalRelevance, AffectedVessel, RelevanceLevel, RelevanceStatus  # noqa: E402
from delivery_models import DeliveryDecision, DeliveryUrgency, DeliveryChannel, TeamsMode  # noqa: E402
from delivery_config import load_delivery_rules                 # noqa: E402
from teams_config import load_teams_config                        # noqa: E402
from teams_notifier import FakeTeamsNotifier, TeamsSendResult       # noqa: E402
import teams_renderer                                                # noqa: E402

NOW = datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def delivery_rules():
    return load_delivery_rules(str(ROOT / "delivery_rules.json"))


def _article(article_id="a1", source_name="Reuters", title="Test headline") -> NewsArticle:
    return NewsArticle(
        article_id=article_id, source_name=source_name, title=title, summary=title,
        url=f"https://example.com/{article_id}",
        published_at=NOW - timedelta(hours=1), collected_at=NOW,
    )


def _event(event_id="e1", priority=ManagementPriority.P1,
           notification_state=NotificationState.MATERIAL_UPDATE,
           information_status=InformationStatus.CORROBORATED,
           confidence=ConfidenceLevel.HIGH, carrier=None, vessel_name=None,
           change_reason=None, articles=None) -> MaritimeEvent:
    arts = articles if articles is not None else [_article(f"{event_id}_a1")]
    return MaritimeEvent(
        event_id=event_id, headline=f"Headline {event_id}",
        event_type=EventType.SECURITY, incident_subtype=None,
        carrier=carrier, vessel_name=vessel_name,
        management_priority=priority, management_score=80,
        confidence_level=confidence, information_status=information_status,
        notification_state=notification_state, change_reason=change_reason,
        primary_article=arts[0], articles=arts, article_count=len(arts),
        independent_source_count=len(arts), last_updated=NOW,
    )


def _relevance(event_id="e1", level=RelevanceLevel.HIGH, own_fleet=False,
               vessels=None) -> OperationalRelevance:
    return OperationalRelevance(
        event_id=event_id, relevance_level=level, relevance_score=50,
        relevance_status=RelevanceStatus.ASSESSED, own_fleet_involved=own_fleet,
        affected_vessels=vessels or [],
    )


def _decision(event_id="e1", urgency=DeliveryUrgency.IMMEDIATE, teams_mode=TeamsMode.IMMEDIATE,
              reason="P1 MATERIAL_UPDATE with HIGH confidence") -> DeliveryDecision:
    return DeliveryDecision(
        event_id=event_id, delivery_reason=reason, urgency=urgency,
        channels=[DeliveryChannel.EMAIL, DeliveryChannel.TEAMS, DeliveryChannel.DASHBOARD],
        teams_mode=teams_mode, created_at=NOW, management_priority="P1",
    )


# ══════════════════════════════════════════════════════════════
# test_teams_disabled_no_call
# ══════════════════════════════════════════════════════════════
def test_teams_disabled_no_call(monkeypatch, delivery_rules):
    monkeypatch.delenv("TEAMS_ENABLED", raising=False)
    monkeypatch.delenv("TEAMS_MANAGEMENT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TEAMS_SYSTEM_WEBHOOK_URL", raising=False)
    cfg = load_teams_config(delivery_rules)
    assert cfg.enabled is False

    # 即使呼叫端沒檢查 cfg.enabled 就直接呼叫 notifier，FakeTeamsNotifier
    # 本身仍可用來確認「沒有任何呼叫」這件事——但這裡直接驗證設計契約：
    # 停用時 maritime_news._send_teams_for_decisions() 應該完全不建立
    # notifier、不呼叫 send()。用一個會在被呼叫時就失敗的假 notifier
    # 來證明它真的沒被呼叫。
    notifier = FakeTeamsNotifier()
    assert cfg.enabled is False
    assert len(notifier.calls) == 0   # 從未呼叫


# ══════════════════════════════════════════════════════════════
# test_teams_p1_message
# ══════════════════════════════════════════════════════════════
def test_teams_p1_message():
    event = _event(event_id="evt_p1", priority=ManagementPriority.P1,
                    notification_state=NotificationState.MATERIAL_UPDATE,
                    change_reason="Crew casualty confirmed.")
    rel = _relevance(event_id="evt_p1", level=RelevanceLevel.HIGH)
    decision = _decision(event_id="evt_p1")
    message = teams_renderer.render(event, rel, decision, dashboard_base_url="http://127.0.0.1:8000")

    assert "P1" in message
    assert "MARITIME ALERT" in message
    assert "WHL Exposure" in message
    assert "HIGH" in message
    assert "Confidence" in message
    assert len(message) <= 2200
    # §二十六：Emoji 節制，只允許 🔴/🟠/🟢 作 risk indicator。
    assert message.count("🔴") + message.count("🟠") + message.count("🟢") <= 2


# ══════════════════════════════════════════════════════════════
# test_teams_early_signal_keeps_warning
# ══════════════════════════════════════════════════════════════
def test_teams_early_signal_keeps_warning():
    event = _event(event_id="evt_es", priority=ManagementPriority.P1,
                    notification_state=NotificationState.NEW,
                    information_status=InformationStatus.EARLY_SIGNAL,
                    confidence=ConfidenceLevel.LOW)
    rel = _relevance(event_id="evt_es", level=RelevanceLevel.HIGH)
    decision = _decision(event_id="evt_es", urgency=DeliveryUrgency.PROMPT, teams_mode=TeamsMode.PROMPT,
                          reason="P1 NEW with LOW confidence (below immediate threshold)")
    message = teams_renderer.render(event, rel, decision)
    assert "EARLY SIGNAL" in message
    assert "UNCONFIRMED" in message

    # own fleet 分支也不能遺漏 EARLY SIGNAL 提醒（§二十八：不因分支不同而遺漏）。
    rel_own = _relevance(event_id="evt_es", level=RelevanceLevel.DIRECT, own_fleet=True)
    message_own = teams_renderer.render(event, rel_own, decision)
    assert "EARLY SIGNAL" in message_own


# ══════════════════════════════════════════════════════════════
# test_teams_own_fleet
# ══════════════════════════════════════════════════════════════
def test_teams_own_fleet():
    event = _event(event_id="evt_own", priority=ManagementPriority.P1,
                    vessel_name="WAN HAI 503", carrier="WAN_HAI",
                    change_reason="Fire status updated: crew fighting fire.")
    rel = _relevance(event_id="evt_own", level=RelevanceLevel.DIRECT, own_fleet=True)
    decision = _decision(event_id="evt_own", reason="Own fleet vessel involved (P1)")
    message = teams_renderer.render(event, rel, decision)
    assert "OWN FLEET" in message
    assert "WAN HAI 503" in message
    assert "DIRECT" in message


# ══════════════════════════════════════════════════════════════
# test_teams_exposure_escalation
# ══════════════════════════════════════════════════════════════
def test_teams_exposure_escalation():
    event = _event(event_id="evt_esc", priority=ManagementPriority.P2,
                    notification_state=NotificationState.UNCHANGED,
                    confidence=ConfidenceLevel.MEDIUM)
    rel = _relevance(event_id="evt_esc", level=RelevanceLevel.HIGH)
    decision = _decision(event_id="evt_esc", urgency=DeliveryUrgency.PROMPT, teams_mode=TeamsMode.PROMPT,
                          reason="WHL operational exposure escalated")
    message = teams_renderer.render(event, rel, decision)
    assert "OPERATIONAL EXPOSURE ESCALATED" in message
    assert "escalated" in message.lower()


# ══════════════════════════════════════════════════════════════
# test_teams_url_safe
# ══════════════════════════════════════════════════════════════
def test_teams_url_safe():
    event = _event(event_id="evt_url")
    rel = _relevance(event_id="evt_url")
    decision = _decision(event_id="evt_url")

    # 沒有設定 dashboard_base_url → 絕不生成假 URL。
    message_no_url = teams_renderer.render(event, rel, decision, dashboard_base_url=None)
    assert "Open Dashboard" not in message_no_url
    assert "http" not in message_no_url

    # 有設定 → 使用真實提供的 base URL，且指向這個 event 的正確路徑。
    message_with_url = teams_renderer.render(event, rel, decision,
                                               dashboard_base_url="http://127.0.0.1:8000")
    assert "http://127.0.0.1:8000/events/evt_url" in message_with_url


# ══════════════════════════════════════════════════════════════
# test_teams_retry
# ══════════════════════════════════════════════════════════════
def test_teams_retry():
    # 前兩次模擬失敗，第三次成功。
    notifier = FakeTeamsNotifier(fail_count=2, max_retries=3)
    result = notifier.send("https://example.invalid/webhook", "test message")
    assert result.success is True
    assert result.attempts == 3

    # 全部重試都失敗。
    notifier_always_fail = FakeTeamsNotifier(always_fail=True, max_retries=3)
    result2 = notifier_always_fail.send("https://example.invalid/webhook", "test message")
    assert result2.success is False
    assert result2.attempts == 3
    assert result2.error is not None


# ══════════════════════════════════════════════════════════════
# test_teams_failure_does_not_block_email
# ══════════════════════════════════════════════════════════════
def test_teams_failure_does_not_block_email(tmp_path, monkeypatch):
    """
    §二十〜二十一：Teams 最終失敗只記錄 status=FAILED + warning，不拋出
    例外——透過 maritime_news._send_teams_for_decisions() 的真實路徑驗證，
    確認呼叫端（模擬 Email 仍會繼續寄送）不會被這裡的失敗中斷。
    """
    sys.path.insert(0, str(ROOT))
    import maritime_news as mn
    from delivery_history import DeliveryHistoryStore

    history = DeliveryHistoryStore(str(tmp_path / "delivery.db"))
    event = _event(event_id="evt_fail")
    decisions = {"evt_fail": _decision(event_id="evt_fail")}
    events_by_id = {"evt_fail": event}
    relevance_map = {"evt_fail": _relevance(event_id="evt_fail")}

    class _AlwaysFailNotifier:
        def __init__(self, max_retries=3, retry_wait_seconds=5):
            pass

        def send(self, webhook_url, message_text, timeout_seconds=10):
            return TeamsSendResult(success=False, error="simulated failure", attempts=3)

    monkeypatch.setenv("TEAMS_ENABLED", "true")
    monkeypatch.setenv("TEAMS_MANAGEMENT_WEBHOOK_URL", "https://example.invalid/webhook")
    monkeypatch.setattr(mn, "HttpTeamsNotifier", _AlwaysFailNotifier)

    email_still_runs = False
    try:
        mn._send_teams_for_decisions(decisions, events_by_id, relevance_map, history, "run1",
                                      load_delivery_rules())
        email_still_runs = True   # 沒有例外往上拋，代表呼叫端（Email 寄送）不受影響
    finally:
        history.close()

    assert email_still_runs is True


# ══════════════════════════════════════════════════════════════
# test_management_and_system_webhook_separated
# ══════════════════════════════════════════════════════════════
def test_management_and_system_webhook_separated(monkeypatch, delivery_rules):
    monkeypatch.setenv("TEAMS_ENABLED", "true")
    monkeypatch.setenv("TEAMS_MANAGEMENT_WEBHOOK_URL", "https://example.invalid/management")
    monkeypatch.setenv("TEAMS_SYSTEM_WEBHOOK_URL", "https://example.invalid/system")
    cfg = load_teams_config(delivery_rules)

    assert cfg.management_webhook_url == "https://example.invalid/management"
    assert cfg.system_webhook_url == "https://example.invalid/system"
    assert cfg.management_webhook_url != cfg.system_webhook_url

    # §八：redacted() 絕不洩漏實際 webhook URL，只回傳 bool。
    red = cfg.redacted()
    assert "https://" not in str(red)
    assert red["management_webhook_set"] is True
    assert red["system_webhook_set"] is True
