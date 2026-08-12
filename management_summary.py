#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
management_summary.py
海事航運新聞監控系統 — Phase 4 §二十二〜二十八、五十七〜五十八
Rule-Based Management Wording（無 LLM）

職責：
  1. management_headline() — 簡短繁中主管標題（不是原文 headline 直接搬）
  2. management_summary()  — 1-2 句、~120 字內的管理摘要
  3. why_it_matters()      — 依 event_type / own fleet 產生「為什麼重要」
  4. what_changed()        — 把 Phase 3 的 change_reason（我們自己產生、
                              格式受控的英文片語）轉成繁中條列

★ Facts ≠ Assessment（§二十二、五十）：
  - 已確認資料（information_status 非 EARLY_SIGNAL/UNCONFIRMED）→ 直接陳述。
  - 尚未交叉驗證 → 一律使用「據報」「疑似」「初步資訊顯示」等保留詞，
    不得把 EARLY SIGNAL 寫成已確認事實。
  - 系統推導的影響（而非來源明確證實的事實）一律使用「可能」「需關注」
    「建議持續監控」，不宣稱公司已採取任何行動。

★ 本模組完全不呼叫任何 LLM / 外部 API。所有文字都是「結構化欄位 +
  固定 template + 我們自己产生的 change_reason 格式」的規則比對結果，
  絕不對來源原始英文摘要做自由翻譯或摘要（避免產生「看起來像 AI
  生成但其實是幻覺」的文字）。
"""

from __future__ import annotations

import re
from typing import Optional

from risk_config import load_risk_rules
from models import InformationStatus, EventType

# ══════════════════════════════════════════════════════════════
# 顯示用中文對照表
# ══════════════════════════════════════════════════════════════
_SUBTYPE_ZH = {
    "VESSEL_ATTACK":       "商船遭攻擊事件",
    "EXPLOSION":           "爆炸事件",
    "FIRE":                "火災事件",
    "SINKING":             "船舶沉沒事件",
    "COLLISION":           "船舶碰撞事件",
    "ALLISION":            "船舶衝撞固定物事件",
    "GROUNDING":           "船舶擱淺事件",
    "LOSS_OF_PROPULSION":  "船舶失去推進動力事件",
    "HIJACKING":           "船舶劫持事件",
    "STOWAWAY_DRUGS":      "偷渡／毒品走私事件",
    "CREW_CASUALTY":       "船員傷亡事件",
    "PORT_DISRUPTION":     "港口／航道中斷事件",
    "POLLUTION":           "海洋污染事件",
}

_EVENT_TYPE_ZH = {
    EventType.SAFETY:      "船舶安全事件",
    EventType.SECURITY:    "海事保全事件",
    EventType.CREW:        "船員事件",
    EventType.OPERATIONS:  "港口／營運事件",
    EventType.REGULATORY:  "法規動態",
    EventType.ENVIRONMENT: "環境污染事件",
    EventType.MARKET:      "市場動態",
    EventType.COMPETITOR:  "航商動態",
    EventType.OTHER:       "海事動態",
}

_VESSEL_STATUS_ZH = {
    "UNDERWAY":  "持續航行", "DISABLED": "失去動力", "GROUNDED": "擱淺",
    "REFLOATED": "已重新浮起", "ABANDONED": "船員已棄船",
    "UNDER_TOW": "拖帶中", "SANK": "已沉沒",
}
_FIRE_STATUS_ZH = {"ONGOING": "火勢仍在延燒", "EXTINGUISHED": "火勢已撲滅"}
_CASUALTY_STATUS_ZH = {
    "NONE_REPORTED": "目前無人員傷亡通報", "INJURED": "已有船員受傷",
    "FATALITY": "已有船員罹難", "MISSING": "有船員下落不明",
}
_PORT_STATUS_ZH = {"CONGESTED": "壅塞", "CLOSED": "關閉", "REOPENED": "重新開放"}
_NAV_STATUS_ZH = {"RESTRICTED": "航行受限", "CLOSED": "關閉", "REOPENED": "重新開放"}
_CREW_LABEL_ZH = {"injured": "船員受傷", "fatalities": "船員罹難", "missing": "船員失蹤"}


def _zh_part(display: Optional[str]) -> Optional[str]:
    """"紅海 / Red Sea" → "紅海"；沒有 "/" 的（如 "MSC"）原樣回傳。"""
    if not display:
        return None
    return display.split(" / ")[0].strip() or display


# ══════════════════════════════════════════════════════════════
# What Changed：翻譯 Phase 3 change_reason（我們自己產生、格式受控）
# ══════════════════════════════════════════════════════════════
def _fmt_priority(m):
    verb = "升至" if m.group(1) == "escalated" else "降至"
    return f"風險優先級由 {m.group(2)} {verb} {m.group(3)}"


def _fmt_casualty(m):
    zh = _CASUALTY_STATUS_ZH.get(m.group(1), m.group(1))
    return f"人員傷亡狀態更新：{zh}"


def _fmt_crew_count(m):
    label = _CREW_LABEL_ZH.get(m.group(1), m.group(1))
    return f"已確認{label} {m.group(2)} 人"


def _fmt_vessel_status(m):
    zh = _VESSEL_STATUS_ZH.get(m.group(1), m.group(1))
    return f"船舶狀態更新：{zh}"


def _fmt_fire_status(m):
    zh = _FIRE_STATUS_ZH.get(m.group(1), m.group(1))
    return f"火勢狀態更新：{zh}"


def _fmt_security(m):
    zh = _SUBTYPE_ZH.get(m.group(1), m.group(1))
    return f"事件性質更新為：{zh}"


def _fmt_port_status(m):
    zh = _PORT_STATUS_ZH.get(m.group(1), m.group(1))
    return f"港口狀態更新：{zh}"


def _fmt_nav_status(m):
    zh = _NAV_STATUS_ZH.get(m.group(1), m.group(1))
    return f"航道狀態更新：{zh}"


_CHANGE_PATTERNS: list[tuple[re.Pattern, callable]] = [
    (re.compile(r'^Priority (escalated|downgraded) (P\d) → (P\d)$'), _fmt_priority),
    (re.compile(r'^Severity score increased'), lambda m: "事件嚴重度評分上升"),
    (re.compile(r'^Management score increased'), lambda m: "整體風險評分上升"),
    (re.compile(r'^Casualty status (?:changed|update:) .+ → (\w+)$'), _fmt_casualty),
    (re.compile(r'^Crew (injured|fatalities|missing) count updated: .+ → (\d+)$'), _fmt_crew_count),
    (re.compile(r'^Vessel status changed .+ → (\w+)$'), _fmt_vessel_status),
    (re.compile(r'^Fire status changed .+ → (\w+)$'), _fmt_fire_status),
    (re.compile(r'^Security incident type changed .+ → (\w+)$'), _fmt_security),
    (re.compile(r'^Port Status changed .+ → (\w+)$'), _fmt_port_status),
    (re.compile(r'^Navigation Status changed .+ → (\w+)$'), _fmt_nav_status),
    (re.compile(r'^Confidence upgraded (\w+) → (\w+)'),
     lambda m: f"情報可信度由 {m.group(1)} 升為 {m.group(2)}"),
    (re.compile(r'^Confidence changed (\w+) → (\w+)'),
     lambda m: f"情報可信度變化：{m.group(1)} → {m.group(2)}"),
    (re.compile(r'^Information upgraded from (\w+) to (\w+)'),
     lambda m: f"情報狀態由 {m.group(1)} 升級為 {m.group(2)}"),
    (re.compile(r'^Information status changed (\w+) → (\w+)'),
     lambda m: f"情報狀態變化：{m.group(1)} → {m.group(2)}"),
    (re.compile(r'^Fleet relevance increased'), lambda m: "船隊關聯性提高"),
    (re.compile(r'^Vessel identified as (.+)$'),
     lambda m: f"已確認船舶身份：{m.group(1)}"),
    (re.compile(r'^Operator identified as (.+)$'),
     lambda m: "已確認營運航商"),
    (re.compile(r'^Resolution confirmed'), lambda m: "事件已確認解除"),
    (re.compile(r'^Event reopened'), lambda m: "事件再度轉為活躍狀態"),
]


def translate_change_reason(change_reason: Optional[str]) -> list[str]:
    """
    把 Phase 3 `event.change_reason`（"; " 串接的英文片語，格式由
    material_change_detector.py / event_lifecycle.py 自己產生、完全受控）
    轉成繁中條列。無法辨識的片語不捏造翻譯，原樣附註「更新：」開頭保留。
    """
    if not change_reason:
        return []
    bullets: list[str] = []
    for fragment in change_reason.split("; "):
        fragment = fragment.strip()
        if not fragment:
            continue
        matched = False
        for pattern, fmt in _CHANGE_PATTERNS:
            m = pattern.match(fragment)
            if m:
                bullets.append(fmt(m))
                matched = True
                break
        if not matched:
            bullets.append(f"更新：{fragment}")
    # 去重（保留順序）
    seen = set()
    out = []
    for b in bullets:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


# ══════════════════════════════════════════════════════════════
# ManagementSummaryBuilder
# ══════════════════════════════════════════════════════════════
class ManagementSummaryBuilder:

    def __init__(self, risk_rules: Optional[dict] = None):
        self.rules = risk_rules or load_risk_rules()
        self._carrier_zh = {
            c["key"]: _zh_part(c.get("display", c["key"]))
            for c in self.rules.get("major_carriers", [])
        }
        self._vessel_type_zh = {
            v["key"]: _zh_part(v.get("display", v["key"]))
            for v in self.rules.get("vessel_types", [])
        }
        self._own_fleet_keys = {
            c["key"] for c in self.rules.get("major_carriers", []) if c.get("is_own_fleet")
        }

    # ── 便利方法 ─────────────────────────────────────────────
    def is_own_fleet(self, event) -> bool:
        return bool(event.carrier and event.carrier in self._own_fleet_keys)

    def location_zh(self, event) -> Optional[str]:
        loc = event.location or event.port
        return _zh_part(loc) if loc else None

    def carrier_zh(self, event) -> Optional[str]:
        if not event.carrier:
            return None
        return self._carrier_zh.get(event.carrier, event.carrier)

    def vessel_type_zh(self, event) -> Optional[str]:
        if not event.vessel_type:
            return None
        return self._vessel_type_zh.get(event.vessel_type, event.vessel_type)

    def subject_display(self, event) -> str:
        """事件的主要描述對象：船名 > 航商+船型 > 地點。"""
        if event.vessel_name:
            return event.vessel_name
        carrier = self.carrier_zh(event)
        vtype = self.vessel_type_zh(event) or "船舶"
        if carrier:
            return f"{carrier}{vtype}"
        return vtype

    def _is_unverified(self, event) -> bool:
        return event.information_status in (
            InformationStatus.EARLY_SIGNAL, InformationStatus.UNCONFIRMED,
        )

    def _hedge_prefix(self, event) -> str:
        return "據報，" if self._is_unverified(event) else ""

    def _status_phrase(self, event) -> str:
        """從 operational fields 挑一個最具體的狀態描述，缺資料就用中性描述。"""
        if event.casualty_status and event.casualty_status != "NONE_REPORTED":
            return _CASUALTY_STATUS_ZH.get(event.casualty_status, "")
        if event.fire_status:
            return _FIRE_STATUS_ZH.get(event.fire_status, "")
        if event.vessel_status:
            return _VESSEL_STATUS_ZH.get(event.vessel_status, "")
        if event.port_status:
            return _PORT_STATUS_ZH.get(event.port_status, "")
        if event.navigation_status:
            return _NAV_STATUS_ZH.get(event.navigation_status, "")
        return "詳細狀況仍待進一步確認" if self._is_unverified(event) else "相關單位持續掌握狀況"

    # ── 1. Management Headline（§五十七〜五十八）─────────────────
    def management_headline(self, event, max_chars: int = 35) -> str:
        location = self.location_zh(event)
        subtype_label = _SUBTYPE_ZH.get(event.incident_subtype) or _EVENT_TYPE_ZH.get(
            event.event_type, "海事動態"
        )
        subject = self.subject_display(event)

        if location:
            headline = f"{location}{subtype_label}"
        else:
            headline = f"{subject}{subtype_label}"

        if len(headline) > max_chars:
            headline = headline[: max_chars - 1] + "…"
        return headline

    # ── 2. Management Summary（§二十二〜二十四）───────────────────
    def management_summary(self, event, max_chars: int = 120) -> str:
        location = self.location_zh(event) or "該海域"
        subject = self.subject_display(event)
        subtype = event.incident_subtype
        status = self._status_phrase(event)
        hedge = self._hedge_prefix(event)

        if subtype == "FIRE" or event.fire_status:
            text = f"{hedge}{subject}於{location}發生火災事件，目前{status}，可能影響船舶安全及後續營運。"
        elif subtype == "GROUNDING":
            text = f"{hedge}{subject}於{location}發生擱淺，目前{status}，需持續關注船況及航道作業影響。"
        elif subtype in ("VESSEL_ATTACK", "HIJACKING", "STOWAWAY_DRUGS"):
            text = f"{hedge}{location}發生商船遭攻擊事件，目前資訊顯示{status}，該區域航安風險提高。"
        elif subtype in ("COLLISION", "ALLISION"):
            verb = "衝撞固定物" if subtype == "ALLISION" else "碰撞"
            text = f"{hedge}{subject}於{location}發生{verb}事件，目前{status}，需關注船體損害及航行安全。"
        elif subtype == "PORT_DISRUPTION" or event.port_status or event.navigation_status:
            reason = _PORT_STATUS_ZH.get(event.port_status) or _NAV_STATUS_ZH.get(event.navigation_status) or "作業中斷"
            text = f"{hedge}{location}港口／航道因故{reason}，可能影響靠離泊安排、船期及港口作業。"
        elif event.event_type == EventType.REGULATORY:
            text = f"{hedge}相關主管機關發布新的法規要求，可能影響船舶營運及合規作業，建議持續關注後續細節。"
        elif subtype == "POLLUTION" or event.pollution_status:
            text = f"{hedge}{subject}於{location}傳出污染事件，目前{status}，可能涉及環境影響及後續清理作業。"
        elif subtype == "SINKING":
            text = f"{hedge}{subject}於{location}傳出沉沒事故，目前{status}，需高度關注人員及環境安全。"
        elif subtype == "LOSS_OF_PROPULSION":
            text = f"{hedge}{subject}於{location}發生失去推進能力（Loss of Propulsion）事件，目前{status}，可能影響航行安全及船期。"
        else:
            label = _EVENT_TYPE_ZH.get(event.event_type, "海事動態")
            text = f"{hedge}{location}發生{label}，目前{status}，建議持續監控後續發展。"

        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        return text

    # ── 3. Why It Matters（§二十五）──────────────────────────────
    def why_it_matters(self, event) -> str:
        if self.is_own_fleet(event):
            return "涉及本公司船隊，建議列為最高優先資訊並確認船端／營運端狀況。"

        et = event.event_type
        if et == EventType.SAFETY:
            return "可能涉及船體、貨物及人員安全，若事故位於主要航道亦可能造成後續交通影響。"
        if et == EventType.SECURITY:
            return "事件發生於重要商船航線，可能提高區域航行及 War Risk exposure。"
        if et == EventType.OPERATIONS:
            return "可能影響靠離泊安排、港口等待時間及船期可靠度。"
        if et == EventType.REGULATORY:
            return "需評估是否涉及現行船隊作業程序、設備或文件要求。"
        if et == EventType.CREW:
            return "涉及船員安全與福祉，可能影響船舶調度及人員後續安排。"
        if et == EventType.ENVIRONMENT:
            return "可能涉及環境法規責任及後續清理成本，建議持續關注。"
        if et == EventType.MARKET:
            return "可能影響艙位供給、運價走勢及航線規劃參考。"
        if et == EventType.COMPETITOR:
            return "為主要競爭航商之營運動態，可作為航網及艙位策略參考。"
        return "建議持續監控後續發展及是否有進一步影響。"

    # ── 4. What Changed（§二十六〜二十七）────────────────────────
    def what_changed(self, event) -> list[str]:
        return translate_change_reason(event.change_reason)
