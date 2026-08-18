#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preview_email.py
海事航運新聞監控系統 — Phase 4 §preview 本機預覽工具

用途：
  用手造 fixture MaritimeEvent（不打任何 live RSS / SMTP / LLM）跑過
  BriefingSelector → ManagementSummaryBuilder → EmailViewModelBuilder →
  ExecutiveEmailRenderer，把渲染結果寫成純 HTML 檔案到 output/，
  供開發者直接用瀏覽器打開檢視版面，**絕對不會寄出真實 Email**。

Phase 5 新增：
  用 FakeLLMProvider（llm_provider.py，完全不連線）跑過
  IntelligenceAnalyzer → AnalysisValidator，示範 AI Enhanced 欄位
  （Timeline / Contradiction / AI 版摘要）如何呈現，一樣**絕對不會
  呼叫真實 LLM API 或寄出真實 Email**。

用法：
  python preview_email.py

輸出：
  output/phase4_daily_brief_preview.html   — 綜合 Daily Brief（P1+P2+P3+Resolved）
  output/phase4_p1_alert_preview.html      — 單一 P1 Alert（consolidated）
  output/phase4_no_risk_preview.html       — 無重大事件的 Daily Brief
  output/preview_early_signal.html         — EARLY SIGNAL（未驗證）P1 事件
  output/preview_resolved.html             — 純 Resolved 事件 Daily Brief
  output/phase5_ai_daily_brief_preview.html    — AI Enhanced Daily Brief（含 Timeline）
  output/phase5_ai_update_preview.html         — AI Enhanced MATERIAL_UPDATE（What Changed）
  output/phase5_ai_contradiction_preview.html  — AI 偵測到跨來源矛盾（Information Note）
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# dev_tools/ 位於 repo root 下一層：ROOT 往上一層才是專案根目錄
# （production 模組所在位置，也是 output/ 實際輸出的地方）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models import (                                    # noqa: E402
    MaritimeEvent, NewsArticle, EventType,
    InformationStatus, ManagementPriority, NotificationState, ConfidenceLevel,
)
from briefing_selector import BriefingSelector            # noqa: E402
from management_summary import ManagementSummaryBuilder    # noqa: E402
from email_view_model import (                             # noqa: E402
    build_daily_brief_view_model, build_alert_view_model,
)
from executive_email_renderer import ExecutiveEmailRenderer  # noqa: E402

# ── Phase 5：全部使用 Fake/Null，完全 offline ─────────────────────
from llm_config import load_llm_rules, LLMConfig            # noqa: E402
from llm_provider import FakeLLMProvider                    # noqa: E402
from ai_cache import NullAICache                            # noqa: E402
from intelligence_analyzer import IntelligenceAnalyzer      # noqa: E402

OUTPUT_DIR = ROOT / "output"
NOW = datetime.now(timezone.utc)


def _article(article_id, source_name, title, url, hours_ago=1) -> NewsArticle:
    return NewsArticle(
        article_id=article_id,
        source_name=source_name,
        title=title,
        summary=title,
        url=url,
        published_at=NOW - timedelta(hours=hours_ago),
        collected_at=NOW,
    )


# ══════════════════════════════════════════════════════════════
# Fixture 事件（手造，模擬各種情境，不依賴任何 live 資料來源）
# ══════════════════════════════════════════════════════════════
def make_red_sea_attack_p1() -> MaritimeEvent:
    a1 = _article("rs1", "Reuters", "Container vessel reports attack in Red Sea", "https://reuters.com/rs1", 1)
    a2 = _article("rs2", "UKMTO", "UKMTO incident advisory — Red Sea", "https://ukmto.org/rs2", 1)
    a3 = _article("rs3", "TradeWinds", "Vessel attacked off Yemen coast", "https://tradewindsnews.com/rs3", 2)
    return MaritimeEvent(
        event_id="evt_red_sea_attack",
        headline="Container vessel reports attack in Red Sea",
        event_type=EventType.SECURITY, incident_subtype="VESSEL_ATTACK",
        vessel_type="CONTAINER_SHIP", location="紅海 / Red Sea", sea_area="RED_SEA",
        management_priority=ManagementPriority.P1, management_score=92,
        confidence_level=ConfidenceLevel.HIGH,
        information_status=InformationStatus.CORROBORATED,
        impact_tags=["SECURITY", "NAVIGATION", "SCHEDULE"],
        fleet_relevance_score=20,
        notification_state=NotificationState.NEW,
        primary_article=a2, articles=[a1, a2, a3],
        article_count=3, independent_source_count=3,
        last_updated=NOW - timedelta(hours=1),
    )


def make_singapore_terminal_p2() -> MaritimeEvent:
    a1 = _article("sg1", "Splash247", "Singapore terminal operations disrupted after equipment failure", "https://splash247.com/sg1", 3)
    return MaritimeEvent(
        event_id="evt_singapore_terminal",
        headline="Singapore terminal operations disrupted",
        event_type=EventType.OPERATIONS, incident_subtype="PORT_DISRUPTION",
        location="新加坡海峽 / Singapore Strait", port="新加坡港 / Port of Singapore",
        port_status="CONGESTED",
        management_priority=ManagementPriority.P2, management_score=58,
        confidence_level=ConfidenceLevel.MEDIUM,
        information_status=InformationStatus.UNCONFIRMED,
        impact_tags=["PORT", "SCHEDULE", "TERMINAL"],
        fleet_relevance_score=14,
        notification_state=NotificationState.NEW,
        primary_article=a1, articles=[a1],
        article_count=1, independent_source_count=1,
        last_updated=NOW - timedelta(hours=3),
    )


def make_imo_regulatory_p3() -> MaritimeEvent:
    a1 = _article("imo1", "Lloyd's List", "IMO adopts new cargo safety reporting requirement", "https://lloydslist.com/imo1", 8)
    return MaritimeEvent(
        event_id="evt_imo_cargo_rule",
        headline="IMO adopts new cargo safety requirement",
        event_type=EventType.REGULATORY, incident_subtype=None,
        management_priority=ManagementPriority.P3, management_score=30,
        confidence_level=ConfidenceLevel.HIGH,
        information_status=InformationStatus.CONFIRMED,
        impact_tags=["REGULATORY", "CARGO"],
        fleet_relevance_score=16,
        notification_state=NotificationState.NEW,
        primary_article=a1, articles=[a1],
        article_count=1, independent_source_count=1,
        last_updated=NOW - timedelta(hours=8),
    )


def make_wan_hai_grounding_resolved() -> MaritimeEvent:
    a1 = _article("wh1", "gCaptain", "Wan Hai vessel refloated after grounding near Kaohsiung", "https://gcaptain.com/wh1", 6)
    return MaritimeEvent(
        event_id="evt_wanhai_grounding",
        headline="Wan Hai vessel refloated after grounding",
        event_type=EventType.SAFETY, incident_subtype="GROUNDING",
        vessel_name="Wan Hai 307", carrier="WAN_HAI",
        location="高雄港 / Kaohsiung Port", port="高雄港 / Kaohsiung Port",
        vessel_status="REFLOATED",
        management_priority=ManagementPriority.P1, management_score=70,
        confidence_level=ConfidenceLevel.HIGH,
        information_status=InformationStatus.CONFIRMED,
        impact_tags=["NAVIGATION", "SCHEDULE"],
        fleet_relevance_score=25,
        notification_state=NotificationState.RESOLVED_UPDATE,
        change_reason="Resolution confirmed by source text",
        primary_article=a1, articles=[a1],
        article_count=1, independent_source_count=1,
        last_updated=NOW - timedelta(hours=1),
    )


def make_early_signal_p1() -> MaritimeEvent:
    """單一 Tier D（Reddit/社群）來源，僅供 EARLY SIGNAL 展示用。"""
    a1 = _article("es1", "r/maritime", "Unconfirmed report: fire reported aboard container ship near Singapore Strait", "https://reddit.com/r/maritime/es1", 0)
    a1.source_tier = "D"
    return MaritimeEvent(
        event_id="evt_early_signal_fire",
        headline="Unconfirmed report of fire near Singapore Strait",
        event_type=EventType.SAFETY, incident_subtype="FIRE",
        vessel_type="CONTAINER_SHIP", location="新加坡海峽 / Singapore Strait", sea_area="SINGAPORE_STRAIT",
        fire_status="ONGOING",
        management_priority=ManagementPriority.P1, management_score=75,
        confidence_level=ConfidenceLevel.LOW,
        information_status=InformationStatus.EARLY_SIGNAL,
        impact_tags=["NAVIGATION", "SECURITY"],
        fleet_relevance_score=18,
        notification_state=NotificationState.NEW,
        primary_article=a1, articles=[a1],
        article_count=1, independent_source_count=1,
        last_updated=NOW,
    )


def make_red_sea_attack_material_update() -> MaritimeEvent:
    """§二十：同一事件的 MATERIAL_UPDATE 情境，供 AI What Changed 展示用。"""
    a1 = _article("rsu1", "Reuters", "Container vessel reports attack in Red Sea", "https://reuters.com/rs1", 3)
    a2 = _article("rsu2", "UKMTO", "UKMTO incident advisory — Red Sea", "https://ukmto.org/rs2", 2)
    a3 = _article("rsu3", "TradeWinds", "Crew injury confirmed after Red Sea vessel attack", "https://tradewindsnews.com/rs3", 1)
    return MaritimeEvent(
        event_id="evt_red_sea_attack",
        headline="Container vessel reports attack in Red Sea",
        event_type=EventType.SECURITY, incident_subtype="VESSEL_ATTACK",
        vessel_type="CONTAINER_SHIP", location="紅海 / Red Sea", sea_area="RED_SEA",
        casualty_status="INJURED",
        management_priority=ManagementPriority.P1, management_score=95,
        confidence_level=ConfidenceLevel.HIGH,
        information_status=InformationStatus.CORROBORATED,
        impact_tags=["SECURITY", "NAVIGATION", "CREW", "SCHEDULE"],
        fleet_relevance_score=20,
        notification_state=NotificationState.MATERIAL_UPDATE,
        change_reason="Casualty status changed unknown → INJURED; Confidence upgraded MEDIUM → HIGH",
        version=2,
        primary_article=a2, articles=[a1, a2, a3],
        article_count=3, independent_source_count=3,
        last_updated=NOW - timedelta(hours=1),
    )


def make_contradiction_event() -> MaritimeEvent:
    """§二十三：跨來源矛盾情境 — 一方稱無傷亡，另一方稱有船員受傷。"""
    a1 = _article("cx1", "Reuters", "Official source: no casualties in Red Sea vessel incident", "https://reuters.com/cx1", 2)
    a2 = _article("cx2", "gCaptain", "Crew member reportedly injured in Red Sea vessel incident", "https://gcaptain.com/cx2", 1)
    return MaritimeEvent(
        event_id="evt_contradiction_case",
        headline="Conflicting reports on Red Sea vessel incident casualties",
        event_type=EventType.SECURITY, incident_subtype="VESSEL_ATTACK",
        vessel_type="CONTAINER_SHIP", location="紅海 / Red Sea", sea_area="RED_SEA",
        management_priority=ManagementPriority.P1, management_score=88,
        confidence_level=ConfidenceLevel.MEDIUM,
        information_status=InformationStatus.UNCONFIRMED,
        impact_tags=["SECURITY", "CREW"],
        fleet_relevance_score=18,
        notification_state=NotificationState.NEW,
        primary_article=a1, articles=[a1, a2],
        article_count=2, independent_source_count=2,
        last_updated=NOW,
    )


def build_selector_and_summary():
    bs = BriefingSelector()
    sb = ManagementSummaryBuilder()
    return bs, sb


def run_fake_llm_analysis(event: MaritimeEvent, canned_json: dict):
    """
    用 FakeLLMProvider + NullAICache 跑一次 IntelligenceAnalyzer.analyze_event()，
    回傳 (IntelligenceAnalysis|None, status)。完全不連線、不快取到磁碟，
    純粹展示 Phase 5 pipeline 的輸出長相。
    """
    llm_rules = load_llm_rules()
    cfg = LLMConfig(
        enabled=True, provider="fake", model="fake-preview-model",
        anthropic_api_key="", openai_api_key="",
        timeout_seconds=30, max_retries=2, failure_circuit_breaker=3,
    )
    provider = FakeLLMProvider(mode="valid", canned_json=canned_json)
    analyzer = IntelligenceAnalyzer(cfg, llm_rules, provider, NullAICache())
    return analyzer.analyze_event(event)


def write_html(filename: str, html: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    path.write_text(html, encoding="utf-8")
    try:
        display_path = path.relative_to(ROOT)
    except ValueError:
        display_path = path
    print(f"  ✅ {display_path}  ({len(html):,} bytes)")


def main():
    print("Phase 4 — Executive Email 本機預覽產生器（不寄送真實 Email）")
    print("=" * 64)

    bs, sb = build_selector_and_summary()
    renderer = ExecutiveEmailRenderer()

    # 1. Daily Brief — 綜合情境（P1 + P2 + P3 + Resolved）
    events = [
        make_red_sea_attack_p1(),
        make_singapore_terminal_p2(),
        make_imo_regulatory_p3(),
        make_wan_hai_grounding_resolved(),
    ]
    selection = bs.select(events)
    vm = build_daily_brief_view_model(selection, summary_builder=sb)
    html = renderer.render_daily_brief(vm)
    print(f"\n[1] Daily Brief — Subject: {vm.subject}")
    write_html("phase4_daily_brief_preview.html", html)

    # 2. P1 Alert — 單一事件（consolidated single-email 情境）
    alert_events = [make_red_sea_attack_p1()]
    alert_selection = bs.select(alert_events)
    alert_vm = build_alert_view_model(alert_selection, summary_builder=sb)
    alert_html = renderer.render_alert(alert_vm)
    print(f"\n[2] P1 Alert — Subject: {alert_vm.subject}")
    write_html("phase4_p1_alert_preview.html", alert_html)

    # 3. No Risk — 空事件列表
    no_risk_selection = bs.select([])
    no_risk_vm = build_daily_brief_view_model(no_risk_selection, summary_builder=sb)
    no_risk_html = renderer.render_daily_brief(no_risk_vm)
    print(f"\n[3] No Risk — Subject: {no_risk_vm.subject}")
    write_html("phase4_no_risk_preview.html", no_risk_html)

    # 4. Early Signal — 單一 Tier D 未驗證來源
    early_selection = bs.select([make_early_signal_p1()])
    early_vm = build_alert_view_model(early_selection, summary_builder=sb)
    early_html = renderer.render_alert(early_vm)
    print(f"\n[4] Early Signal — Subject: {early_vm.subject}")
    write_html("preview_early_signal.html", early_html)

    # 5. Resolved — 純 Resolved 情境
    resolved_selection = bs.select([make_wan_hai_grounding_resolved()])
    resolved_vm = build_daily_brief_view_model(resolved_selection, summary_builder=sb)
    resolved_html = renderer.render_daily_brief(resolved_vm)
    print(f"\n[5] Resolved — Subject: {resolved_vm.subject}")
    write_html("preview_resolved.html", resolved_html)

    # ══════════════════════════════════════════════════════════
    # Phase 5 — AI Enhanced Previews（FakeLLMProvider，完全 offline）
    # ══════════════════════════════════════════════════════════
    print("\n" + "-" * 64)
    print("Phase 5 — AI Enhanced Preview（FakeLLMProvider，不連線）")
    print("-" * 64)

    # 6. AI Enhanced Daily Brief — P1 事件 + Timeline
    ai_event_1 = make_red_sea_attack_p1()
    ai_canned_1 = {
        "management_summary_zh": "一艘商船於紅海南部航行期間遭疑似攻擊並受損；UKMTO 與 Reuters 的資訊目前一致確認事故發生，尚無可靠來源證實人員傷亡。",
        "why_it_matters_zh": "紅海為重要商船航線，此類攻擊事件可能提高航行風險與 War Risk exposure，若航線涉及該區域需持續評估繞航必要性。",
        "what_changed_zh": "",
        "timeline": [
            {"time": (NOW - timedelta(hours=2)).isoformat(), "summary_zh": "首次接獲商船疑似遭攻擊通報", "source_ids": ["S1"]},
            {"time": (NOW - timedelta(hours=1)).isoformat(), "summary_zh": "UKMTO 發布事故通告，確認事件發生", "source_ids": ["S1", "S2"]},
        ],
        "confirmed_facts": [
            {"fact": "事件發生於紅海南部", "source_ids": ["S1", "S2"]},
        ],
        "unconfirmed_claims": [],
        "contradictions": [],
        "monitoring_points": ["持續關注官方是否發布航行警告", "關注是否有進一步人員傷亡通報"],
        "source_support": ["S1", "S2"],
        "analysis_confidence": "MEDIUM",
    }
    ai_analysis_1, status_1 = run_fake_llm_analysis(ai_event_1, ai_canned_1)
    ai_selection_1 = bs.select([ai_event_1, make_singapore_terminal_p2(), make_imo_regulatory_p3()])
    ai_vm_1 = build_daily_brief_view_model(
        ai_selection_1, summary_builder=sb,
        ai_analyses={ai_event_1.event_id: ai_analysis_1} if ai_analysis_1 else {},
    )
    ai_html_1 = renderer.render_daily_brief(ai_vm_1)
    print(f"\n[6] AI Enhanced Daily Brief — status={status_1} — Subject: {ai_vm_1.subject}")
    write_html("phase5_ai_daily_brief_preview.html", ai_html_1)

    # 7. AI Enhanced MATERIAL_UPDATE — What Changed 由 AI 改寫成自然語句
    ai_event_2 = make_red_sea_attack_material_update()
    ai_canned_2 = {
        "management_summary_zh": "紅海南部商船遭攻擊事件最新資訊確認 1 名船員受傷，事故已由單一消息來源升級為三個獨立來源交叉確認。",
        "why_it_matters_zh": "船員傷亡已獲證實，需關注後續醫療後送安排；事件持續獲多方來源確認，顯示資訊可信度提升。",
        "what_changed_zh": "最新資訊確認 1 名船員受傷，且情報可信度已由 MEDIUM 升級為 HIGH（三個獨立來源交叉確認）。",
        "timeline": [
            {"time": (NOW - timedelta(hours=3)).isoformat(), "summary_zh": "首次接獲攻擊通報", "source_ids": ["S1"]},
            {"time": (NOW - timedelta(hours=1)).isoformat(), "summary_zh": "確認 1 名船員受傷", "source_ids": ["S3"]},
        ],
        "confirmed_facts": [
            {"fact": "1 名船員受傷", "source_ids": ["S3"]},
        ],
        "unconfirmed_claims": [],
        "contradictions": [],
        "monitoring_points": ["持續關注船員醫療後送安排", "關注船舶是否需要轉往鄰近港口"],
        "source_support": ["S1", "S2", "S3"],
        "analysis_confidence": "HIGH",
    }
    ai_analysis_2, status_2 = run_fake_llm_analysis(ai_event_2, ai_canned_2)
    ai_selection_2 = bs.select([ai_event_2])
    ai_vm_2 = build_alert_view_model(
        ai_selection_2, summary_builder=sb,
        ai_analyses={ai_event_2.event_id: ai_analysis_2} if ai_analysis_2 else {},
    )
    ai_html_2 = renderer.render_alert(ai_vm_2)
    print(f"\n[7] AI Enhanced Material Update — status={status_2} — Subject: {ai_vm_2.subject}")
    write_html("phase5_ai_update_preview.html", ai_html_2)

    # 8. AI Contradiction Detection — 跨來源矛盾（Information Note）
    ai_event_3 = make_contradiction_event()
    ai_canned_3 = {
        "management_summary_zh": "紅海商船事故目前來源對人員傷亡情況說法不一，官方消息稱無人受傷，另有媒體報導疑似有船員受傷，尚待進一步確認。",
        "why_it_matters_zh": "人員安全狀況尚未一致確認，需持續關注官方後續澄清，避免以未確認資訊做出判斷。",
        "what_changed_zh": "",
        "timeline": [],
        "confirmed_facts": [
            {"fact": "事件發生於紅海", "source_ids": ["S1", "S2"]},
        ],
        "unconfirmed_claims": [
            {"claim": "可能有船員受傷", "source_ids": ["S2"]},
        ],
        "contradictions": [
            {
                "topic": "船員傷亡情況", "source_a": "S1", "claim_a": "官方來源稱無人受傷",
                "source_b": "S2", "claim_b": "媒體報導疑似有船員受傷", "status": "UNRESOLVED",
            },
        ],
        "monitoring_points": ["持續關注官方是否發布正式澄清"],
        "source_support": ["S1", "S2"],
        "analysis_confidence": "MEDIUM",
    }
    ai_analysis_3, status_3 = run_fake_llm_analysis(ai_event_3, ai_canned_3)
    ai_selection_3 = bs.select([ai_event_3])
    ai_vm_3 = build_alert_view_model(
        ai_selection_3, summary_builder=sb,
        ai_analyses={ai_event_3.event_id: ai_analysis_3} if ai_analysis_3 else {},
    )
    ai_html_3 = renderer.render_alert(ai_vm_3)
    print(f"\n[8] AI Contradiction Detection — status={status_3} — Subject: {ai_vm_3.subject}")
    write_html("phase5_ai_contradiction_preview.html", ai_html_3)

    print("\n" + "=" * 64)
    print("完成。以上 8 個檔案皆為純 HTML，未經任何 SMTP / live 網路呼叫（Phase 5 場景額外未呼叫任何真實 LLM API）。")


if __name__ == "__main__":
    main()
