"""
tests/test_phase4_email.py
Phase 4 — Executive Maritime Intelligence Email 單元測試

全部使用手造 fixture MaritimeEvent／NewsArticle，不依賴任何 live RSS /
SMTP / LLM。SMTP 相關行為（若有）一律 mock，不會真的連線。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models import (                                       # noqa: E402
    MaritimeEvent, NewsArticle, EventType,
    InformationStatus, ManagementPriority, NotificationState, ConfidenceLevel,
)
from risk_config import load_risk_rules                     # noqa: E402
from email_config import load_email_rules                   # noqa: E402
from briefing_selector import BriefingSelector               # noqa: E402
from management_summary import ManagementSummaryBuilder, translate_change_reason  # noqa: E402
from email_view_model import (                               # noqa: E402
    build_daily_brief_view_model, build_alert_view_model,
)
from executive_email_renderer import ExecutiveEmailRenderer   # noqa: E402

NOW = datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════
# Fixtures / 工廠函式
# ══════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def risk_rules():
    return load_risk_rules(str(ROOT / "risk_rules.json"))


@pytest.fixture(scope="module")
def email_rules():
    return load_email_rules(str(ROOT / "email_rules.json"))


@pytest.fixture
def selector(email_rules, risk_rules):
    return BriefingSelector(email_rules=email_rules, risk_rules=risk_rules)


@pytest.fixture
def summary_builder(risk_rules):
    return ManagementSummaryBuilder(risk_rules=risk_rules)


@pytest.fixture
def renderer():
    return ExecutiveEmailRenderer()


def make_article(article_id="a1", source_name="Reuters", title="Test headline",
                  url="https://reuters.com/x", hours_ago=1) -> NewsArticle:
    return NewsArticle(
        article_id=article_id, source_name=source_name, title=title,
        summary=title, url=url,
        published_at=NOW - timedelta(hours=hours_ago), collected_at=NOW,
    )


def make_event(event_id="e1", priority=ManagementPriority.P2,
               notification_state=NotificationState.NEW,
               event_type=EventType.SAFETY, incident_subtype=None,
               carrier=None, vessel_name=None, location=None,
               information_status=InformationStatus.CORROBORATED,
               confidence_level=ConfidenceLevel.MEDIUM,
               fleet_relevance_score=10, management_score=50,
               change_reason=None, articles=None, primary_article=None,
               **status_fields) -> MaritimeEvent:
    articles = articles if articles is not None else [make_article(article_id=f"{event_id}_a1")]
    primary_article = primary_article or articles[0]
    return MaritimeEvent(
        event_id=event_id, headline=f"Headline for {event_id}",
        event_type=event_type, incident_subtype=incident_subtype,
        carrier=carrier, vessel_name=vessel_name, location=location,
        management_priority=priority, management_score=management_score,
        confidence_level=confidence_level, information_status=information_status,
        fleet_relevance_score=fleet_relevance_score,
        notification_state=notification_state, change_reason=change_reason,
        primary_article=primary_article, articles=articles,
        article_count=len(articles), independent_source_count=len(articles),
        last_updated=NOW, impact_tags=[],
        **status_fields,
    )


# ══════════════════════════════════════════════════════════════
# BriefingSelector
# ══════════════════════════════════════════════════════════════
def test_briefing_selector_p1(selector):
    e = make_event("p1_evt", priority=ManagementPriority.P1,
                    notification_state=NotificationState.NEW)
    sel = selector.select([e])
    assert sel["immediate"] == [e]
    assert sel["watch"] == []
    assert sel["industry"] == []
    assert sel["resolved"] == []


def test_briefing_selector_p2(selector):
    e = make_event("p2_evt", priority=ManagementPriority.P2,
                    notification_state=NotificationState.MATERIAL_UPDATE)
    sel = selector.select([e])
    assert sel["watch"] == [e]
    assert sel["immediate"] == []


def test_p4_suppressed(selector):
    e = make_event("p4_evt", priority=ManagementPriority.P4,
                    notification_state=NotificationState.NEW)
    sel = selector.select([e])
    assert e in sel["suppressed"]
    assert e not in sel["industry"]


def test_unchanged_suppressed(selector):
    e = make_event("unchanged_evt", priority=ManagementPriority.P1,
                    notification_state=NotificationState.UNCHANGED)
    sel = selector.select([e])
    assert e in sel["suppressed"]
    assert e not in sel["immediate"]


def test_minor_update_suppressed(selector):
    e = make_event("minor_evt", priority=ManagementPriority.P2,
                    notification_state=NotificationState.MINOR_UPDATE)
    sel = selector.select([e])
    assert e in sel["suppressed"]
    assert e not in sel["watch"]


def test_resolved_section(selector):
    e = make_event("resolved_evt", priority=ManagementPriority.P1,
                    notification_state=NotificationState.RESOLVED_UPDATE)
    sel = selector.select([e])
    assert sel["resolved"] == [e]
    assert e not in sel["immediate"]


def test_own_fleet_sorted_first(selector):
    e_other = make_event("other_p1", priority=ManagementPriority.P1,
                          notification_state=NotificationState.NEW,
                          carrier="MSC", management_score=99)
    e_own = make_event("own_p1", priority=ManagementPriority.P1,
                        notification_state=NotificationState.NEW,
                        carrier="WAN_HAI", management_score=40)
    sel = selector.select([e_other, e_own])
    # 同一個 Priority tier 內，Own Fleet 優先排在前面，即使 management_score 較低
    assert sel["immediate"][0].event_id == "own_p1"
    assert sel["immediate"][1].event_id == "other_p1"


# ══════════════════════════════════════════════════════════════
# Overall Risk（純 bucket 存在性的函式，見 email_view_model.py）
# ══════════════════════════════════════════════════════════════
def test_overall_risk_high(selector, summary_builder):
    e = make_event("p1_evt", priority=ManagementPriority.P1,
                    notification_state=NotificationState.NEW)
    sel = selector.select([e])
    vm = build_daily_brief_view_model(sel, summary_builder=summary_builder)
    assert vm.overall_risk == "HIGH"


def test_overall_risk_normal(selector, summary_builder):
    sel = selector.select([])
    vm = build_daily_brief_view_model(sel, summary_builder=summary_builder)
    assert vm.overall_risk == "NORMAL"
    assert vm.is_no_risk is True


# ══════════════════════════════════════════════════════════════
# ManagementSummaryBuilder — 中文摘要 templates
# ══════════════════════════════════════════════════════════════
def test_management_summary_fire(summary_builder):
    e = make_event("fire_evt", event_type=EventType.SAFETY, incident_subtype="FIRE",
                    vessel_name="MV Test Star", location="紅海 / Red Sea",
                    fire_status="ONGOING", information_status=InformationStatus.CONFIRMED)
    summary = summary_builder.management_summary(e)
    assert "火災" in summary
    assert "紅海" in summary
    assert "MV Test Star" in summary


def test_management_summary_security(summary_builder):
    e = make_event("attack_evt", event_type=EventType.SECURITY, incident_subtype="VESSEL_ATTACK",
                    location="紅海 / Red Sea", information_status=InformationStatus.CORROBORATED)
    summary = summary_builder.management_summary(e)
    assert "攻擊" in summary
    assert "紅海" in summary


def test_management_summary_port(summary_builder):
    e = make_event("port_evt", event_type=EventType.OPERATIONS, incident_subtype="PORT_DISRUPTION",
                    location="新加坡海峽 / Singapore Strait", port_status="CONGESTED",
                    information_status=InformationStatus.CORROBORATED)
    summary = summary_builder.management_summary(e)
    assert "新加坡海峽" in summary
    assert "壅塞" in summary
    assert "靠離泊" in summary or "船期" in summary


def test_early_signal_wording(summary_builder):
    """§二十二：EARLY_SIGNAL / UNCONFIRMED 事件的摘要必須使用保留詞，不可寫成已確認事實。"""
    e = make_event("early_evt", event_type=EventType.SAFETY, incident_subtype="FIRE",
                    location="新加坡海峽 / Singapore Strait", fire_status="ONGOING",
                    information_status=InformationStatus.EARLY_SIGNAL)
    summary = summary_builder.management_summary(e)
    assert "據報" in summary or "疑似" in summary or "初步" in summary


def test_material_update_what_changed(summary_builder):
    e = make_event("update_evt", notification_state=NotificationState.MATERIAL_UPDATE,
                    change_reason="Priority escalated P2 → P1; Confidence upgraded LOW → HIGH")
    bullets = summary_builder.what_changed(e)
    assert any("P2" in b and "P1" in b for b in bullets)
    assert any("LOW" in b and "HIGH" in b for b in bullets)
    # 不得出現原始英文片語未經翻譯直接外流成主要內容
    assert "escalated" not in " ".join(bullets)


def test_resolved_wording(summary_builder):
    e = make_event("resolved_evt", notification_state=NotificationState.RESOLVED_UPDATE,
                    change_reason="Resolution confirmed by source text")
    bullets = summary_builder.what_changed(e)
    assert any("解除" in b for b in bullets)


# ══════════════════════════════════════════════════════════════
# Subject Line
# ══════════════════════════════════════════════════════════════
def test_subject_p1_alert(selector, summary_builder):
    e = make_event("p1_evt", priority=ManagementPriority.P1,
                    notification_state=NotificationState.NEW)
    sel = selector.select([e])
    vm = build_alert_view_model(sel, summary_builder=summary_builder)
    # 品牌前綴統一為 GITHUB_Maritime Intel News Alert（2026-08，見
    # email_rules.json §subject._note）——不再是舊的 "Maritime Alert"。
    assert "GITHUB_Maritime Intel News Alert" in vm.subject
    assert "P1" in vm.subject


def test_subject_daily_brief(selector, summary_builder):
    e1 = make_event("p1_evt", priority=ManagementPriority.P1,
                     notification_state=NotificationState.NEW)
    e2 = make_event("p2_evt", priority=ManagementPriority.P2,
                     notification_state=NotificationState.NEW)
    sel = selector.select([e1, e2])
    vm = build_daily_brief_view_model(sel, summary_builder=summary_builder, generated_at=NOW)
    assert "Daily Brief" in vm.subject
    assert "P1:1" in vm.subject
    assert "P2:1" in vm.subject


def test_subject_no_risk(selector, summary_builder):
    sel = selector.select([])
    vm = build_daily_brief_view_model(sel, summary_builder=summary_builder, generated_at=NOW)
    assert "No Major Fleet Risk" in vm.subject


# ══════════════════════════════════════════════════════════════
# HTML Renderer
# ══════════════════════════════════════════════════════════════
def test_html_outlook_table_structure(selector, summary_builder, renderer):
    """table-based inline CSS、無 Grid/Flexbox/JS，open/close table 標籤數量相等。"""
    e = make_event("p1_evt", priority=ManagementPriority.P1,
                    notification_state=NotificationState.NEW)
    sel = selector.select([e])
    vm = build_daily_brief_view_model(sel, summary_builder=summary_builder)
    html = renderer.render_daily_brief(vm)
    assert html.count("<table") == html.count("</table>")
    assert "<table" in html
    assert "display:flex" not in html
    assert "display: flex" not in html
    assert "grid-template" not in html
    assert "<script" not in html.lower()


def test_html_no_none_values(selector, summary_builder, renderer):
    """欄位為 None 時整行省略，不得渲染成 'Vessel: None' 之類的字串。"""
    e = make_event("no_vessel_evt", priority=ManagementPriority.P1,
                    notification_state=NotificationState.NEW,
                    vessel_name=None, carrier=None, location=None)
    sel = selector.select([e])
    vm = build_daily_brief_view_model(sel, summary_builder=summary_builder)
    html = renderer.render_daily_brief(vm)
    assert "None" not in html
    assert "Vessel: None" not in html
    assert "Carrier: None" not in html


def test_html_source_links(selector, summary_builder, renderer):
    a = make_article(article_id="lnk1", source_name="Reuters",
                      url="https://reuters.com/some-article", title="Test")
    e = make_event("link_evt", priority=ManagementPriority.P1,
                    notification_state=NotificationState.NEW,
                    articles=[a], primary_article=a)
    sel = selector.select([e])
    vm = build_daily_brief_view_model(sel, summary_builder=summary_builder)
    html = renderer.render_daily_brief(vm)
    assert "https://reuters.com/some-article" in html
    assert "Read Primary Source" in html
    # 不得把完整 URL dump 一大串來源清單，只顯示計數 + 名稱
    assert "Reuters" in html


def test_html_escapes_untrusted_text(selector, summary_builder, renderer):
    """來源標題/名稱等未信任文字必須 HTML escape，避免注入 <script>。"""
    a = make_article(article_id="xss1", source_name='Reuters<script>alert(1)</script>',
                      title='Fire aboard "Test Star" & other <b>markup</b>',
                      url="https://reuters.com/xss")
    e = make_event("xss_evt", priority=ManagementPriority.P1,
                    notification_state=NotificationState.NEW,
                    articles=[a], primary_article=a)
    sel = selector.select([e])
    vm = build_daily_brief_view_model(sel, summary_builder=summary_builder)
    html = renderer.render_daily_brief(vm)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_html_rejects_unsafe_url_scheme(renderer):
    """URL scheme allow-list：僅允許 http/https，javascript:/data:/file: 一律拒絕。"""
    assert renderer.safe_url("https://reuters.com/x") is not None
    assert renderer.safe_url("http://reuters.com/x") is not None
    assert renderer.safe_url("javascript:alert(1)") is None
    assert renderer.safe_url("data:text/html,<script>alert(1)</script>") is None
    assert renderer.safe_url("file:///etc/passwd") is None
    assert renderer.safe_url(None) is None


# ══════════════════════════════════════════════════════════════
# Preview 產生（不寄送真實 Email）
# ══════════════════════════════════════════════════════════════
def test_preview_generation(tmp_path, monkeypatch):
    """
    preview_email.py 可以在不連線 SMTP 的情況下產生 5 個 HTML 檔案。
    為了不弄髒 repo 的 output/ 目錄，這裡把輸出導到 tmp_path。
    """
    import importlib
    import preview_email as pe

    monkeypatch.setattr(pe, "OUTPUT_DIR", tmp_path)
    pe.main()

    expected = [
        "phase4_daily_brief_preview.html",
        "phase4_p1_alert_preview.html",
        "phase4_no_risk_preview.html",
        "preview_early_signal.html",
        "preview_resolved.html",
    ]
    for filename in expected:
        f = tmp_path / filename
        assert f.exists(), f"{filename} was not generated"
        content = f.read_text(encoding="utf-8")
        assert "<html>" in content
        assert content.count("<table") == content.count("</table>")
