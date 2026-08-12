"""
tests/test_phase6_operational.py
Phase 6 — Fleet, Route & Port Operational Relevance Integration 單元測試

全部使用 FakeFleetProvider / FakeScheduleProvider / FakeRouteProvider
（跟 Phase 5 的 FakeLLMProvider 同一種慣例），OperationalHistoryStore
一律指向 tmp_path，不連任何真實內部系統、真實 SMTP、真實 LLM、真實
Internet。PortNormalizer 使用專案內的 config/ports_config.json（純本地
別名表，不是外部服務）。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models import (                                        # noqa: E402
    MaritimeEvent, NewsArticle, EventType,
    InformationStatus, ManagementPriority, NotificationState, ConfidenceLevel,
)
from operational_config import load_operational_rules        # noqa: E402
from operational_models import (                              # noqa: E402
    RelevanceLevel, RelevanceStatus, ExposureType, OperationalNotificationState,
    FleetVessel, PortCall, Service,
)
from fleet_provider import FakeFleetProvider                  # noqa: E402
from schedule_provider import FakeScheduleProvider             # noqa: E402
from route_provider import FakeRouteProvider                   # noqa: E402
from port_normalizer import PortNormalizer                     # noqa: E402
from operational_relevance import OperationalRelevanceEngine   # noqa: E402
from operational_history import (                              # noqa: E402
    OperationalHistoryStore, compute_operational_notification_state,
)
from email_view_model import build_event_view_model            # noqa: E402
from management_summary import ManagementSummaryBuilder        # noqa: E402
from executive_email_renderer import ExecutiveEmailRenderer    # noqa: E402
from email_config import load_email_rules                      # noqa: E402

NOW = datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════
# Fixtures / 工廠函式
# ══════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def operational_rules():
    return load_operational_rules(str(ROOT / "operational_rules.json"))


@pytest.fixture(scope="module")
def email_rules():
    return load_email_rules(str(ROOT / "email_rules.json"))


@pytest.fixture(scope="module")
def port_normalizer():
    return PortNormalizer()   # 讀專案內 config/ports_config.json，純本地別名表


@pytest.fixture
def summary_builder():
    return ManagementSummaryBuilder()


@pytest.fixture
def renderer():
    return ExecutiveEmailRenderer()


def make_article(article_id="a1", source_name="Reuters", title="Test headline",
                  summary="Test summary") -> NewsArticle:
    return NewsArticle(
        article_id=article_id, source_name=source_name, title=title,
        summary=summary, url="https://reuters.com/x",
        published_at=NOW - timedelta(hours=1), collected_at=NOW,
    )


def make_event(event_id="e1", event_type=EventType.SAFETY,
               carrier=None, vessel_name=None, imo_number=None,
               location=None, port=None, sea_area=None, shipping_lane=None,
               region=None, headline=None, summary_text="Test summary",
               priority=ManagementPriority.P2) -> MaritimeEvent:
    a = make_article(f"{event_id}_a1", title=headline or f"Headline {event_id}",
                      summary=summary_text)
    return MaritimeEvent(
        event_id=event_id, headline=headline or f"Headline {event_id}",
        event_type=event_type, incident_subtype=None,
        carrier=carrier, vessel_name=vessel_name, imo_number=imo_number,
        location=location, port=port, sea_area=sea_area, shipping_lane=shipping_lane,
        region=region,
        management_priority=priority, management_score=60,
        confidence_level=ConfidenceLevel.MEDIUM, information_status=InformationStatus.CORROBORATED,
        fleet_relevance_score=10, notification_state=NotificationState.NEW, change_reason=None,
        primary_article=a, articles=[a], article_count=1, independent_source_count=1,
        last_updated=NOW, impact_tags=[],
    )


def build_engine(rules, vessels=None, port_calls=None, services=None,
                  fleet_ts=None, schedule_ts=None, route_ts=None,
                  fleet_raise=False, schedule_raise=False, route_raise=False,
                  normalizer=None):
    fp = FakeFleetProvider(vessels=vessels or [], data_timestamp=fleet_ts, raise_error=fleet_raise)
    sp = FakeScheduleProvider(port_calls=port_calls or [], data_timestamp=schedule_ts, raise_error=schedule_raise)
    rp = FakeRouteProvider(services=services or [], data_timestamp=route_ts)
    return OperationalRelevanceEngine(rules, fp, sp, rp, port_normalizer=normalizer or PortNormalizer())


WHL_503 = FleetVessel(vessel_id="WH503", vessel_name="WAN HAI 503", imo_number="9876543")
WHL_510 = FleetVessel(vessel_id="WH510", vessel_name="WAN HAI 510", imo_number="9876550")


# ══════════════════════════════════════════════════════════════
# CASE 6-A/6-B — Own Fleet Matching
# ══════════════════════════════════════════════════════════════
def test_own_fleet_exact_match(operational_rules):
    """CASE 6-A：船名精確比對 → 一律 DIRECT，own_fleet_involved=True。"""
    engine = build_engine(operational_rules, vessels=[WHL_503, WHL_510])
    e = make_event("e_a", vessel_name="WAN HAI 503", event_type=EventType.SAFETY)
    rel = engine.assess(e, now=NOW)
    assert rel.relevance_level == RelevanceLevel.DIRECT
    assert rel.own_fleet_involved is True
    assert rel.relevance_status == RelevanceStatus.ASSESSED
    assert ExposureType.OWN_VESSEL in rel.exposure_types


def test_different_wanhai_vessel_no_match(operational_rules):
    """CASE 6-B："WAN HAI 505" 與船隊中的 "WAN HAI 503" 是不同船，不可誤判為同一艘。"""
    engine = build_engine(operational_rules, vessels=[WHL_503])
    e = make_event("e_b", vessel_name="WAN HAI 505", carrier="WAN_HAI", event_type=EventType.SAFETY)
    rel = engine.assess(e, now=NOW)
    assert rel.own_fleet_involved is False
    assert rel.relevance_level != RelevanceLevel.DIRECT
    # Carrier-only fallback 仍可能貢獻低分（LOW），但絕不能是 DIRECT/own_fleet_involved
    assert rel.relevance_level in (RelevanceLevel.LOW, RelevanceLevel.NONE)


# ══════════════════════════════════════════════════════════════
# CASE 6-C/6-D/6-E — Port Call Exposure / ETA Windows
# ══════════════════════════════════════════════════════════════
def test_port_eta_18h_high_exposure(operational_rules, port_normalizer):
    """CASE 6-C：18h 內有 Port Call → HIGH exposure。"""
    pc = PortCall(vessel_id="WH503", vessel_name="WAN HAI 503", service_code="AEX1",
                   port_code="SGSIN", eta_utc=NOW + timedelta(hours=18))
    engine = build_engine(operational_rules, port_calls=[pc], normalizer=port_normalizer)
    e = make_event("e_c", event_type=EventType.OPERATIONS, port="Singapore")
    rel = engine.assess(e, now=NOW)
    assert rel.relevance_level == RelevanceLevel.HIGH
    assert rel.closest_eta_hours == pytest.approx(18.0, abs=0.1)
    assert ExposureType.PORT_CALL in rel.exposure_types


def test_port_eta_96h_moderate(operational_rules, port_normalizer):
    """CASE 6-D：96h 外的 Port Call → MODERATE，不是 HIGH。"""
    pc = PortCall(vessel_id="WH510", vessel_name="WAN HAI 510", service_code="AEX1",
                   port_code="TWKHH", eta_utc=NOW + timedelta(hours=96))
    engine = build_engine(operational_rules, port_calls=[pc], normalizer=port_normalizer)
    e = make_event("e_d", event_type=EventType.OPERATIONS, port="Kaohsiung")
    rel = engine.assess(e, now=NOW)
    assert rel.relevance_level == RelevanceLevel.MODERATE
    assert rel.closest_eta_hours == pytest.approx(96.0, abs=0.1)


def test_no_port_call(operational_rules, port_normalizer):
    """CASE 6-E：事件的港口沒有任何未來 Port Call → NONE，not fabricated。"""
    engine = build_engine(operational_rules, port_calls=[], normalizer=port_normalizer)
    e = make_event("e_e", event_type=EventType.OPERATIONS, port="Singapore")
    rel = engine.assess(e, now=NOW)
    assert rel.relevance_level == RelevanceLevel.NONE
    assert rel.affected_ports == []
    assert rel.relevance_status == RelevanceStatus.ASSESSED


# ══════════════════════════════════════════════════════════════
# CASE 6-F/6-G — Route / Shipping Lane Exposure
# ══════════════════════════════════════════════════════════════
def test_red_sea_route_exposure(operational_rules):
    """CASE 6-F：事件的 sea_area 落在某條 Service 的 major_shipping_lanes → 曝險。"""
    svc = Service(service_code="AEX1", major_shipping_lanes=["RED_SEA", "SUEZ_CANAL"])
    engine = build_engine(operational_rules, services=[svc])
    e = make_event("e_f", event_type=EventType.SECURITY, sea_area="RED_SEA")
    rel = engine.assess(e, now=NOW)
    assert rel.relevance_level in (RelevanceLevel.MODERATE, RelevanceLevel.HIGH)
    assert ExposureType.SERVICE_ROUTE in rel.exposure_types
    assert "AEX1" in rel.affected_services


def test_unrelated_route_no_exposure(operational_rules):
    """CASE 6-G：事件海域跟任何 Service 的航線都無關 → NONE。"""
    svc = Service(service_code="AEX1", major_shipping_lanes=["RED_SEA", "SUEZ_CANAL"])
    engine = build_engine(operational_rules, services=[svc])
    e = make_event("e_g", event_type=EventType.SECURITY, sea_area="BALTIC_SEA")
    rel = engine.assess(e, now=NOW)
    assert rel.relevance_level == RelevanceLevel.NONE
    assert rel.affected_services == []


# ══════════════════════════════════════════════════════════════
# CASE 6-H/6-I — Regulatory Global Fleet / Market News Discipline
# ══════════════════════════════════════════════════════════════
def test_global_regulatory_relevance(operational_rules):
    """CASE 6-H：REGULATORY 事件命中關鍵字（IMO/SOLAS/...）→ GLOBAL_FLEET，即使無港口/航線比對。"""
    engine = build_engine(operational_rules)
    e = make_event("e_h", event_type=EventType.REGULATORY,
                    headline="IMO adopts new SOLAS amendment on container weight verification")
    rel = engine.assess(e, now=NOW)
    assert ExposureType.GLOBAL_FLEET in rel.exposure_types
    assert rel.relevance_level in (RelevanceLevel.MODERATE, RelevanceLevel.HIGH)


def test_market_news_not_direct(operational_rules):
    """CASE 6-I：純粹 Carrier 品牌提及的 MARKET 新聞不可自動變成高曝險。"""
    engine = build_engine(operational_rules, vessels=[WHL_503])
    e = make_event("e_i", event_type=EventType.MARKET, carrier="WAN_HAI")
    rel = engine.assess(e, now=NOW)
    assert rel.own_fleet_involved is False
    assert rel.relevance_level in (RelevanceLevel.NONE, RelevanceLevel.LOW)


# ══════════════════════════════════════════════════════════════
# CASE 6-J/6-K — Exposure Lifecycle（Escalation / Cleared）
# ══════════════════════════════════════════════════════════════
def test_exposure_escalation(operational_rules, tmp_path, port_normalizer):
    """
    事件本身（Phase 3 notification_state）可以維持 UNCHANGED，但船期 ETA
    從 96h 逼近到 40h 時，Operational Exposure 必須獨立判定為 ESCALATED。
    """
    store = OperationalHistoryStore(str(tmp_path / "op_history.db"))
    try:
        e = make_event("e_escalate", event_type=EventType.OPERATIONS, port="Kaohsiung")

        pc1 = PortCall(vessel_id="WH510", vessel_name="WAN HAI 510", port_code="TWKHH",
                        eta_utc=NOW + timedelta(hours=96))
        engine1 = build_engine(operational_rules, port_calls=[pc1], normalizer=port_normalizer)
        rel1 = engine1.assess(e, now=NOW, run_id="run1")
        assert rel1.relevance_level == RelevanceLevel.MODERATE
        prev = store.get_latest(e.event_id)
        state1 = compute_operational_notification_state(prev, rel1)
        assert state1 == OperationalNotificationState.EXPOSURE_NEW
        store.save_snapshot(rel1)

        pc2 = PortCall(vessel_id="WH510", vessel_name="WAN HAI 510", port_code="TWKHH",
                        eta_utc=NOW + timedelta(hours=40))
        engine2 = build_engine(operational_rules, port_calls=[pc2], normalizer=port_normalizer)
        rel2 = engine2.assess(e, now=NOW, run_id="run2")
        assert rel2.relevance_level == RelevanceLevel.HIGH
        prev2 = store.get_latest(e.event_id)
        state2 = compute_operational_notification_state(prev2, rel2)
        assert state2 == OperationalNotificationState.EXPOSURE_ESCALATED
        store.save_snapshot(rel2)
    finally:
        store.close()


def test_exposure_cleared(operational_rules, tmp_path, port_normalizer):
    """Port Call 從船期資料中移除（不是憑空猜測改道）→ EXPOSURE_CLEARED。"""
    store = OperationalHistoryStore(str(tmp_path / "op_history2.db"))
    try:
        e = make_event("e_clear", event_type=EventType.OPERATIONS, port="Singapore")

        pc = PortCall(vessel_id="WH503", vessel_name="WAN HAI 503", port_code="SGSIN",
                       eta_utc=NOW + timedelta(hours=18))
        engine1 = build_engine(operational_rules, port_calls=[pc], normalizer=port_normalizer)
        rel1 = engine1.assess(e, now=NOW, run_id="run1")
        assert rel1.relevance_level == RelevanceLevel.HIGH
        store.save_snapshot(rel1)

        engine2 = build_engine(operational_rules, port_calls=[], normalizer=port_normalizer)
        rel2 = engine2.assess(e, now=NOW, run_id="run2")
        assert rel2.relevance_level == RelevanceLevel.NONE
        prev = store.get_latest(e.event_id)
        state = compute_operational_notification_state(prev, rel2)
        assert state == OperationalNotificationState.EXPOSURE_CLEARED
    finally:
        store.close()


# ══════════════════════════════════════════════════════════════
# CASE 6-L/6-M — Provider Failure / Stale Data
# ══════════════════════════════════════════════════════════════
def test_fleet_provider_failure_unknown(operational_rules):
    """CASE 6-L：Provider 失敗 → UNAVAILABLE，絕不能靜默降級成 NONE。"""
    engine = build_engine(operational_rules, fleet_raise=True)
    e = make_event("e_l", vessel_name="WAN HAI 503")
    rel = engine.assess(e, now=NOW)
    assert rel.relevance_status == RelevanceStatus.UNAVAILABLE
    assert rel.relevance_level is None
    assert rel.relevance_level != RelevanceLevel.NONE


def test_stale_schedule(operational_rules, port_normalizer):
    """CASE 6-M：船期資料過期（超過 schedule_stale_after_hours）→ DATA_STALE，需明確標記。"""
    old_ts = NOW - timedelta(hours=48)  # 遠超過 schedule_stale_after_hours=12
    pc = PortCall(vessel_id="WH503", vessel_name="WAN HAI 503", port_code="SGSIN",
                   eta_utc=NOW + timedelta(hours=18))
    engine = build_engine(operational_rules, port_calls=[pc], schedule_ts=old_ts,
                           normalizer=port_normalizer)
    e = make_event("e_m", event_type=EventType.OPERATIONS, port="Singapore")
    rel = engine.assess(e, now=NOW)
    assert rel.is_stale is True
    assert rel.relevance_status == RelevanceStatus.DATA_STALE


# ══════════════════════════════════════════════════════════════
# CASE 6-N/6-O — Port Normalization Discipline
# ══════════════════════════════════════════════════════════════
def test_ambiguous_port_rejected(operational_rules, port_normalizer):
    """CASE 6-N："Portland" 在多國都有同名港口，別名表刻意不收錄，不可猜測。"""
    pc = PortCall(vessel_id="WH503", vessel_name="WAN HAI 503", port_code="USPDX",
                   eta_utc=NOW + timedelta(hours=10))
    engine = build_engine(operational_rules, port_calls=[pc], normalizer=port_normalizer)
    e = make_event("e_n", event_type=EventType.OPERATIONS, port="Portland")
    rel = engine.assess(e, now=NOW)
    assert rel.affected_ports == []
    assert ExposureType.PORT_CALL not in rel.exposure_types


def test_shipping_lane_not_equal_port(operational_rules, port_normalizer):
    """CASE 6-O："Singapore Strait"（海域）不可被誤判成「新加坡港」的 Port Call。"""
    pc = PortCall(vessel_id="WH503", vessel_name="WAN HAI 503", port_code="SGSIN",
                   eta_utc=NOW + timedelta(hours=10))
    engine = build_engine(operational_rules, port_calls=[pc], normalizer=port_normalizer)
    e = make_event("e_o", event_type=EventType.SECURITY, sea_area="SINGAPORE_STRAIT",
                    location="Singapore Strait")
    rel = engine.assess(e, now=NOW)
    assert rel.affected_ports == []
    assert ExposureType.PORT_CALL not in rel.exposure_types


# ══════════════════════════════════════════════════════════════
# 多船排序 / Event 欄位不可變 / Config-driven 驗證
# ══════════════════════════════════════════════════════════════
def test_multiple_vessels_sorted_eta(operational_rules, port_normalizer):
    """同一港口有多筆 Port Call 時，affected_vessels 依 ETA 由近到遠排序，closest_eta_hours 取最小值。"""
    pc_far = PortCall(vessel_id="WH510", vessel_name="WAN HAI 510", port_code="SGSIN",
                       eta_utc=NOW + timedelta(hours=50))
    pc_near = PortCall(vessel_id="WH503", vessel_name="WAN HAI 503", port_code="SGSIN",
                        eta_utc=NOW + timedelta(hours=5))
    engine = build_engine(operational_rules, port_calls=[pc_far, pc_near], normalizer=port_normalizer)
    e = make_event("e_multi", event_type=EventType.OPERATIONS, port="Singapore")
    rel = engine.assess(e, now=NOW)
    assert rel.closest_eta_hours == pytest.approx(5.0, abs=0.1)
    assert len(rel.affected_vessels) == 2
    assert rel.affected_vessels[0].vessel_name == "WAN HAI 503"
    assert rel.affected_vessels[1].vessel_name == "WAN HAI 510"
    assert rel.affected_vessels[0].hours_to_exposure < rel.affected_vessels[1].hours_to_exposure


def test_event_confidence_unchanged_by_relevance(operational_rules):
    """EVENT RISK ≠ COMPANY EXPOSURE：跑完 Operational Relevance 後，event 本身的 Phase 1-5 欄位必須完全不變。"""
    engine = build_engine(operational_rules, vessels=[WHL_503])
    e = make_event("e_immutable", vessel_name="WAN HAI 503", priority=ManagementPriority.P1)
    before = (e.confidence_level, e.management_priority, e.information_status, e.management_score)
    rel = engine.assess(e, now=NOW)
    after = (e.confidence_level, e.management_priority, e.information_status, e.management_score)
    assert before == after
    assert rel.relevance_level == RelevanceLevel.DIRECT   # 曝險判定本身仍正確運作


def test_operational_score_config(operational_rules):
    """門檻/權重必須是 config-driven：換一份門檻設定，同樣的分數要落到不同的 Level。"""
    pc = PortCall(vessel_id="WH503", vessel_name="WAN HAI 503", port_code="SGSIN",
                   eta_utc=NOW + timedelta(hours=18))   # port_call_immediate=68 → 預設落在 HIGH
    normalizer = PortNormalizer()

    engine_default = build_engine(operational_rules, port_calls=[pc], normalizer=normalizer)
    e1 = make_event("e_cfg1", event_type=EventType.OPERATIONS, port="Singapore")
    rel_default = engine_default.assess(e1, now=NOW)
    assert rel_default.relevance_level == RelevanceLevel.HIGH

    custom_rules = dict(operational_rules)
    custom_rules["relevance_thresholds"] = dict(operational_rules["relevance_thresholds"])
    custom_rules["relevance_thresholds"]["HIGH"] = 90   # 拉高門檻，同樣 68 分應改落 MODERATE
    engine_custom = build_engine(custom_rules, port_calls=[pc], normalizer=normalizer)
    e2 = make_event("e_cfg2", event_type=EventType.OPERATIONS, port="Singapore")
    rel_custom = engine_custom.assess(e2, now=NOW)
    assert rel_custom.relevance_level == RelevanceLevel.MODERATE
    assert rel_custom.relevance_score == rel_default.relevance_score   # 分數不變，只是門檻換了


# ══════════════════════════════════════════════════════════════
# Operational History Store（獨立 SQLite，跟 Phase 3 event_history 分開）
# ══════════════════════════════════════════════════════════════
def test_operational_history_saved(operational_rules, tmp_path, port_normalizer):
    db_path = str(tmp_path / "op_history_saved.db")
    store = OperationalHistoryStore(db_path)
    try:
        pc = PortCall(vessel_id="WH503", vessel_name="WAN HAI 503", port_code="SGSIN",
                       eta_utc=NOW + timedelta(hours=18))
        engine = build_engine(operational_rules, port_calls=[pc], normalizer=port_normalizer)
        e = make_event("e_hist", event_type=EventType.OPERATIONS, port="Singapore")
        rel = engine.assess(e, now=NOW, run_id="run_hist_1")
        store.save_snapshot(rel)

        latest = store.get_latest("e_hist")
        assert latest is not None
        assert latest["relevance_level"] == RelevanceLevel.HIGH
        assert latest["affected_vessels"][0]["vessel_name"] == "WAN HAI 503"

        # UNAVAILABLE 快照不可覆蓋已知良好的基準
        unavail_engine = build_engine(operational_rules, fleet_raise=True)
        rel_unavail = unavail_engine.assess(e, now=NOW, run_id="run_hist_2")
        store.save_snapshot(rel_unavail)
        still_latest = store.get_latest("e_hist")
        assert still_latest["relevance_level"] == RelevanceLevel.HIGH   # 沒有被 UNAVAILABLE 蓋掉
    finally:
        store.close()


def test_operational_history_restart(operational_rules, tmp_path, port_normalizer):
    """模擬跨 process 重啟：關閉 store 再重新開啟同一個 db 檔案，資料仍在。"""
    db_path = str(tmp_path / "op_history_restart.db")

    store1 = OperationalHistoryStore(db_path)
    pc = PortCall(vessel_id="WH510", vessel_name="WAN HAI 510", port_code="TWKHH",
                   eta_utc=NOW + timedelta(hours=96))
    engine = build_engine(operational_rules, port_calls=[pc], normalizer=port_normalizer)
    e = make_event("e_restart", event_type=EventType.OPERATIONS, port="Kaohsiung")
    rel = engine.assess(e, now=NOW, run_id="run_restart_1")
    store1.save_snapshot(rel)
    store1.close()

    store2 = OperationalHistoryStore(db_path)
    try:
        latest = store2.get_latest("e_restart")
        assert latest is not None
        assert latest["relevance_level"] == RelevanceLevel.MODERATE
        assert latest["run_id"] == "run_restart_1"
    finally:
        store2.close()


# ══════════════════════════════════════════════════════════════
# Email Integration — WHL OPERATIONAL EXPOSURE 措辭
# ══════════════════════════════════════════════════════════════
def test_email_operational_exposure_display(operational_rules, email_rules, summary_builder,
                                             renderer, port_normalizer):
    pc = PortCall(vessel_id="WH503", vessel_name="WAN HAI 503", port_code="SGSIN",
                   eta_utc=NOW + timedelta(hours=18))
    engine = build_engine(operational_rules, vessels=[WHL_503], port_calls=[pc],
                           normalizer=port_normalizer)
    e = make_event("e_email_direct", vessel_name="WAN HAI 503", event_type=EventType.SAFETY,
                    priority=ManagementPriority.P1)
    rel = engine.assess(e, now=NOW)

    vm = build_event_view_model(e, summary_builder, email_rules, operational_relevance=rel)
    assert vm.has_operational_assessment is True
    assert vm.relevance_level == RelevanceLevel.DIRECT

    html = renderer._event_card(vm)
    assert "WHL OPERATIONAL EXPOSURE" in html
    assert "DIRECT" in html
    assert "WAN HAI 503" in html


def test_email_no_exposure_wording(operational_rules, email_rules, summary_builder, renderer,
                                    port_normalizer):
    """NONE 狀態必須使用「未發現直接曝險」而非武斷的「不受影響」措辭。"""
    engine = build_engine(operational_rules, port_calls=[], normalizer=port_normalizer)
    e = make_event("e_email_none", event_type=EventType.OPERATIONS, port="Singapore")
    rel = engine.assess(e, now=NOW)
    assert rel.relevance_level == RelevanceLevel.NONE

    vm = build_event_view_model(e, summary_builder, email_rules, operational_relevance=rel)
    html = renderer._event_card(vm)
    assert "未發現直接曝險" in html
    assert "不受影響" not in html


def test_email_unavailable_exposure_wording(operational_rules, email_rules, summary_builder,
                                             renderer):
    """Provider 失敗必須顯示 Unavailable，絕不能被誤渲染成 NONE badge。"""
    engine = build_engine(operational_rules, fleet_raise=True)
    e = make_event("e_email_unavail", event_type=EventType.SAFETY)
    rel = engine.assess(e, now=NOW)
    assert rel.relevance_status == RelevanceStatus.UNAVAILABLE

    vm = build_event_view_model(e, summary_builder, email_rules, operational_relevance=rel)
    assert vm.relevance_status == RelevanceStatus.UNAVAILABLE
    html = renderer._event_card(vm)
    assert "UNAVAILABLE" in html
    assert "Unavailable" in html
    # 不可被誤渲染成 NONE badge（Unavailable 徽章文字本身含 "UNAVAILABLE"，
    # 這裡額外確認 view model 沒有被 NONE 分支誤處理成「未發現直接曝險」文字）。
    assert vm.exposure_no_direct_text is None
