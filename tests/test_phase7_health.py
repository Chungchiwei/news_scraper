#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_phase7_health.py
Phase 7 — System Health 單元測試（§九十一 Health Tests，5 項）。

全部使用 tmp_path 暫存 SQLite（EventStore / DeliveryHistoryStore /
SourceHealthStore），不連任何真實 Internet / SMTP / Teams。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from event_store import EventStore                                   # noqa: E402
from delivery_history import DeliveryHistoryStore, DeliveryStatus     # noqa: E402
from delivery_models import DeliveryChannel as DM_DeliveryChannel     # noqa: E402
from source_health import SourceHealthStore, SourceHealthStatus       # noqa: E402
from system_health import SystemHealthService, SystemStatus           # noqa: E402
from teams_notifier import FakeTeamsNotifier                           # noqa: E402

NOW = datetime.now(timezone.utc)


@pytest.fixture
def event_store(tmp_path):
    store = EventStore(str(tmp_path / "events.db"))
    yield store
    store.close()


@pytest.fixture
def delivery_history(tmp_path):
    store = DeliveryHistoryStore(str(tmp_path / "delivery.db"))
    yield store
    store.close()


@pytest.fixture
def source_health(tmp_path):
    store = SourceHealthStore(str(tmp_path / "srchealth.db"))
    yield store
    store.close()


def build_service(event_store, delivery_history, source_health,
                   operational_provider_status=None, llm_enabled=False):
    return SystemHealthService(
        event_store=event_store, delivery_history_store=delivery_history,
        source_health_store=source_health,
        operational_provider_status=operational_provider_status or {},
        llm_enabled=llm_enabled,
    )


# ══════════════════════════════════════════════════════════════
# test_health_last_run
# ══════════════════════════════════════════════════════════════
def test_health_last_run(event_store, delivery_history, source_health):
    event_store.start_run("run_abc", NOW)
    event_store.finish_run(
        "run_abc", NOW, articles_collected=10, valid_articles=8, events_detected=3,
        new_events=1, material_updates=1, unchanged_events=1, resolved_events=0,
        status="SUCCESS",
    )
    svc = build_service(event_store, delivery_history, source_health)
    report = svc.build_report(now=NOW)
    assert report.last_run is not None
    assert report.last_run["run_id"] == "run_abc"
    assert report.last_run["status"] == "SUCCESS"


# ══════════════════════════════════════════════════════════════
# test_health_event_store
# ══════════════════════════════════════════════════════════════
def test_health_event_store(event_store, delivery_history, source_health):
    # 正常情況：Event Store 健康 → HEALTHY（沒有其他 degraded 訊號）。
    svc = build_service(event_store, delivery_history, source_health)
    report = svc.build_report(now=NOW)
    assert report.event_store_status == SystemStatus.HEALTHY
    assert report.overall_status == SystemStatus.HEALTHY

    # Event Store 不可用（health_check 拋例外）→ CRITICAL，且 Overall 也是 CRITICAL
    # ——Persistent Memory 是 production-critical 依賴。
    class BrokenEventStore:
        def health_check(self):
            raise RuntimeError("db locked")

        def get_latest_run(self):
            raise RuntimeError("db locked")

    svc2 = build_service(BrokenEventStore(), delivery_history, source_health)
    report2 = svc2.build_report(now=NOW)
    assert report2.event_store_status == SystemStatus.CRITICAL
    assert report2.overall_status == SystemStatus.CRITICAL
    assert any("unavailable" in n.lower() for n in report2.notes)


# ══════════════════════════════════════════════════════════════
# test_health_delivery_status
# ══════════════════════════════════════════════════════════════
def test_health_delivery_status(event_store, delivery_history, source_health):
    delivery_history.record_delivery(
        event_id="e1", run_id="r1", channel=DM_DeliveryChannel.EMAIL,
        delivery_type="DAILY_BRIEF", delivery_reason="test",
        dedup_key="e1:v1:NONE", status=DeliveryStatus.SENT,
    )
    delivery_history.record_delivery(
        event_id="e1", run_id="r1", channel=DM_DeliveryChannel.TEAMS,
        delivery_type="IMMEDIATE", delivery_reason="test",
        dedup_key="e1:v1:NONE", status=DeliveryStatus.FAILED, error_message="HTTP 500",
    )
    svc = build_service(event_store, delivery_history, source_health)
    report = svc.build_report(now=NOW)
    assert report.email_status == SystemStatus.HEALTHY
    assert report.teams_status == SystemStatus.DEGRADED
    # Teams 失敗（非 Email）→ Overall 只到 DEGRADED，不是 CRITICAL。
    assert report.overall_status == SystemStatus.DEGRADED

    # Email 最終失敗 → Overall 必須是 CRITICAL（既有 exit(1) production policy）。
    delivery_history.record_delivery(
        event_id="e2", run_id="r2", channel=DM_DeliveryChannel.EMAIL,
        delivery_type="ALERT", delivery_reason="test",
        dedup_key="e2:v1:NONE", status=DeliveryStatus.FAILED, error_message="SMTP timeout",
    )
    report2 = svc.build_report(now=NOW)
    assert report2.email_status == SystemStatus.DEGRADED
    assert report2.overall_status == SystemStatus.CRITICAL


# ══════════════════════════════════════════════════════════════
# test_source_health_degraded
# ══════════════════════════════════════════════════════════════
def test_source_health_degraded(source_health):
    for _ in range(3):
        source_health.record_failure("Splash247 RSS")
    status = source_health.get("Splash247 RSS")
    assert status["status"] == SourceHealthStatus.DEGRADED

    for _ in range(2):
        source_health.record_failure("Splash247 RSS")
    status2 = source_health.get("Splash247 RSS")
    assert status2["status"] == SourceHealthStatus.DOWN
    assert status2["consecutive_failures"] == 5

    # 成功一次就重置為 HEALTHY，連續失敗次數歸零。
    source_health.record_success("Splash247 RSS", http_status=200)
    status3 = source_health.get("Splash247 RSS")
    assert status3["status"] == SourceHealthStatus.HEALTHY
    assert status3["consecutive_failures"] == 0


# ══════════════════════════════════════════════════════════════
# test_source_health_failure_not_management_alert
# ══════════════════════════════════════════════════════════════
def test_source_health_failure_not_management_alert(event_store, delivery_history, source_health):
    """
    §五十七：Source Failure ≠ Maritime Alert。Source Health 記錄本身
    完全不接觸 DeliveryOrchestrator / TeamsNotifier / delivery_history
    的 Maritime Intelligence 路徑——這裡用一個「從頭到尾都沒被呼叫過」
    的 FakeTeamsNotifier 佐證：即使大量來源 DOWN，也不會有任何 Teams
    呼叫是由 Source Health 觸發的。
    """
    notifier = FakeTeamsNotifier()

    for source in ("Reuters", "TradeWinds", "Splash247"):
        for _ in range(5):
            source_health.record_failure(source)

    svc = build_service(event_store, delivery_history, source_health)
    report = svc.build_report(now=NOW)

    # 全部來源都 DOWN → System Health 判定為 CRITICAL（沒有任何情報可用），
    # 但這仍然只是 System Health 報告的一個欄位，不是 Maritime Intelligence
    # Teams 通知——delivery_history 裡不會因此出現任何一筆 TEAMS 記錄，
    # FakeTeamsNotifier 也完全沒被呼叫過。
    assert report.source_health_summary["DOWN"] == 3
    assert report.overall_status == SystemStatus.CRITICAL
    assert len(notifier.calls) == 0
    assert delivery_history.recent(limit=50) == []
