#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
email_view_model.py
海事航運新聞監控系統 — Phase 4 §四十六〜四十七 View Model Layer

職責：
  把 Intelligence Layer（MaritimeEvent、BriefingSelector 分桶結果、
  ManagementSummaryBuilder 產生的中文文字）攤平成 Renderer 唯讀、
  不再做任何風險判斷／文字生成的展示用資料（EmailEventViewModel /
  ExecutiveBriefViewModel）。

  Renderer 拿到這裡輸出的 view model 之後，只負責把已經算好的欄位
  排進 HTML table，不得重新計算 priority / confidence / fleet
  relevance / overall risk（§四十六：「Renderer 永遠不自己算風險」）。

  ★ Overall Risk 完全是 Priority bucket 是否有東西的函式（§三十四）：
      P1 存在 → HIGH
      無 P1，P2 存在 → ELEVATED
      無 P1/P2，P3 存在 → WATCH
      皆無 → NORMAL
    不建立任何新的 AI/ML 評分模型，且與 Confidence（可信度）完全脫鉤。

  ★ None 值欄位一律不猜測填字串；Renderer 端看到 None／空字串／空
    list 時，該行整個省略，不得渲染成 "Vessel: None"。

Phase 5 擴充：
  build_event_view_model() / build_daily_brief_view_model() /
  build_alert_view_model() 現在都能接受一個可選的
  IntelligenceAnalysis（見 analysis_validator.py），已驗證通過的
  AI Enhancement 文字會逐欄位取代 Rule-Based 文字（AI 該欄位是空字串
  時仍 fallback 回 Rule-Based，不是整包二選一）。沒有提供、或呼叫端
  沒有跑 LLM，這裡的行為與 Phase 4 完全相同（Rule-Based Summary 永遠
  是保底路徑，見 §四十六）。View Model 本身仍然不做任何風險判斷 ——
  AI 分析是否可信、要不要用，已經在 intelligence_analyzer.py /
  analysis_validator.py 決定過了，這裡只負責攤平成展示用欄位。

Phase 6 擴充：
  build_event_view_model() / build_daily_brief_view_model() /
  build_alert_view_model() 現在還能再接受一個可選的
  operational_relevance（單一 event 用）/ operational_relevance_map
  （event_id → OperationalRelevance，brief 層級用），見
  operational_relevance.py。

  ★ EVENT RISK ≠ COMPANY EXPOSURE（Phase 6 §三）：這裡只是把已經算好
    的 OperationalRelevance 攤平成展示欄位，不重新計算、不影響
    priority/confidence/overall_risk 任何既有欄位。呼叫端沒有提供
    operational_relevance（Phase 6 尚未接上，或 Provider 這次沒跑）時，
    has_operational_assessment=False，Renderer 完全不畫 Fleet Exposure
    區塊——對純 Phase 1-5 呼叫端行為零影響。
  ★ NO MATCH ≠ NO RISK、DATA UNAVAILABLE ≠ NONE（§六十四〜六十五）：
    relevance_status=UNAVAILABLE 時一律顯示「Unavailable」措辭，绝不
    顯示成 NONE／無曝險。DATA_STALE 時附上資料時效提示句，不隱藏。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from models import (
    ManagementPriority, NotificationState, ConfidenceLevel, InformationStatus,
)
from management_summary import ManagementSummaryBuilder
from email_config import load_email_rules
from analysis_validator import IntelligenceAnalysis

_TZ = timezone(timedelta(hours=8))
_TZ_NAME = "TPE"


def _fmt_tpe(dt: Optional[datetime]) -> Optional[str]:
    """UTC → TPE 顯示字串，例如 '11 Aug 2026 08:00 TPE'（§六十七）。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ).strftime(f"%d %b %Y %H:%M {_TZ_NAME}")


_PRIORITY_LABEL = {
    ManagementPriority.P1: "P1 — IMMEDIATE ATTENTION",
    ManagementPriority.P2: "P2 — MANAGEMENT WATCH",
    ManagementPriority.P3: "P3 — INDUSTRY WATCH",
    ManagementPriority.P4: "P4 — REFERENCE",
}
_PRIORITY_COLOR = {
    ManagementPriority.P1: "#dc2626",   # Red
    ManagementPriority.P2: "#c2410c",   # Orange
    ManagementPriority.P3: "#b45309",   # Amber
    ManagementPriority.P4: "#64748b",   # Gray
}
_NOTIF_BADGE = {
    NotificationState.NEW:             "NEW",
    NotificationState.MATERIAL_UPDATE: "UPDATE",
    NotificationState.RESOLVED_UPDATE: "RESOLVED",
    # UNCHANGED / MINOR_UPDATE 不對應任何 badge — 本來就不該進到這裡
    # （§二十一：UNCHANGED must never be shown to management）。
}
_INFO_STATUS_LABEL = {
    InformationStatus.CONFIRMED:    "CONFIRMED",
    InformationStatus.CORROBORATED: "CORROBORATED",
    InformationStatus.UNCONFIRMED:  "UNCONFIRMED",
    InformationStatus.EARLY_SIGNAL: "EARLY SIGNAL",
}


# ══════════════════════════════════════════════════════════════
# View Models（純資料，Renderer 唯讀）
# ══════════════════════════════════════════════════════════════
@dataclass
class EmailEventViewModel:
    event_id: str

    priority: str                       # P1-P4 原始代碼（排序/樣式判斷用）
    priority_label: str
    priority_color: str
    notification_badge: Optional[str]   # NEW / UPDATE / RESOLVED / None
    is_own_fleet: bool

    headline_zh: str
    management_summary_zh: str
    why_it_matters_zh: str
    what_changed_zh: list = field(default_factory=list)

    event_type: Optional[str] = None
    impact_tags: list = field(default_factory=list)

    location: Optional[str] = None
    vessel_name: Optional[str] = None
    carrier_display: Optional[str] = None

    confidence_level: Optional[str] = None            # HIGH/MEDIUM/LOW
    information_status_label: Optional[str] = None    # CONFIRMED/.../EARLY SIGNAL
    is_early_signal: bool = False

    source_count: int = 0
    independent_source_count: int = 0
    source_names: list = field(default_factory=list)
    primary_url: Optional[str] = None
    primary_source_name: Optional[str] = None

    fleet_relevance_label: str = "LOW"

    last_updated_display: Optional[str] = None
    published_display: Optional[str] = None

    original_headline: Optional[str] = None

    # ── Phase 5：AI Enhancement（有驗證通過的分析才會填入）───────────
    has_ai_enhancement: bool = False
    timeline: list = field(default_factory=list)          # [{"time_display":..,"summary_zh":..}]
    contradiction_notes: list = field(default_factory=list)  # list[str]，已組成的中文提示句
    ai_analysis_confidence: Optional[str] = None            # HIGH/MEDIUM/LOW，不對外顯示，僅供除錯

    # ── Phase 6：Operational Relevance（獨立於 Priority/Confidence 的
    # 第二軸，見本檔案 docstring）。operational_relevance 沒有提供時，
    # has_operational_assessment 維持 False，其餘欄位維持預設值，
    # Renderer 看到 False 就整段不畫（跟 Phase 5 的 has_ai_enhancement
    # 是同一種「有才畫」慣例）──
    has_operational_assessment: bool = False
    relevance_level: Optional[str] = None            # DIRECT/HIGH/MODERATE/LOW/NONE
    relevance_status: Optional[str] = None           # ASSESSED/DATA_STALE/UNAVAILABLE
    operational_own_fleet_match: bool = False         # Phase 6 精確船舶比對結果（獨立於 Phase 2 的 is_own_fleet）
    exposure_vessel_names: list = field(default_factory=list)     # 未格式化船名，供 brief 層級彙總用
    exposure_vessels_display: list = field(default_factory=list)  # 已格式化好的卡片顯示字串
    exposure_service_codes: list = field(default_factory=list)
    exposure_closest_eta_display: Optional[str] = None
    exposure_no_direct_text: Optional[str] = None     # NONE 狀態固定措辭（"未發現直接曝險"）
    exposure_unavailable_text: Optional[str] = None   # UNAVAILABLE 狀態固定措辭
    exposure_is_stale: bool = False
    exposure_stale_note: Optional[str] = None         # 已格式化好的資料時效提示句（中英雙語）


@dataclass
class ExecutiveBriefViewModel:
    company_name: str
    brief_title: str
    brief_subtitle: str
    generated_at_display: str

    overall_risk: str          # HIGH / ELEVATED / WATCH / NORMAL
    overall_risk_color: str

    p1_count: int
    p2_count: int
    monitored_count: int       # P3 industry watch 顯示數

    immediate: list = field(default_factory=list)   # list[EmailEventViewModel]
    watch: list = field(default_factory=list)
    industry: list = field(default_factory=list)
    resolved: list = field(default_factory=list)

    overflow: dict = field(default_factory=dict)

    executive_summary_zh: str = ""
    subject: str = ""
    is_no_risk: bool = False

    # ── Phase 5：本封信是否包含任何 AI Enhancement（決定 footer 是否顯示揭露聲明）──
    has_ai_enhancement: bool = False

    # ── Phase 6：WHL FLEET EXPOSURE 彙總（只在有跑 Operational Relevance
    # Engine 時才非零，見本檔案 docstring）。彙總計算的是「這封信裡實際
    # 顯示出來的事件」，不會改變 Overall Risk / P1-P4 分桶邏輯本身──
    has_operational_assessment: bool = False
    exposure_direct_count: int = 0
    exposure_high_count: int = 0
    exposure_affected_vessel_count: int = 0
    exposure_unavailable: bool = False    # 本次 run 是否有事件因 Provider 失敗顯示 Unavailable
    exposure_stale: bool = False          # 本次 run 是否有事件的曝險評估基於過期船期資料


# ══════════════════════════════════════════════════════════════
# 內部小工具
# ══════════════════════════════════════════════════════════════
def _trimmed_impact_tags(event, rules: dict) -> list:
    cfg = rules.get("impact_tags_display", {})
    order = cfg.get("order", [])
    max_tags = cfg.get("max_tags", 4)
    tags = list(event.impact_tags or [])
    if order:
        tags.sort(key=lambda t: order.index(t) if t in order else len(order))
    return tags[:max_tags]


def _source_summary(event, max_names: int = 3) -> list:
    names: list = []
    for a in event.articles or []:
        if a.source_name and a.source_name not in names:
            names.append(a.source_name)
    return names[:max_names]


def _fleet_relevance_label(event, rules: dict, is_own_fleet: bool) -> str:
    bands_cfg = rules.get("fleet_relevance_bands", {})
    if is_own_fleet:
        return bands_cfg.get("own_fleet_label", "OWN FLEET")
    score = event.fleet_relevance_score or 0
    for band in bands_cfg.get("bands", []):
        if score >= band.get("min", 0):
            return band.get("label", "LOW")
    return "LOW"


def _compute_overall_risk(p1: int, p2: int, p3: int) -> tuple[str, str]:
    """§三十四：Overall Risk 純粹是 bucket 是否有東西的函式，不引入新評分模型。"""
    if p1 > 0:
        return "HIGH", "#dc2626"
    if p2 > 0:
        return "ELEVATED", "#c2410c"
    if p3 > 0:
        return "WATCH", "#b45309"
    return "NORMAL", "#15803d"


def _build_executive_summary(p1: int, p2: int, p3: int, resolved_count: int) -> str:
    """§三十六：規則式 1-2 句繁中摘要，含明確的『無事可報』wording。"""
    if p1 > 0 and p2 > 0:
        return (f"本次監控期間共發現 {p1} 起需要主管立即關注之重大事件，"
                f"另有 {p2} 起事件列入管理觀察，建議優先檢視下列 P1 事件。")
    if p1 > 0:
        return f"本次監控期間共發現 {p1} 起需要主管立即關注之重大事件，建議優先檢視。"
    if p2 > 0:
        return f"本次監控期間無重大緊急事件，惟有 {p2} 起事件列入管理觀察，建議留意後續發展。"
    if p3 > 0:
        return "本次監控期間未發現需要管理層立即關注之事件，僅有一般產業動態供參考。"
    if resolved_count > 0:
        return "本次監控期間未發現新增重大事件，先前列管事件已有解除或改善之更新。"
    return "本次監控期間未發現任何值得主管關注之重大海事風險事件，船隊營運無異常通報。"


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        t = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(t)
    except ValueError:
        return None


def _build_timeline_display(ai_timeline: list) -> list:
    """AI timeline（ISO time + summary_zh + source_ids）→ 展示用 TPE 時間字串。"""
    items = []
    for entry in ai_timeline or []:
        if not isinstance(entry, dict):
            continue
        summary_zh = entry.get("summary_zh")
        if not summary_zh:
            continue
        dt = _parse_iso(entry.get("time"))
        items.append({
            "time_display": _fmt_tpe(dt) if dt else None,
            "summary_zh": summary_zh,
        })
    return items


def _build_contradiction_notes(ai_contradictions: list) -> list:
    """§二十五：矛盾一律用中性 INFORMATION NOTE 措辭，不由系統自行選邊。"""
    notes = []
    for c in ai_contradictions or []:
        if not isinstance(c, dict):
            continue
        topic = c.get("topic") or "部分細節"
        notes.append(f"目前不同來源對「{topic}」說法不一，尚待進一步確認。")
    return notes


_EXPOSURE_NO_DIRECT_TEXT = "未發現直接曝險（No direct exposure identified）"
_EXPOSURE_UNAVAILABLE_TEXT = (
    "WHL Operational Exposure: Unavailable — 本次船期／船隊資料無法取得，"
    "曝險狀態未知，請改以其他管道確認（not the same as \"no exposure\"）。"
)
_EXPOSURE_STALE_TEMPLATE = (
    "船期曝險評估係依最後更新之船期資料（{hours}小時前）， 請留意資料時效 / "
    "Fleet relevance assessment based on schedule data last updated {hours}h ago."
)


def _fmt_eta_hours(hours: Optional[float]) -> Optional[str]:
    if hours is None:
        return None
    return f"{round(hours)}h"


def _build_exposure_fields(operational_relevance) -> dict:
    """
    §Phase 6 五十五〜六十四：把 OperationalRelevance（如果呼叫端有提供）
    攤平成 Email Card 用的展示欄位。

    ★ 呼叫端沒有跑 Phase 6（operational_relevance is None）時，回傳只有
      has_operational_assessment=False 的 dict——EmailEventViewModel
      其餘 exposure_* 欄位維持 dataclass 預設值，Renderer 完全不畫這個
      區塊，對 Phase 1-5-only 呼叫端行為零影響。
    ★ UNAVAILABLE 與 NONE 是兩種不同狀態，措辭分開處理，不可混用
      （§六十四〜六十五：DATA UNAVAILABLE ≠ NONE）。
    """
    if operational_relevance is None:
        return {"has_operational_assessment": False}

    status = operational_relevance.relevance_status
    level = operational_relevance.relevance_level

    vessel_names: list = []
    vessels_display: list = []
    for v in operational_relevance.affected_vessels or []:
        vessel_names.append(v.vessel_name)
        eta = _fmt_eta_hours(v.hours_to_exposure)
        bits = [v.vessel_name]
        if v.next_port:
            bits.append(f"→ {v.next_port}")
        if eta:
            bits.append(f"(ETA {eta})")
        vessels_display.append(" ".join(bits))

    fields: dict = {
        "has_operational_assessment": True,
        "relevance_level": level,
        "relevance_status": status,
        "operational_own_fleet_match": bool(operational_relevance.own_fleet_involved),
        "exposure_vessel_names": vessel_names,
        "exposure_vessels_display": vessels_display,
        "exposure_service_codes": list(operational_relevance.affected_services or []),
        "exposure_closest_eta_display": _fmt_eta_hours(operational_relevance.closest_eta_hours),
        "exposure_is_stale": bool(operational_relevance.is_stale),
    }

    if status == "UNAVAILABLE":
        fields["exposure_unavailable_text"] = _EXPOSURE_UNAVAILABLE_TEXT
    elif level in (None, "NONE"):
        fields["exposure_no_direct_text"] = _EXPOSURE_NO_DIRECT_TEXT

    if operational_relevance.is_stale:
        stale_hours = None
        if operational_relevance.data_timestamp is not None:
            now_ref = operational_relevance.assessed_at or datetime.now(timezone.utc)
            data_ts = operational_relevance.data_timestamp
            if data_ts.tzinfo is None:
                data_ts = data_ts.replace(tzinfo=timezone.utc)
            stale_hours = round((now_ref - data_ts).total_seconds() / 3600.0)
        fields["exposure_stale_note"] = _EXPOSURE_STALE_TEMPLATE.format(
            hours=stale_hours if stale_hours is not None else "?"
        )

    return fields


def _append_exposure_summary(base_summary: str, direct: int, high: int, vessel_count: int) -> str:
    """
    §Phase 6 五十五〜五十八：Executive Summary 補充船隊曝險資訊——只是
    在既有規則式摘要句子後面『補一句』，不改變 Overall Risk / P1-P4
    分桶判斷邏輯本身（那些仍然完全由 Phase 1-5 決定）。
    """
    if direct == 0 and high == 0:
        return base_summary
    bits = []
    if direct > 0:
        bits.append(f"其中 {direct} 起事件直接涉及本公司船舶")
    if high > 0:
        bits.append(f"{high} 起屬於高度船隊曝險")
    if vessel_count > 0:
        bits.append(f"共 {vessel_count} 艘船舶可能受影響")
    return base_summary + "　" + "，".join(bits) + "，建議優先確認船期與航線曝險。"


def _should_show_timeline(event, ai_timeline: list, rules: dict) -> bool:
    if not ai_timeline:
        return False
    cfg = rules.get("email_display", {})
    show_for = cfg.get("show_timeline_for", ["P1"])
    if event.management_priority in show_for:
        return True
    min_updates = cfg.get("show_timeline_min_material_updates", 2)
    # version 只在 Material Update 時遞增（Phase 3 §三十一），version >=
    # 1 + min_updates 大致等同「已經過至少 min_updates 次 Material Update」。
    return (event.version or 1) >= (1 + min_updates)


# ══════════════════════════════════════════════════════════════
# Event View Model 建構
# ══════════════════════════════════════════════════════════════
def build_event_view_model(event, summary_builder: ManagementSummaryBuilder,
                            rules: dict,
                            ai_analysis: Optional[IntelligenceAnalysis] = None,
                            operational_relevance=None) -> EmailEventViewModel:
    is_own = summary_builder.is_own_fleet(event)
    priority = event.management_priority or ManagementPriority.P4
    badge = _NOTIF_BADGE.get(event.notification_state)

    rule_summary = summary_builder.management_summary(event)
    rule_why = summary_builder.why_it_matters(event)
    rule_changed = (
        summary_builder.what_changed(event)
        if event.notification_state in (NotificationState.MATERIAL_UPDATE,
                                         NotificationState.RESOLVED_UPDATE)
        else []
    )

    # ── Phase 5：AI Enhancement 逐欄位覆蓋，AI 該欄位空白時 fallback
    #    回 Rule-Based（不是整包二選一），Rule-Based 永遠是保底路徑 ──
    has_ai = ai_analysis is not None
    management_summary_zh = (ai_analysis.management_summary_zh if has_ai and ai_analysis.management_summary_zh
                              else rule_summary)
    why_it_matters_zh = (ai_analysis.why_it_matters_zh if has_ai and ai_analysis.why_it_matters_zh
                          else rule_why)
    what_changed_zh = (
        [ai_analysis.what_changed_zh] if has_ai and ai_analysis.what_changed_zh
        else rule_changed
    )
    timeline = (_build_timeline_display(ai_analysis.timeline)
                if has_ai and _should_show_timeline(event, ai_analysis.timeline, rules)
                else [])
    contradiction_notes = _build_contradiction_notes(ai_analysis.contradictions) if has_ai else []
    ai_confidence = ai_analysis.analysis_confidence if has_ai else None

    info_status = event.information_status
    is_early = info_status in (InformationStatus.EARLY_SIGNAL, InformationStatus.UNCONFIRMED)

    primary = event.primary_article
    exposure_fields = _build_exposure_fields(operational_relevance)

    return EmailEventViewModel(
        event_id=event.event_id,
        priority=priority,
        priority_label=_PRIORITY_LABEL.get(priority, priority),
        priority_color=_PRIORITY_COLOR.get(priority, "#64748b"),
        notification_badge=badge,
        is_own_fleet=is_own,
        headline_zh=summary_builder.management_headline(event),
        management_summary_zh=management_summary_zh,
        why_it_matters_zh=why_it_matters_zh,
        what_changed_zh=what_changed_zh,
        event_type=event.event_type,
        impact_tags=_trimmed_impact_tags(event, rules),
        location=summary_builder.location_zh(event),
        vessel_name=event.vessel_name,
        carrier_display=summary_builder.carrier_zh(event),
        confidence_level=event.confidence_level,
        information_status_label=_INFO_STATUS_LABEL.get(info_status),
        is_early_signal=is_early,
        source_count=event.article_count or len(event.articles or []),
        independent_source_count=event.independent_source_count or 0,
        source_names=_source_summary(event),
        primary_url=primary.url if primary else None,
        primary_source_name=primary.source_name if primary else None,
        fleet_relevance_label=_fleet_relevance_label(event, rules, is_own),
        last_updated_display=_fmt_tpe(event.last_updated),
        published_display=_fmt_tpe(primary.published_at) if primary else None,
        original_headline=primary.title if primary else None,
        has_ai_enhancement=has_ai,
        timeline=timeline,
        contradiction_notes=contradiction_notes,
        ai_analysis_confidence=ai_confidence,
        **exposure_fields,
    )


# ══════════════════════════════════════════════════════════════
# Subject Line（§十二）
# ══════════════════════════════════════════════════════════════
def build_alert_subject(immediate_vm: list, rules: dict) -> str:
    cfg = rules.get("subject", {})
    prefix_alert = cfg.get("prefix_alert", "Maritime Alert")
    prefix_update = cfg.get("prefix_update", "Maritime Update")

    if not immediate_vm:
        return f"[{prefix_alert}] No Immediate Events"

    if len(immediate_vm) == 1:
        vm = immediate_vm[0]
        if vm.notification_badge == "RESOLVED":
            return f"[{prefix_update}] P1 Resolved | {vm.headline_zh}"
        if vm.notification_badge == "UPDATE":
            return f"[🟠 {prefix_update}] P1 Update | {vm.headline_zh}"
        return f"[🔴 {prefix_alert}] P1 Event | {vm.headline_zh}"

    return f"[🔴 {prefix_alert}] {len(immediate_vm)} Immediate Attention Events"


def build_daily_brief_subject(p1_count: int, p2_count: int, is_no_risk: bool,
                               generated_at: datetime, rules: dict) -> str:
    cfg = rules.get("subject", {})
    prefix_brief = cfg.get("prefix_brief", "Maritime Intelligence")
    date_str = generated_at.astimezone(_TZ).strftime("%m/%d")

    if is_no_risk:
        return f"[{prefix_brief}] No Major Fleet Risk | {date_str}"
    if p1_count > 0:
        # ★ 改成讀 prefix_brief（email_rules.json → subject.prefix_brief），
        #   不要再把品牌前綴寫死在這裡——寫死過一次就是這次要修的問題：
        #   哪天品牌前綴又要改，這裡會忘記跟著改，變回兩個不同標題。
        return f"[{prefix_brief}] Daily Brief | {date_str} | P1:{p1_count} P2:{p2_count}"
    return f"[{prefix_brief}] Daily Brief | {date_str} | P1:{p1_count} P2:{p2_count}"


# ══════════════════════════════════════════════════════════════
# Executive Brief View Model 建構（Daily Brief / Alert 共用組裝邏輯）
# ══════════════════════════════════════════════════════════════
def _build_brief(selection: dict, summary_builder: Optional[ManagementSummaryBuilder],
                  email_rules: Optional[dict], generated_at: Optional[datetime],
                  alert_mode: bool,
                  ai_analyses: Optional[dict] = None,
                  operational_relevance_map: Optional[dict] = None) -> ExecutiveBriefViewModel:
    rules = email_rules or load_email_rules()
    sb = summary_builder or ManagementSummaryBuilder()
    generated_at = generated_at or datetime.now(timezone.utc)
    company_cfg = rules.get("company", {})
    ai_analyses = ai_analyses or {}
    operational_relevance_map = operational_relevance_map or {}

    def _vm(e):
        return build_event_view_model(
            e, sb, rules, ai_analysis=ai_analyses.get(e.event_id),
            operational_relevance=operational_relevance_map.get(e.event_id),
        )

    immediate_vm = [_vm(e) for e in selection.get("immediate", [])]

    if alert_mode:
        # §七十五〜七十六：Alert Mode 只聚焦 P1，不夾帶 P2/P3，避免 alert fatigue。
        watch_vm: list = []
        industry_vm: list = []
        resolved_vm: list = []
        p2_count = 0
        p3_count = 0
        overall_risk, risk_color = _compute_overall_risk(len(immediate_vm), 0, 0)
        subject = build_alert_subject(immediate_vm, rules)
        is_no_risk = False
    else:
        watch_vm = [_vm(e) for e in selection.get("watch", [])]
        industry_vm = [_vm(e) for e in selection.get("industry", [])]
        resolved_vm = [_vm(e) for e in selection.get("resolved", [])]
        p2_count = len(watch_vm)
        p3_count = len(industry_vm)
        overall_risk, risk_color = _compute_overall_risk(len(immediate_vm), p2_count, p3_count)
        is_no_risk = (len(immediate_vm) == 0 and p2_count == 0 and p3_count == 0
                      and not resolved_vm)
        subject = build_daily_brief_subject(len(immediate_vm), p2_count, is_no_risk,
                                             generated_at, rules)

    all_vms = immediate_vm + watch_vm + industry_vm + resolved_vm
    has_ai = any(vm.has_ai_enhancement for vm in all_vms)

    # ── Phase 6：WHL FLEET EXPOSURE 彙總（只彙總這封信實際顯示出來的
    # 事件；has_operational_assessment 全為 False 時彙總欄位維持 0/False，
    # Renderer 完全不畫這個區塊）──
    has_operational_assessment = any(vm.has_operational_assessment for vm in all_vms)
    exposure_direct_count = sum(1 for vm in all_vms if vm.relevance_level == "DIRECT")
    exposure_high_count = sum(1 for vm in all_vms if vm.relevance_level == "HIGH")
    exposure_vessel_names_all: set = set()
    for vm in all_vms:
        exposure_vessel_names_all.update(vm.exposure_vessel_names)
    exposure_affected_vessel_count = len(exposure_vessel_names_all)
    exposure_unavailable = any(vm.relevance_status == "UNAVAILABLE" for vm in all_vms)
    exposure_stale = any(vm.exposure_is_stale for vm in all_vms)

    exec_summary = _build_executive_summary(len(immediate_vm), p2_count, p3_count, len(resolved_vm))
    exec_summary = _append_exposure_summary(
        exec_summary, exposure_direct_count, exposure_high_count, exposure_affected_vessel_count
    )

    return ExecutiveBriefViewModel(
        company_name=company_cfg.get("name", "WAN HAI LINES"),
        brief_title=company_cfg.get("brief_title", "MARITIME INTELLIGENCE BRIEF"),
        brief_subtitle=company_cfg.get("brief_subtitle", "Fleet Safety · Security · Operations"),
        generated_at_display=_fmt_tpe(generated_at),
        overall_risk=overall_risk,
        overall_risk_color=risk_color,
        p1_count=len(immediate_vm),
        p2_count=p2_count,
        monitored_count=p3_count,
        immediate=immediate_vm,
        watch=watch_vm,
        industry=industry_vm,
        resolved=resolved_vm,
        has_ai_enhancement=has_ai,
        overflow=selection.get("overflow", {}),
        executive_summary_zh=exec_summary,
        subject=subject,
        is_no_risk=is_no_risk,
        has_operational_assessment=has_operational_assessment,
        exposure_direct_count=exposure_direct_count,
        exposure_high_count=exposure_high_count,
        exposure_affected_vessel_count=exposure_affected_vessel_count,
        exposure_unavailable=exposure_unavailable,
        exposure_stale=exposure_stale,
    )


def build_daily_brief_view_model(selection: dict,
                                  summary_builder: Optional[ManagementSummaryBuilder] = None,
                                  email_rules: Optional[dict] = None,
                                  generated_at: Optional[datetime] = None,
                                  ai_analyses: Optional[dict] = None,
                                  operational_relevance_map: Optional[dict] = None) -> ExecutiveBriefViewModel:
    return _build_brief(selection, summary_builder, email_rules, generated_at,
                         alert_mode=False, ai_analyses=ai_analyses,
                         operational_relevance_map=operational_relevance_map)


def build_alert_view_model(selection: dict,
                            summary_builder: Optional[ManagementSummaryBuilder] = None,
                            email_rules: Optional[dict] = None,
                            generated_at: Optional[datetime] = None,
                            ai_analyses: Optional[dict] = None,
                            operational_relevance_map: Optional[dict] = None) -> ExecutiveBriefViewModel:
    return _build_brief(selection, summary_builder, email_rules, generated_at,
                         alert_mode=True, ai_analyses=ai_analyses,
                         operational_relevance_map=operational_relevance_map)
