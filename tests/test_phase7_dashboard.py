#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_phase7_dashboard.py
Phase 7 — Management Dashboard 單元測試（§九十一 Dashboard Tests，13 項）。

全部使用 tmp_path 暫存 SQLite（EventStore / OperationalHistoryStore /
DeliveryHistoryStore / SourceHealthStore）+ FastAPI TestClient，不連
任何真實 Internet / production database。

env var（MARITIME_DB_PATH 等）透過 monkeypatch.setenv 逐一測試設定，
因為 dashboard/app.py 的 get_dashboard_service() 是在「每次請求」時才
讀取環境變數（generator dependency），不是模組載入當下就固定，所以
不需要重新 import app 模組。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from event_store import EventStore                                    # noqa: E402
from operational_history import OperationalHistoryStore                  # noqa: E402
from operational_models import OperationalRelevance, AffectedVessel, RelevanceLevel, RelevanceStatus  # noqa: E402
from delivery_history import DeliveryHistoryStore                          # noqa: E402
from source_health import SourceHealthStore                                  # noqa: E402

import dashboard.app as dashboard_app                                          # noqa: E402

NOW = datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def db_paths(tmp_path, monkeypatch):
    paths = {
        "MARITIME_DB_PATH": str(tmp_path / "events.db"),
        "MARITIME_OPERATIONAL_HISTORY_DB_PATH": str(tmp_path / "ophist.db"),
        "MARITIME_DELIVERY_HISTORY_DB_PATH": str(tmp_path / "delivery.db"),
        "MARITIME_SOURCE_HEALTH_DB_PATH": str(tmp_path / "srchealth.db"),
    }
    for k, v in paths.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DASHBOARD_AUTH_ENABLED", "false")
    monkeypatch.setenv("LLM_ENABLED", "false")
    return paths


@pytest.fixture
def client(db_paths):
    return TestClient(dashboard_app.app)


def seed_event(db_path: str, event_id="e1", headline="Test Event", priority="P1",
                event_type="SECURITY", event_status="ACTIVE", notification_state="NEW",
                confidence_level="HIGH", carrier=None, vessel_name=None, port=None,
                region=None, management_score=80, last_seen=NOW, first_seen=NOW,
                last_material_update=None) -> None:
    store = EventStore(db_path)
    try:
        store.upsert_event({
            "event_id": event_id, "canonical_key": f"ck_{event_id}", "headline": headline,
            "event_type": event_type, "management_priority": priority,
            "event_status": event_status, "notification_state": notification_state,
            "confidence_level": confidence_level, "information_status": "CORROBORATED",
            "carrier": carrier, "vessel_name": vessel_name, "port": port, "region": region,
            "management_score": management_score, "version": 1,
            "first_seen_utc": _iso(first_seen), "last_seen_utc": _iso(last_seen),
            "last_material_update_utc": _iso(last_material_update) if last_material_update else None,
        })
    finally:
        store.close()


def seed_relevance(db_path: str, event_id="e1", level=RelevanceLevel.HIGH,
                    status=RelevanceStatus.ASSESSED, vessels=None, ports=None) -> None:
    store = OperationalHistoryStore(db_path)
    try:
        rel = OperationalRelevance(
            event_id=event_id, relevance_level=level, relevance_score=50,
            relevance_status=status, affected_vessels=vessels or [],
            affected_ports=ports or [], assessed_at=NOW,
        )
        store.save_snapshot(rel)
    finally:
        store.close()


def seed_history(db_path: str, event_id="e1", change_type="NOTIFICATION_STATE",
                  old_value="NEW", new_value="MATERIAL_UPDATE",
                  change_reason="Crew casualty confirmed.", material=True, run_id="r1") -> None:
    store = EventStore(db_path)
    try:
        store.insert_history(event_id, change_type, old_value, new_value,
                              change_reason, material, run_id)
    finally:
        store.close()


# ══════════════════════════════════════════════════════════════
# test_dashboard_overview
# ══════════════════════════════════════════════════════════════
def test_dashboard_overview(client, db_paths):
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_p1", priority="P1")
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_p2", priority="P2")

    resp = client.get("/")
    assert resp.status_code == 200
    assert "MARITIME INTELLIGENCE" in resp.text or "Overview" in resp.text or "P1" in resp.text

    api = client.get("/api/summary").json()
    assert api["active_p1"] == 1
    assert api["active_p2"] == 1
    # 不顯示 RSS Article Count / Crawler Source Count 給主管（§三十七）。
    assert "rss" not in str(api).lower()
    assert "crawler" not in str(api).lower()


# ══════════════════════════════════════════════════════════════
# test_dashboard_active_events
# ══════════════════════════════════════════════════════════════
def test_dashboard_active_events(client, db_paths):
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_active", headline="Red Sea Vessel Attack",
               priority="P1")
    resp = client.get("/events")
    assert resp.status_code == 200
    assert "Red Sea Vessel Attack" in resp.text


# ══════════════════════════════════════════════════════════════
# test_dashboard_event_detail
# ══════════════════════════════════════════════════════════════
def test_dashboard_event_detail(client, db_paths):
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_detail", headline="Terminal Fire Kaohsiung",
               priority="P1", event_type="SAFETY")
    resp = client.get("/events/e_detail")
    assert resp.status_code == 200
    assert "Terminal Fire Kaohsiung" in resp.text
    assert "EVENT PRIORITY" in resp.text
    assert "WHL EXPOSURE" in resp.text

    resp_missing = client.get("/events/does-not-exist")
    assert resp_missing.status_code == 404


# ══════════════════════════════════════════════════════════════
# test_dashboard_event_history
# ══════════════════════════════════════════════════════════════
def test_dashboard_event_history(client, db_paths):
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_hist")
    seed_history(db_paths["MARITIME_DB_PATH"], event_id="e_hist",
                 change_reason="Crew casualty confirmed.")
    api = client.get("/api/events/e_hist").json()
    assert "event_history" in api
    assert len(api["event_history"]) == 1
    assert api["event_history"][0]["change_reason"] == "Crew casualty confirmed."


# ══════════════════════════════════════════════════════════════
# test_dashboard_operational_history
# ══════════════════════════════════════════════════════════════
def test_dashboard_operational_history(client, db_paths):
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_op")
    seed_history(db_paths["MARITIME_DB_PATH"], event_id="e_op")   # Phase 3 History
    seed_relevance(db_paths["MARITIME_OPERATIONAL_HISTORY_DB_PATH"], event_id="e_op",
                    level=RelevanceLevel.DIRECT)   # Phase 6 Operational History

    api = client.get("/api/events/e_op").json()
    assert "operational_snapshot" in api
    assert api["operational_snapshot"]["relevance_level"] == "DIRECT"
    # §四十六：Event Timeline 與 Operational Exposure Timeline 必須是分開的
    # 兩個結構，不能被合併成同一個 list。
    assert "event_history" in api
    assert isinstance(api["event_history"], list)
    assert isinstance(api["operational_snapshot"], dict)
    assert api["event_history"] is not api["operational_snapshot"]


# ══════════════════════════════════════════════════════════════
# test_dashboard_fleet_exposure
# ══════════════════════════════════════════════════════════════
def test_dashboard_fleet_exposure(client, db_paths):
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_fleet", priority="P2")
    vessel = AffectedVessel(vessel_name="WAN HAI 503", service_code="AEX1", next_port="SGSIN",
                             eta_display=None, exposure_type="PORT_CALL", hours_to_exposure=18.0)
    seed_relevance(db_paths["MARITIME_OPERATIONAL_HISTORY_DB_PATH"], event_id="e_fleet",
                    level=RelevanceLevel.HIGH, vessels=[vessel])

    resp = client.get("/fleet")
    assert resp.status_code == 200
    assert "WAN HAI 503" in resp.text

    api = client.get("/api/fleet-exposure").json()
    names = [v["vessel_name"] for v in api]
    assert "WAN HAI 503" in names


# ══════════════════════════════════════════════════════════════
# test_dashboard_port_exposure
# ══════════════════════════════════════════════════════════════
def test_dashboard_port_exposure(client, db_paths):
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_port", priority="P2")
    seed_relevance(db_paths["MARITIME_OPERATIONAL_HISTORY_DB_PATH"], event_id="e_port",
                    level=RelevanceLevel.HIGH, ports=["SGSIN"])

    resp = client.get("/ports")
    assert resp.status_code == 200
    assert "SGSIN" in resp.text


# ══════════════════════════════════════════════════════════════
# test_dashboard_filters
# ══════════════════════════════════════════════════════════════
def test_dashboard_filters(client, db_paths):
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_f1", priority="P1", headline="P1 Event")
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_f2", priority="P2", headline="P2 Event")

    api_all = client.get("/api/events").json()
    assert len(api_all) == 2

    api_p1 = client.get("/api/events", params={"priority": "P1"}).json()
    assert len(api_p1) == 1
    assert api_p1[0]["event_id"] == "e_f1"


# ══════════════════════════════════════════════════════════════
# test_dashboard_search
# ══════════════════════════════════════════════════════════════
def test_dashboard_search(client, db_paths):
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_s1", vessel_name="WAN HAI 503",
               headline="Fire onboard")
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_s2", vessel_name="MSC ORION",
               headline="Grounding near Singapore")

    resp = client.get("/events", params={"q": "WAN HAI 503"})
    assert resp.status_code == 200
    assert "WAN HAI 503" in resp.text
    assert "MSC ORION" not in resp.text


# ══════════════════════════════════════════════════════════════
# test_dashboard_no_secret_exposure
# ══════════════════════════════════════════════════════════════
def test_dashboard_no_secret_exposure(client, db_paths, monkeypatch):
    monkeypatch.setenv("MAIL_PASSWORD", "super-secret-password")
    monkeypatch.setenv("TEAMS_MANAGEMENT_WEBHOOK_URL", "https://example.invalid/webhook/secret-token")

    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_secret")
    for path in ("/api/health", "/api/summary", "/api/events", "/api/fleet-exposure"):
        resp = client.get(path)
        assert resp.status_code == 200
        body = resp.text
        assert "super-secret-password" not in body
        assert "webhook" not in body.lower()
        assert db_paths["MARITIME_DB_PATH"] not in body
        assert "Traceback" not in body


# ══════════════════════════════════════════════════════════════
# test_dashboard_unavailable_provider
# ══════════════════════════════════════════════════════════════
def test_dashboard_unavailable_provider(db_paths):
    """
    §六十七：Fleet/Schedule/Route Provider 真的不可用時，Dashboard 顯示
    的欄位是 "UNAVAILABLE" 字串，不是空 list 或 0（透過 DashboardService
    直接構造 SystemHealthService 驗證，因為 app.py 目前把
    operational_provider_status 固定傳空 dict，這裡直接測 Service 層）。
    """
    from dashboard.service import DashboardService
    from system_health import SystemHealthService

    event_store = EventStore(db_paths["MARITIME_DB_PATH"])
    op_history = OperationalHistoryStore(db_paths["MARITIME_OPERATIONAL_HISTORY_DB_PATH"])
    delivery_history = DeliveryHistoryStore(db_paths["MARITIME_DELIVERY_HISTORY_DB_PATH"])
    source_health = SourceHealthStore(db_paths["MARITIME_SOURCE_HEALTH_DB_PATH"])
    try:
        health_service = SystemHealthService(
            event_store=event_store, delivery_history_store=delivery_history,
            source_health_store=source_health,
            operational_provider_status={"fleet": "UNAVAILABLE", "schedule": "UNAVAILABLE",
                                          "route": "UNAVAILABLE"},
        )
        service = DashboardService(event_store, op_history, delivery_history, source_health, health_service)
        report = service.system_health()
        assert report.fleet_provider_status == "UNAVAILABLE"
        assert report.schedule_provider_status == "UNAVAILABLE"
        assert report.route_provider_status == "UNAVAILABLE"
        assert report.fleet_provider_status != 0
        assert report.fleet_provider_status != "0"
    finally:
        event_store.close()
        op_history.close()
        delivery_history.close()
        source_health.close()


# ══════════════════════════════════════════════════════════════
# test_dashboard_stale_data
# ══════════════════════════════════════════════════════════════
def test_dashboard_stale_data(client, db_paths):
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_stale", priority="P2")
    seed_relevance(db_paths["MARITIME_OPERATIONAL_HISTORY_DB_PATH"], event_id="e_stale",
                    level=RelevanceLevel.HIGH, status=RelevanceStatus.DATA_STALE)

    api = client.get("/api/events/e_stale").json()
    # 過期的 Schedule 資料必須被誠實標示為 DATA_STALE，不能被當成
    # 目前 confirmed 的曝險直接顯示（§六十六）。
    assert api["relevance_status"] == "DATA_STALE"


# ══════════════════════════════════════════════════════════════
# test_dashboard_read_only
# ══════════════════════════════════════════════════════════════
def test_dashboard_read_only(client, db_paths):
    seed_event(db_paths["MARITIME_DB_PATH"], event_id="e_ro", priority="P3")

    # Dashboard 完全沒有任何寫入端點——嘗試 POST/PUT/DELETE 到既有路徑
    # 應該得到 405（方法不被允許）或 404，絕不能真的修改到 Priority。
    for method, path in (("post", "/events/e_ro"), ("put", "/events/e_ro"),
                          ("delete", "/events/e_ro"), ("post", "/")):
        resp = getattr(client, method)(path)
        assert resp.status_code in (404, 405)

    # 確認資料真的沒被動過。
    row = EventStore(db_paths["MARITIME_DB_PATH"]).get_event("e_ro")
    assert row["management_priority"] == "P3"
