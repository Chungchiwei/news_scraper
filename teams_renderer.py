#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
teams_renderer.py
海事航運新聞監控系統 — Phase 7 §二十五〜三十一 Teams Alert Design

職責：
  把 MaritimeEvent + OperationalRelevance + DeliveryDecision 渲染成
  一則簡短、可在 10-15 秒內看懂的純文字 Teams 訊息。

  ★ Renderer 只渲染，不判斷 Risk（§十六）：urgency/channel 是否要送
  已經由 delivery_orchestrator.py 決定好，這裡只讀 DeliveryDecision
  的 teams_mode 選模板，不重新判斷。
  ★ Emoji 節制（§二十六）：最多只用 🔴/🟠/🟢 作 Risk Indicator，不堆疊
  裝飾性 emoji。
  ★ EARLY SIGNAL 絕不因為訊息短就被省略（§二十八）——所有模板在
  event.information_status 屬於 EARLY_SIGNAL/UNCONFIRMED 時，都會
  無條件附加一句提醒，不因為分支不同而遺漏。
  ★ 來源精簡（§二十九）：最多列 N 個來源名稱，超過就顯示「M 個獨立
  來源」，詳細來源留給 Dashboard。
  ★ 訊息長度上限（§三十）：超過上限截斷非關鍵區塊，保留開頭最重要
  的風險資訊。
  ★ Dashboard 連結（§三十一）：只有 dashboard_base_url 有設定才附上
  連結，絕不生成假 URL。
"""

from __future__ import annotations

from typing import Optional

_STATUS_LABEL = {
    "NEW": "NEW",
    "MATERIAL_UPDATE": "MATERIAL UPDATE",
    "MINOR_UPDATE": "MINOR UPDATE",
    "UNCHANGED": "UNCHANGED",
    "RESOLVED_UPDATE": "RESOLVED",
}


def _status_label(notification_state: Optional[str]) -> str:
    return _STATUS_LABEL.get(notification_state, notification_state or "UNKNOWN")


def _exposure_label(operational_relevance) -> str:
    if operational_relevance is None:
        return "Not assessed"
    if getattr(operational_relevance, "relevance_status", None) == "UNAVAILABLE":
        return "Unavailable"
    return operational_relevance.relevance_level or "NONE"


def _affected_line(operational_relevance) -> Optional[str]:
    if operational_relevance is None:
        return None
    vessels = getattr(operational_relevance, "affected_vessels", None) or []
    services = getattr(operational_relevance, "affected_services", None) or []
    if not vessels and not services:
        return None
    bits = []
    if vessels:
        unit = "vessel" if len(vessels) == 1 else "vessels"
        bits.append(f"{len(vessels)} {unit}")
    if services:
        unit = "service" if len(services) == 1 else "services"
        bits.append(f"{len(services)} {unit}")
    return " / ".join(bits)


def _sources_line(event, max_sources: int) -> Optional[str]:
    names: list = []
    for a in (getattr(event, "articles", None) or []):
        if a.source_name and a.source_name not in names:
            names.append(a.source_name)
    if not names:
        return None
    if len(names) <= max_sources:
        return " · ".join(names)
    n = getattr(event, "independent_source_count", None) or len(names)
    unit = "independent source" if n == 1 else "independent sources"
    return f"{n} {unit}"


def _is_early_signal(event) -> bool:
    return getattr(event, "information_status", None) in ("EARLY_SIGNAL", "UNCONFIRMED")


# ══════════════════════════════════════════════════════════════
# 個別模板
# ══════════════════════════════════════════════════════════════
def _render_general(event, operational_relevance, exposure_label: str) -> str:
    icon = "🔴" if event.management_priority == "P1" else "🟠"
    lines = [f"{icon} MARITIME ALERT | {event.management_priority}", "", event.headline, ""]
    lines += ["Status:", _status_label(event.notification_state), ""]
    lines += ["WHL Exposure:", exposure_label, ""]
    affected = _affected_line(operational_relevance)
    if affected:
        lines += ["Affected:", affected, ""]
    if event.notification_state == "MATERIAL_UPDATE" and getattr(event, "change_reason", None):
        lines += ["What Changed:", event.change_reason, ""]
    lines += ["Confidence:", event.confidence_level or "UNKNOWN"]
    return "\n".join(lines).rstrip()


def _render_own_fleet(event, operational_relevance, exposure_label: str) -> str:
    icon = "🔴" if event.management_priority == "P1" else "🟠"
    vessel = event.vessel_name or "WHL vessel"
    lines = [f"{icon} {event.management_priority} | OWN FLEET", "",
             f"{vessel} — Vessel Incident", ""]
    lines += ["Event:", event.incident_subtype or event.event_type or "Incident", ""]
    lines += ["Exposure:", exposure_label, ""]
    lines += ["Information:", f"{event.information_status or 'UNKNOWN'} / {event.confidence_level or 'UNKNOWN'}", ""]
    latest = getattr(event, "change_reason", None)
    if not latest and getattr(event, "primary_article", None):
        latest = event.primary_article.summary
    if latest:
        lines += ["Latest:", latest, ""]
    lines += ["Open Dashboard for details."]
    return "\n".join(lines).rstrip()


def _render_exposure_escalation(event, operational_relevance, exposure_label: str, decision) -> str:
    lines = [f"🟠 {event.management_priority} | OPERATIONAL EXPOSURE ESCALATED", "",
             event.headline, ""]
    lines += ["WHL Exposure:", exposure_label, ""]
    lines += ["Reason:", decision.delivery_reason or "WHL operational exposure escalated", ""]
    lines += ["Confidence:", event.confidence_level or "UNKNOWN"]
    return "\n".join(lines).rstrip()


def _render_resolved(event, operational_relevance, exposure_label: str) -> str:
    lines = [f"🟢 RESOLVED | {event.management_priority}", "", event.headline, ""]
    lines += ["Status:", "RESOLVED", ""]
    if operational_relevance is not None:
        lines += ["WHL Exposure:", exposure_label, ""]
    note = getattr(event, "change_reason", None) or "The event has been resolved."
    lines.append(note)
    return "\n".join(lines).rstrip()


# ══════════════════════════════════════════════════════════════
# 對外入口
# ══════════════════════════════════════════════════════════════
def render(event, operational_relevance, decision, dashboard_base_url: Optional[str] = None,
           max_chars: int = 2200, max_sources: int = 2) -> str:
    """
    依 decision.teams_mode（見 delivery_models.TeamsMode）與是否 Own Fleet
    選擇模板；EARLY SIGNAL 提醒與來源行則無條件附加在所有模板之後
    （§二十八：不能因為分支不同而遺漏）。
    """
    own_fleet = bool(operational_relevance and getattr(operational_relevance, "own_fleet_involved", False))
    exposure_label = _exposure_label(operational_relevance)

    if decision.teams_mode == "RESOLVED":
        body = _render_resolved(event, operational_relevance, exposure_label)
    elif own_fleet:
        body = _render_own_fleet(event, operational_relevance, exposure_label)
    elif "exposure escalated" in (decision.delivery_reason or "").lower():
        body = _render_exposure_escalation(event, operational_relevance, exposure_label, decision)
    else:
        body = _render_general(event, operational_relevance, exposure_label)

    if _is_early_signal(event) and decision.teams_mode != "RESOLVED":
        body += ("\n\nEARLY SIGNAL — UNCONFIRMED\n"
                 "Current information is based on limited sources and requires confirmation.")

    sources_line = _sources_line(event, max_sources)
    if sources_line:
        body += f"\n\nSources:\n{sources_line}"

    # §三十一：只有明確設定 dashboard_base_url 才附連結，絕不生成假 URL。
    if dashboard_base_url:
        body += f"\n\n[Open Dashboard]({dashboard_base_url.rstrip('/')}/events/{event.event_id})"

    if len(body) > max_chars:
        truncate_at = max(0, max_chars - 40)
        body = body[:truncate_at].rstrip() + "\n… (truncated — see Dashboard for full detail)"

    return body


def render_consolidated(events_with_context: list, max_events: int = 5,
                         dashboard_base_url: Optional[str] = None) -> str:
    """
    §八十四〜八十五：同一次 run 若有多個 P1 事件（Own Fleet P1 除外，
    由呼叫端先過濾出去），合併成一則 Consolidated Alert，而不是連續
    洗版好幾則訊息。events_with_context: list[(event, operational_relevance)]。
    """
    n = len(events_with_context)
    header = f"🔴 {n} Immediate Attention Events" if n > 1 else "🔴 Immediate Attention Event"
    lines = [header, ""]
    for event, rel in events_with_context[:max_events]:
        exposure = _exposure_label(rel)
        lines.append(f"• [{event.management_priority}] {event.headline} — WHL Exposure: {exposure}")
    if n > max_events:
        lines.append(f"… and {n - max_events} more — see Dashboard.")
    if dashboard_base_url:
        lines.append("")
        lines.append(f"[Open Dashboard]({dashboard_base_url.rstrip('/')}/events)")
    return "\n".join(lines)
