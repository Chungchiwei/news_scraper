#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
executive_email_renderer.py
海事航運新聞監控系統 — Phase 4 Executive Email Renderer

職責：
  把 EmailEventViewModel / ExecutiveBriefViewModel（純資料，已經過
  BriefingSelector + ManagementSummaryBuilder + EmailViewModelBuilder
  計算完畢）渲染成 HTML 字串。

  ★ Renderer 本身不做任何風險判斷、不生成任何中文文字、不重新排序，
    只負責「把已經算好的欄位排進 table」（§四十六）。
  ★ Table-based HTML + inline CSS，不使用 Grid/Flexbox/JavaScript/
    外部樣式表，確保 Outlook Desktop/Web、Gmail 相容（§三十一〜）。
  ★ 不使用外部 logo 圖片，Header 純文字 + 企業風格配色。
  ★ 所有外部/未信任文字（headline、summary、來源名稱、地點...）
    一律經過 HTML escape；連結一律做 scheme allow-list（僅 http/https）。
  ★ None / 空值欄位整行省略，不渲染 "Vessel: None"。
"""

from __future__ import annotations

from typing import Optional

from email_view_model import EmailEventViewModel, ExecutiveBriefViewModel


class ExecutiveEmailRenderer:

    WIDTH = 780

    # ── 企業配色（§二十二 UI 視覺原則）───────────────────────────
    NAVY        = "#0b1f3a"   # Deep Navy — Header
    PORT_BLUE   = "#13315c"   # Port Blue — 次要區塊
    WHITE       = "#ffffff"
    LIGHT_GRAY  = "#f1f5f9"   # 版面底色
    BORDER      = "#e2e8f0"
    TEXT        = "#1e293b"
    MUTED       = "#64748b"
    GREEN       = "#15803d"   # Resolved / Normal
    GREEN_BG    = "#f0fdf4"
    AMBER_BG    = "#fffbeb"

    _ALLOWED_SCHEMES = ("http://", "https://")

    # ── 安全性工具 ───────────────────────────────────────────────
    @staticmethod
    def esc(text) -> str:
        """所有外部/未信任文字進 HTML 前必經流程（§security）。"""
        if text is None:
            return ""
        s = str(text)
        return (s.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;")
                 .replace("'", "&#x27;"))

    @classmethod
    def safe_url(cls, url: Optional[str]) -> Optional[str]:
        """僅允許 http/https，拒絕 javascript: / data: / file: 等 scheme。"""
        if not url:
            return None
        u = url.strip()
        if u.lower().startswith(cls._ALLOWED_SCHEMES):
            return cls.esc(u)
        return None

    # ══════════════════════════════════════════════════════════
    # 對外入口
    # ══════════════════════════════════════════════════════════
    def render_daily_brief(self, vm: ExecutiveBriefViewModel) -> str:
        return self._render(vm, mode="daily_brief")

    def render_alert(self, vm: ExecutiveBriefViewModel) -> str:
        return self._render(vm, mode="alert")

    # ══════════════════════════════════════════════════════════
    # 組裝
    # ══════════════════════════════════════════════════════════
    def _render(self, vm: ExecutiveBriefViewModel, mode: str) -> str:
        parts = [self._html_open(vm), self._header(vm), self._risk_banner(vm),
                  self._executive_summary(vm), self._exposure_summary_block(vm)]

        has_content = False

        if vm.immediate:
            has_content = True
            parts.append(self._section_title("🚨 IMMEDIATE ATTENTION", "#dc2626"))
            for e in vm.immediate:
                parts.append(self._event_card(e))
            if vm.overflow.get("P1"):
                parts.append(self._overflow_note(vm.overflow["P1"], "P1 Event(s)"))

        if mode == "daily_brief":
            if vm.watch:
                has_content = True
                parts.append(self._section_title("⚠️ MANAGEMENT WATCH", "#c2410c"))
                for e in vm.watch:
                    parts.append(self._event_card(e))
                if vm.overflow.get("P2"):
                    parts.append(self._overflow_note(vm.overflow["P2"], "P2 Event(s)"))

            if vm.industry:
                has_content = True
                parts.append(self._section_title("🔎 OPERATIONAL & INDUSTRY WATCH", "#b45309"))
                for e in vm.industry:
                    parts.append(self._event_card(e))
                if vm.overflow.get("P3"):
                    parts.append(self._overflow_note(vm.overflow["P3"], "P3 Event(s)"))

            if vm.resolved:
                has_content = True
                parts.append(self._section_title("✅ RESOLVED / IMPROVED", self.GREEN))
                for e in vm.resolved:
                    parts.append(self._event_card(e, resolved=True))

        if not has_content:
            parts.append(self._no_risk_block(mode))

        parts.append(self._footer(vm))
        parts.append(self._html_close())
        return "".join(parts)

    # ══════════════════════════════════════════════════════════
    # 版面元件
    # ══════════════════════════════════════════════════════════
    def _html_open(self, vm: ExecutiveBriefViewModel) -> str:
        title = self.esc(vm.subject)
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background-color:{self.LIGHT_GRAY};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{self.LIGHT_GRAY};">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="{self.WIDTH}" cellpadding="0" cellspacing="0" border="0" style="max-width:{self.WIDTH}px;width:100%;background-color:{self.WHITE};border:1px solid {self.BORDER};">
"""

    def _html_close(self) -> str:
        return """</table>
</td></tr>
</table>
</body>
</html>"""

    # ── 品牌列（2026-08 視覺改版）─────────────────────────────────
    # ★ 只改「怎麼呈現」：Header 底色從純深藍改成白/淺灰＋🚢 圖示，
    #   加回使用者慣用的 "Present by Marine Technology Division_FRM"
    #   品牌行；風險等級與 P1/P2/P3/Resolved 數字則改用參考信裡那種
    #   彩色統計方塊（見 _risk_banner/_stat_tile）。所有原本存在的
    #   欄位（company/title/subtitle/generated_at/risk/counts）都還在，
    #   沒有任何內容被拿掉——純視覺重排，內容架構不變。
    BRAND_LINE = "Present by Marine Technology Division_FRM"

    def _header(self, vm: ExecutiveBriefViewModel) -> str:
        company = self.esc(vm.company_name)
        title = self.esc(vm.brief_title)
        subtitle = self.esc(vm.brief_subtitle)
        generated = self.esc(vm.generated_at_display)
        total = vm.p1_count + vm.p2_count + vm.monitored_count + len(vm.resolved)
        return f"""<tr><td style="background-color:#f8fafc;padding:22px 28px;border-bottom:1px solid {self.BORDER};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td valign="top">
<div style="font:bold 13px/1.4 Arial,Helvetica,sans-serif;color:{self.NAVY};letter-spacing:1px;">{company}</div>
<div style="font:bold 21px/1.4 'Microsoft JhengHei',Arial,sans-serif;color:{self.TEXT};padding-top:4px;">&#128674;&nbsp;{title}</div>
<div style="font:13px/1.4 Arial,Helvetica,sans-serif;color:{self.MUTED};padding-top:2px;">{subtitle}</div>
<div style="font:bold 12px/1.4 Arial,Helvetica,sans-serif;color:#c2410c;padding-top:8px;">{self.esc(self.BRAND_LINE)}</div>
</td>
<td align="right" valign="top">
<div style="font:12px Arial,Helvetica,sans-serif;color:{self.MUTED};">最後更新: {generated}</div>
<div style="padding-top:10px;">
<span style="display:inline-block;background-color:{self.BORDER};color:{self.TEXT};font:bold 11px Arial,Helvetica,sans-serif;padding:6px 12px;border-radius:3px;">涵蓋事件&nbsp;{total}&nbsp;則</span>
</div>
</td>
</tr></table>
</td></tr>"""

    # ── 彩色統計方塊（單一 tile，供 _risk_banner 組成整排）──────────
    def _stat_tile(self, icon: str, label: str, count: int, color: str) -> str:
        if count > 0:
            bg, num_color, label_color = color, "#ffffff", "#ffffff"
        else:
            bg, num_color, label_color = "#f8fafc", "#cbd5e1", "#94a3b8"
        return f"""<td align="center" bgcolor="{bg}" style="padding:16px 4px;width:20%;border-right:1px solid #ffffff;">
<div style="font:bold 24px Arial,Helvetica,sans-serif;color:{num_color};">{count}</div>
<div style="font:11px Arial,Helvetica,sans-serif;color:{label_color};padding-top:4px;">{icon}&nbsp;{self.esc(label)}</div>
</td>"""

    def _risk_banner(self, vm: ExecutiveBriefViewModel) -> str:
        color = vm.overall_risk_color
        label_map = {
            "HIGH": "HIGH", "ELEVATED": "ELEVATED", "WATCH": "WATCH", "NORMAL": "NORMAL",
        }
        risk_label = label_map.get(vm.overall_risk, vm.overall_risk)
        total = vm.p1_count + vm.p2_count + vm.monitored_count + len(vm.resolved)

        tiles = "".join([
            self._stat_tile("&#128680;", "P1 IMMEDIATE", vm.p1_count, "#dc2626"),
            self._stat_tile("&#128992;", "P2 WATCH", vm.p2_count, "#c2410c"),
            self._stat_tile("&#128337;", "P3 INDUSTRY", vm.monitored_count, "#0369a1"),
            self._stat_tile("&#9989;", "RESOLVED", len(vm.resolved), self.GREEN),
        ])

        return f"""<tr><td style="padding:0;border-bottom:1px solid {self.BORDER};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td align="center" bgcolor="{self.NAVY}" style="padding:16px 4px;width:20%;border-right:1px solid #ffffff;">
<div style="font:bold 24px Arial,Helvetica,sans-serif;color:#ffffff;">{total}</div>
<div style="font:11px Arial,Helvetica,sans-serif;color:#94a3b8;padding-top:4px;">&#128240;&nbsp;本次總計</div>
</td>
{tiles}
</tr></table>
</td></tr>
<tr><td style="padding:14px 28px;border-bottom:1px solid {self.BORDER};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="font:bold 11px Arial,Helvetica,sans-serif;color:{self.MUTED};letter-spacing:1px;">TODAY'S RISK LEVEL</td></tr>
<tr><td style="padding-top:6px;">
<span style="display:inline-block;background-color:{color};color:#fff;font:bold 15px Arial,Helvetica,sans-serif;padding:5px 14px;border-radius:3px;">{risk_label}</span>
</td></tr>
<tr><td style="padding-top:12px;font:12px Arial,Helvetica,sans-serif;color:{self.MUTED};">
P1 Events: <b style="color:{self.TEXT};">{vm.p1_count}</b>
&nbsp;&nbsp;|&nbsp;&nbsp;
P2 Events: <b style="color:{self.TEXT};">{vm.p2_count}</b>
&nbsp;&nbsp;|&nbsp;&nbsp;
Industry Watch: <b style="color:{self.TEXT};">{vm.monitored_count}</b>
</td></tr>
</table>
</td></tr>"""

    def _executive_summary(self, vm: ExecutiveBriefViewModel) -> str:
        summary = self.esc(vm.executive_summary_zh)
        return f"""<tr><td style="padding:18px 28px;background-color:{self.LIGHT_GRAY};border-bottom:1px solid {self.BORDER};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="font:bold 11px Arial,Helvetica,sans-serif;color:{self.MUTED};letter-spacing:1px;">&#128203;&nbsp;EXECUTIVE SUMMARY</td></tr>
<tr><td style="padding-top:6px;font:14px/1.7 'Microsoft JhengHei',Arial,sans-serif;color:{self.TEXT};">{summary}</td></tr>
</table>
</td></tr>"""

    # ── Phase 6：WHL FLEET EXPOSURE 彙總區塊 ───────────────────────
    # ★ 只彙總「這封信裡實際顯示出來的事件」，不引入新的風險評分，
    #   也不改變 Overall Risk / P1-P4 分桶（§Phase 6 五十五〜五十八）。
    # ★ has_operational_assessment=False（Phase 6 尚未接上，或這次沒有
    #   任何事件跑過 Operational Relevance）時整段不畫——對純
    #   Phase 1-5 呼叫端輸出零影響。
    def _exposure_summary_block(self, vm: ExecutiveBriefViewModel) -> str:
        if not vm.has_operational_assessment:
            return ""
        bits = [
            f'Direct: <b style="color:{self.TEXT};">{vm.exposure_direct_count}</b>',
            f'High: <b style="color:{self.TEXT};">{vm.exposure_high_count}</b>',
            f'Affected Vessels: <b style="color:{self.TEXT};">{vm.exposure_affected_vessel_count}</b>',
        ]
        caveat = ""
        if vm.exposure_unavailable:
            caveat = (f'<tr><td style="padding-top:6px;font:11px Arial,Helvetica,sans-serif;color:#92400e;">'
                      f'&#9888; Fleet/schedule data unavailable for one or more events this run — '
                      f'exposure for those events is shown as Unavailable, not "no exposure".</td></tr>')
        elif vm.exposure_stale:
            caveat = (f'<tr><td style="padding-top:6px;font:11px Arial,Helvetica,sans-serif;color:#92400e;">'
                      f'&#9888; 部分船期曝險評估係依非最新船期資料，請留意資料時效。</td></tr>')
        return f"""<tr><td style="padding:14px 28px;background-color:#eff6ff;border-bottom:1px solid {self.BORDER};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="font:bold 11px Arial,Helvetica,sans-serif;color:{self.NAVY};letter-spacing:1px;">WHL FLEET EXPOSURE</td></tr>
<tr><td style="padding-top:6px;font:12px Arial,Helvetica,sans-serif;color:{self.MUTED};">{"&nbsp;&nbsp;|&nbsp;&nbsp;".join(bits)}</td></tr>
{caveat}
</table>
</td></tr>"""

    # ── Phase 6：單一 Event Card 的 WHL OPERATIONAL EXPOSURE 區塊 ──────
    # ★ status=UNAVAILABLE / level=NONE(or None) / 一般 DIRECT〜LOW 三種
    #   措辭分開處理，絕不把 Unavailable 顯示成 NONE（§六十四〜六十五）。
    _EXPOSURE_LEVEL_COLOR = {
        "DIRECT": "#7f1d1d", "HIGH": "#c2410c", "MODERATE": "#b45309",
        "LOW": "#64748b", "NONE": "#94a3b8",
    }

    def _exposure_card_block(self, e: EmailEventViewModel) -> str:
        if not e.has_operational_assessment:
            return ""

        if e.relevance_status == "UNAVAILABLE":
            badge = self._badge("UNAVAILABLE", "#475569")
            body_html = (f'<div style="font:12px/1.6 Arial,Helvetica,sans-serif;color:{self.MUTED};">'
                        f'{self.esc(e.exposure_unavailable_text or "Unavailable")}</div>')
        elif e.relevance_level in (None, "NONE"):
            badge = self._badge("NONE", self._EXPOSURE_LEVEL_COLOR.get("NONE"))
            body_html = (f'<div style="font:12px/1.6 Arial,Helvetica,sans-serif;color:{self.MUTED};">'
                        f'{self.esc(e.exposure_no_direct_text or "No direct exposure identified")}</div>')
        else:
            badge = self._badge(e.relevance_level, self._EXPOSURE_LEVEL_COLOR.get(e.relevance_level, "#64748b"))
            lines = []
            if e.exposure_vessels_display:
                lines.append("Vessels: " + "; ".join(self.esc(v) for v in e.exposure_vessels_display))
            elif e.exposure_service_codes:
                lines.append("Service: " + ", ".join(self.esc(s) for s in e.exposure_service_codes))
            if e.exposure_closest_eta_display and not e.exposure_vessels_display:
                lines.append(f"Closest ETA: {self.esc(e.exposure_closest_eta_display)}")
            if not lines:
                lines.append("Exposure identified — see operational log for detail.")
            body_html = "".join(
                f'<div style="font:12px/1.6 Arial,Helvetica,sans-serif;color:{self.TEXT};">{ln}</div>'
                for ln in lines
            )

        stale_html = ""
        if e.exposure_is_stale and e.exposure_stale_note:
            stale_html = (f'<div style="padding-top:4px;font:11px Arial,Helvetica,sans-serif;color:#92400e;">'
                          f'&#9888; {self.esc(e.exposure_stale_note)}</div>')

        return f"""
<tr><td style="padding-top:10px;">
<div style="font:bold 11px Arial,Helvetica,sans-serif;color:{self.MUTED};letter-spacing:0.5px;">WHL OPERATIONAL EXPOSURE&nbsp;&nbsp;{badge}</div>
<div style="padding-top:3px;">{body_html}</div>
{stale_html}
</td></tr>"""

    def _section_title(self, label: str, color: str) -> str:
        return f"""<tr><td style="padding:20px 28px 8px 28px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="border-bottom:2px solid {color};padding-bottom:6px;font:bold 13px Arial,Helvetica,sans-serif;color:{color};letter-spacing:1px;">{self.esc(label)}</td></tr>
</table>
</td></tr>"""

    def _overflow_note(self, count: int, label: str) -> str:
        return f"""<tr><td style="padding:0 28px 12px 28px;">
<div style="font:12px Arial,Helvetica,sans-serif;color:{self.MUTED};">+{count} additional {self.esc(label)} — see full event log.</div>
</td></tr>"""

    # ── Event Card ───────────────────────────────────────────────
    def _badge(self, text: str, bg: str, fg: str = "#ffffff") -> str:
        return (f'<span style="display:inline-block;background-color:{bg};color:{fg};'
                f'font:bold 10px Arial,Helvetica,sans-serif;padding:3px 8px;'
                f'border-radius:3px;letter-spacing:0.5px;">{self.esc(text)}</span>')

    def _event_card(self, e: EmailEventViewModel, resolved: bool = False) -> str:
        bar_color = self.GREEN if resolved else e.priority_color
        badges = [self._badge(e.priority_label.split(" — ")[0], bar_color)]

        if e.is_own_fleet:
            badges.append(self._badge("OWN FLEET", self.NAVY))

        if e.notification_badge == "NEW":
            badges.append(self._badge("NEW", "#334155"))
        elif e.notification_badge == "UPDATE":
            badges.append(self._badge("UPDATE", "#0369a1"))
        elif e.notification_badge == "RESOLVED":
            badges.append(self._badge("RESOLVED", self.GREEN))

        badge_html = "&nbsp;".join(badges)

        original_headline_html = ""
        if e.original_headline and e.original_headline.strip() != e.headline_zh.strip():
            original_headline_html = (
                f'<div style="font:12px/1.5 Arial,Helvetica,sans-serif;color:{self.MUTED};'
                f'padding-top:2px;">{self.esc(e.original_headline)}</div>'
            )

        what_changed_html = ""
        if e.what_changed_zh:
            items = "".join(
                f'<div style="padding:2px 0;">&bull;&nbsp;{self.esc(item)}</div>'
                for item in e.what_changed_zh
            )
            what_changed_html = f"""
<tr><td style="padding-top:10px;">
<div style="font:bold 11px Arial,Helvetica,sans-serif;color:{self.MUTED};letter-spacing:0.5px;">WHAT CHANGED</div>
<div style="font:12px/1.7 Arial,Helvetica,sans-serif;color:{self.TEXT};padding-top:2px;">{items}</div>
</td></tr>"""

        early_signal_html = ""
        if e.is_early_signal:
            label = self.esc(e.information_status_label or "UNCONFIRMED")
            early_signal_html = f"""
<tr><td style="padding-top:10px;">
<div style="background-color:{self.AMBER_BG};border:1px solid #fbbf24;border-radius:3px;padding:6px 10px;font:bold 11px Arial,Helvetica,sans-serif;color:#92400e;">
&#9888; {label} — Awaiting independent confirmation
</div>
</td></tr>"""

        # ── Phase 6：WHL OPERATIONAL EXPOSURE（有跑 Operational Relevance
        # Engine 才會非空，見 email_view_model.py _build_exposure_fields）──
        exposure_html = self._exposure_card_block(e)

        # ── Phase 5：Incident Timeline（只有 P1 或多階段 Material Update
        # 才會被 view model 填入，最多 3-5 節點，見 email_view_model.py）──
        timeline_html = ""
        if e.timeline:
            rows = "".join(
                f'<div style="padding:3px 0;">'
                f'<span style="color:{self.MUTED};font:11px Arial,Helvetica,sans-serif;">'
                f'{self.esc(t.get("time_display") or "—")}</span>'
                f'&nbsp;&nbsp;{self.esc(t.get("summary_zh", ""))}'
                f'</div>'
                for t in e.timeline if t.get("summary_zh")
            )
            if rows:
                timeline_html = f"""
<tr><td style="padding-top:10px;">
<div style="font:bold 11px Arial,Helvetica,sans-serif;color:{self.MUTED};letter-spacing:0.5px;">INCIDENT TIMELINE</div>
<div style="font:12px/1.8 Arial,Helvetica,sans-serif;color:{self.TEXT};padding-top:2px;">{rows}</div>
</td></tr>"""

        # ── Phase 5：Contradiction / Information Note（只有 material
        # contradiction 才顯示，中性措辭，不由系統自行選邊，§二十五、五十）──
        contradiction_html = ""
        if e.contradiction_notes:
            notes = "".join(
                f'<div style="padding:2px 0;">{self.esc(note)}</div>'
                for note in e.contradiction_notes
            )
            contradiction_html = f"""
<tr><td style="padding-top:10px;">
<div style="background-color:#f1f5f9;border-left:3px solid {self.MUTED};padding:8px 10px;">
<div style="font:bold 11px Arial,Helvetica,sans-serif;color:{self.TEXT};letter-spacing:0.5px;">INFORMATION NOTE</div>
<div style="font:12px/1.6 Arial,Helvetica,sans-serif;color:{self.TEXT};padding-top:2px;">{notes}</div>
</div>
</td></tr>"""

        # 中繼資料（地點／船名／航商）— None 一律省略
        meta_bits = []
        if e.location:
            meta_bits.append(f"Location: {self.esc(e.location)}")
        if e.vessel_name:
            meta_bits.append(f"Vessel: {self.esc(e.vessel_name)}")
        if e.carrier_display:
            meta_bits.append(f"Carrier: {self.esc(e.carrier_display)}")
        meta_html = ""
        if meta_bits:
            meta_html = (f'<tr><td style="padding-top:10px;font:12px/1.7 Arial,Helvetica,sans-serif;'
                         f'color:{self.TEXT};">' + " &nbsp;|&nbsp; ".join(meta_bits) + "</td></tr>")

        # Impact tags
        tags_html = ""
        if e.impact_tags:
            tag_spans = "&nbsp;".join(
                self._badge(t, "#475569") for t in e.impact_tags
            )
            tags_html = f'<tr><td style="padding-top:10px;">{tag_spans}</td></tr>'

        # 來源與可信度
        conf_bits = []
        if e.confidence_level:
            conf_bits.append(f"Confidence: {self.esc(e.confidence_level)}")
        if e.information_status_label:
            conf_bits.append(self.esc(e.information_status_label))
        conf_line = " · ".join(conf_bits)

        source_bits = []
        if e.independent_source_count:
            unit = "independent source" if e.independent_source_count == 1 else "independent sources"
            source_bits.append(f"{e.independent_source_count} {unit}")
        if e.source_names:
            source_bits.append(" · ".join(self.esc(n) for n in e.source_names))
        source_line = " — ".join(source_bits)

        link_html = ""
        safe_url = self.safe_url(e.primary_url)
        if safe_url:
            link_html = (f'&nbsp;&nbsp;<a href="{safe_url}" style="color:#1d4ed8;'
                        f'text-decoration:none;font:bold 11px Arial,Helvetica,sans-serif;">'
                        f'Read Primary Source &rsaquo;</a>')

        footer_html = ""
        if conf_line or source_line or link_html:
            footer_html = f"""
<tr><td style="padding-top:12px;border-top:1px solid {self.BORDER};margin-top:4px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td style="font:11px Arial,Helvetica,sans-serif;color:{self.MUTED};padding-top:8px;">
{conf_line}{'&nbsp;&nbsp;|&nbsp;&nbsp;' if conf_line and source_line else ''}{source_line}{link_html}
</td>
</tr></table>
</td></tr>"""

        last_updated_html = ""
        if e.last_updated_display:
            last_updated_html = (f'<tr><td style="padding-top:4px;font:11px Arial,Helvetica,sans-serif;'
                                 f'color:{self.MUTED};">Last Updated: {self.esc(e.last_updated_display)}</td></tr>')

        return f"""<tr><td style="padding:0 28px 16px 28px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid {self.BORDER};border-radius:4px;">
<tr>
<td width="6" bgcolor="{bar_color}" style="font-size:0;line-height:0;">&nbsp;</td>
<td style="padding:16px 18px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td>{badge_html}</td></tr>
<tr><td style="padding-top:8px;font:bold 16px/1.4 'Microsoft JhengHei',Arial,sans-serif;color:{self.TEXT};">{self.esc(e.headline_zh)}</td></tr>
{original_headline_html and f"<tr><td>{original_headline_html}</td></tr>" or ""}
<tr><td style="padding-top:8px;font:13px/1.7 'Microsoft JhengHei',Arial,sans-serif;color:{self.TEXT};">{self.esc(e.management_summary_zh)}</td></tr>
{what_changed_html}
<tr><td style="padding-top:10px;">
<div style="font:bold 11px Arial,Helvetica,sans-serif;color:{self.MUTED};letter-spacing:0.5px;">WHY IT MATTERS</div>
<div style="font:13px/1.7 'Microsoft JhengHei',Arial,sans-serif;color:{self.TEXT};padding-top:2px;">{self.esc(e.why_it_matters_zh)}</div>
</td></tr>
{exposure_html}
{timeline_html}
{contradiction_html}
{early_signal_html}
{meta_html}
{tags_html}
{last_updated_html}
{footer_html}
</table>
</td>
</tr>
</table>
</td></tr>"""

    def _no_risk_block(self, mode: str) -> str:
        msg = ("No immediate P1 events at this time."
               if mode == "alert" else
               "No significant fleet risk events identified during this monitoring period.")
        return f"""<tr><td style="padding:32px 28px;text-align:center;">
<div style="font:14px Arial,Helvetica,sans-serif;color:{self.MUTED};">{self.esc(msg)}</div>
</td></tr>"""

    # §四十七：不逐卡貼 "AI Generated" 標籤，只在 Footer 簡短揭露一次。
    AI_DISCLAIMER_TEXT = (
        "Management summaries may include automated source-grounded analysis. "
        "All summaries are traceable to the original sources listed on each event."
    )

    def _footer(self, vm: ExecutiveBriefViewModel) -> str:
        note = ""
        has_unverified = any(getattr(e, "is_early_signal", False)
                              for bucket in (vm.immediate, vm.watch, vm.industry, vm.resolved)
                              for e in bucket)
        if has_unverified:
            note = ('<tr><td style="padding-top:6px;font:11px/1.6 Arial,Helvetica,sans-serif;'
                    f'color:{self.MUTED};">Items marked EARLY SIGNAL are unconfirmed and should '
                    'not be treated as verified fact pending independent confirmation.</td></tr>')

        ai_note = ""
        if getattr(vm, "has_ai_enhancement", False):
            ai_note = (f'<tr><td style="padding-top:6px;font:11px/1.6 Arial,Helvetica,sans-serif;'
                      f'color:{self.MUTED};">{self.esc(self.AI_DISCLAIMER_TEXT)}</td></tr>')

        return f"""<tr><td style="padding:20px 28px;background-color:{self.NAVY};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="font:11px Arial,Helvetica,sans-serif;color:#94a3b8;">
{self.esc(vm.company_name)} — Fleet Risk Management &nbsp;|&nbsp; This briefing is generated by a rule-based maritime intelligence system for internal reference. Confirm details with primary sources before operational decisions.
</td></tr>
{note}
{ai_note}
</table>
</td></tr>"""
