#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preview_teams.py
海事航運新聞監控系統 — Phase 7 §八十九 Teams Offline Preview

用途：
  用手造 fixture MaritimeEvent + OperationalRelevance + DeliveryDecision
  跑過 teams_renderer.render()，把結果寫成純文字檔到 output/，供開發者
  直接檢視訊息內容，**絕對不會呼叫真實 Teams webhook**（全程不 import
  requests 呼叫路徑，只有 render()，沒有 notifier.send()）。

用法：
  python preview_teams.py

輸出：
  output/teams_preview_p1.txt                — 一般 P1 Immediate Alert
  output/teams_preview_own_fleet.txt         — Own Fleet P1
  output/teams_preview_early_signal.txt      — Early Signal / Unconfirmed P1
  output/teams_preview_exposure_escalation.txt — P2 UNCHANGED + Exposure Escalated（Dual-Axis 最重要案例）
  output/teams_preview_resolved.txt          — P1 Resolved + Exposure Cleared
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
from operational_models import OperationalRelevance, AffectedVessel, RelevanceLevel, RelevanceStatus  # noqa: E402
from delivery_models import DeliveryDecision, DeliveryUrgency, DeliveryChannel, TeamsMode  # noqa: E402
import teams_renderer                                     # noqa: E402

OUTPUT_DIR = ROOT / "output"
NOW = datetime.now(timezone.utc)
DASHBOARD_URL = "http://127.0.0.1:8000"


def _article(article_id, source_name, title, url, hours_ago=1) -> NewsArticle:
    return NewsArticle(
        article_id=article_id, source_name=source_name, title=title, summary=title,
        url=url, published_at=NOW - timedelta(hours=hours_ago), collected_at=NOW,
    )


def _decision(event_id, urgency, teams_mode, reason) -> DeliveryDecision:
    return DeliveryDecision(
        event_id=event_id, delivery_reason=reason, urgency=urgency,
        channels=[DeliveryChannel.EMAIL, DeliveryChannel.TEAMS, DeliveryChannel.DASHBOARD],
        teams_mode=teams_mode, created_at=NOW,
    )


# ══════════════════════════════════════════════════════════════
# 1. 一般 P1 Immediate Alert
# ══════════════════════════════════════════════════════════════
def preview_p1():
    a1 = _article("rs1", "Reuters", "Container vessel attacked in Red Sea", "https://reuters.com/rs1")
    a2 = _article("rs2", "TradeWinds", "Vessel attack off Yemen coast", "https://tradewindsnews.com/rs2")
    event = MaritimeEvent(
        event_id="evt_p1_general", headline="Red Sea — Container Vessel Attacked",
        event_type=EventType.SECURITY, incident_subtype="VESSEL_ATTACK",
        location="Red Sea", sea_area="RED_SEA",
        management_priority=ManagementPriority.P1, management_score=92,
        confidence_level=ConfidenceLevel.HIGH, information_status=InformationStatus.CORROBORATED,
        notification_state=NotificationState.MATERIAL_UPDATE,
        change_reason="Crew casualty confirmed.",
        primary_article=a1, articles=[a1, a2], article_count=2, independent_source_count=2,
        last_updated=NOW,
    )
    rel = OperationalRelevance(
        event_id=event.event_id, relevance_level=RelevanceLevel.HIGH, relevance_score=62,
        relevance_status=RelevanceStatus.ASSESSED,
        affected_vessels=[AffectedVessel(vessel_name="WAN HAI 510", service_code="AEX1",
                                          next_port="SGSIN", eta_display=None,
                                          exposure_type="PORT_CALL", hours_to_exposure=30.0)],
        affected_services=["AEX1"],
    )
    decision = _decision(event.event_id, DeliveryUrgency.IMMEDIATE, TeamsMode.IMMEDIATE,
                          "P1 MATERIAL_UPDATE with HIGH confidence")
    return teams_renderer.render(event, rel, decision, dashboard_base_url=DASHBOARD_URL)


# ══════════════════════════════════════════════════════════════
# 2. Own Fleet P1
# ══════════════════════════════════════════════════════════════
def preview_own_fleet():
    a1 = _article("of1", "TradeWinds", "WAN HAI 503 fire reported off Kaohsiung", "https://tradewindsnews.com/of1")
    event = MaritimeEvent(
        event_id="evt_own_fleet_fire", headline="WAN HAI 503 — Fire Reported Off Kaohsiung",
        event_type=EventType.SAFETY, incident_subtype="FIRE",
        vessel_name="WAN HAI 503", carrier="WAN_HAI", location="Kaohsiung", port="Kaohsiung",
        management_priority=ManagementPriority.P1, management_score=95,
        confidence_level=ConfidenceLevel.HIGH, information_status=InformationStatus.CORROBORATED,
        notification_state=NotificationState.MATERIAL_UPDATE,
        change_reason="Fire status updated: crew fighting fire, no injuries reported.",
        primary_article=a1, articles=[a1], article_count=1, independent_source_count=1,
        last_updated=NOW,
    )
    rel = OperationalRelevance(
        event_id=event.event_id, relevance_level=RelevanceLevel.DIRECT, relevance_score=90,
        relevance_status=RelevanceStatus.ASSESSED, own_fleet_involved=True,
    )
    decision = _decision(event.event_id, DeliveryUrgency.IMMEDIATE, TeamsMode.IMMEDIATE,
                          "Own fleet vessel involved (P1)")
    return teams_renderer.render(event, rel, decision, dashboard_base_url=DASHBOARD_URL)


# ══════════════════════════════════════════════════════════════
# 3. Early Signal / Unconfirmed P1
# ══════════════════════════════════════════════════════════════
def preview_early_signal():
    a1 = _article("es1", "Reddit r/maritime", "Possible incident near Singapore Strait",
                   "https://reddit.com/r/maritime/es1")
    event = MaritimeEvent(
        event_id="evt_early_signal", headline="Possible Maritime Security Incident Near Singapore Strait",
        event_type=EventType.SECURITY, location="Singapore Strait", sea_area="SINGAPORE_STRAIT",
        management_priority=ManagementPriority.P1, management_score=80,
        confidence_level=ConfidenceLevel.LOW, information_status=InformationStatus.EARLY_SIGNAL,
        notification_state=NotificationState.NEW,
        primary_article=a1, articles=[a1], article_count=1, independent_source_count=1,
        last_updated=NOW,
    )
    rel = OperationalRelevance(
        event_id=event.event_id, relevance_level=RelevanceLevel.HIGH, relevance_score=62,
        relevance_status=RelevanceStatus.ASSESSED,
    )
    # §六：P1 NEW 但 confidence 未達 immediate 門檻 → PROMPT，not IMMEDIATE
    # （見 delivery_orchestrator.py._classify_base_urgency 的 fallback 分支）。
    decision = _decision(event.event_id, DeliveryUrgency.PROMPT, TeamsMode.PROMPT,
                          "P1 NEW with LOW confidence (below immediate threshold)")
    return teams_renderer.render(event, rel, decision, dashboard_base_url=DASHBOARD_URL)


# ══════════════════════════════════════════════════════════════
# 4. Dual-Axis 最重要案例：P2 UNCHANGED + Exposure Escalated
# ══════════════════════════════════════════════════════════════
def preview_exposure_escalation():
    a1 = _article("ee1", "TradeWinds", "Kaohsiung terminal reports berth congestion",
                   "https://tradewindsnews.com/ee1", hours_ago=6)
    event = MaritimeEvent(
        event_id="evt_exposure_escalation", headline="Kaohsiung Terminal Reports Berth Congestion",
        event_type=EventType.OPERATIONS, incident_subtype="PORT_DISRUPTION",
        location="Kaohsiung", port="Kaohsiung",
        management_priority=ManagementPriority.P2, management_score=55,
        confidence_level=ConfidenceLevel.MEDIUM, information_status=InformationStatus.CORROBORATED,
        notification_state=NotificationState.UNCHANGED,   # ★ 事件本身沒變
        primary_article=a1, articles=[a1], article_count=1, independent_source_count=1,
        last_updated=NOW,
    )
    rel = OperationalRelevance(
        event_id=event.event_id, relevance_level=RelevanceLevel.HIGH, relevance_score=62,
        relevance_status=RelevanceStatus.ASSESSED,
        affected_vessels=[AffectedVessel(vessel_name="WAN HAI 510", service_code="AEX1",
                                          next_port="TWKHH", eta_display=None,
                                          exposure_type="PORT_CALL", hours_to_exposure=40.0)],
        closest_eta_hours=40.0,
    )
    decision = _decision(event.event_id, DeliveryUrgency.PROMPT, TeamsMode.PROMPT,
                          "WHL operational exposure escalated")
    return teams_renderer.render(event, rel, decision, dashboard_base_url=DASHBOARD_URL)


# ══════════════════════════════════════════════════════════════
# 5. Resolved + Exposure Cleared
# ══════════════════════════════════════════════════════════════
def preview_resolved():
    a1 = _article("rv1", "TradeWinds", "MSC vessel refloated near Singapore",
                   "https://tradewindsnews.com/rv1")
    event = MaritimeEvent(
        event_id="evt_resolved", headline="MSC Vessel Refloated Near Singapore",
        event_type=EventType.SAFETY, incident_subtype="GROUNDING",
        location="Singapore", port="Singapore",
        management_priority=ManagementPriority.P1, management_score=70,
        confidence_level=ConfidenceLevel.HIGH, information_status=InformationStatus.CONFIRMED,
        notification_state=NotificationState.RESOLVED_UPDATE,
        change_reason="Vessel successfully refloated and port operations have resumed.",
        primary_article=a1, articles=[a1], article_count=1, independent_source_count=1,
        last_updated=NOW,
    )
    rel = OperationalRelevance(
        event_id=event.event_id, relevance_level=RelevanceLevel.NONE, relevance_score=0,
        relevance_status=RelevanceStatus.ASSESSED,
    )
    decision = _decision(event.event_id, DeliveryUrgency.PROMPT, TeamsMode.RESOLVED,
                          "P1 RESOLVED_UPDATE; WHL operational exposure cleared (previously elevated)")
    return teams_renderer.render(event, rel, decision, dashboard_base_url=DASHBOARD_URL)


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    scenarios = [
        ("teams_preview_p1.txt", preview_p1),
        ("teams_preview_own_fleet.txt", preview_own_fleet),
        ("teams_preview_early_signal.txt", preview_early_signal),
        ("teams_preview_exposure_escalation.txt", preview_exposure_escalation),
        ("teams_preview_resolved.txt", preview_resolved),
    ]
    for filename, fn in scenarios:
        text = fn()
        path = OUTPUT_DIR / filename
        path.write_text(text, encoding="utf-8")
        print(f"── {filename} ──")
        print(text)
        print(f"({len(text)} chars)\n")
    print(f"✅ 已寫入 {len(scenarios)} 個 Teams Preview 檔案到 {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
