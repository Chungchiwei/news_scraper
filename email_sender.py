#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
email_sender.py  v2.1
海事航運新聞監控系統 — Email 發送模組
職責：HTML 渲染 + SMTP 發送
v2.1 更新：
  - 移除「11大航商」來源群組（航商 RSS 已於 v6.3 移除）
  - 移除 carrier_summary_row / carrier_note（無航商 RSS 來源）
  - 簡化 render_hit_rows（移除航商特殊標記）
  - EMAIL_SUBTITLE 補上鋰電池/貨櫃落海/偷渡/毒品走私
  - 版本號 v2.0 → v2.1
修改此檔案不影響爬蟲邏輯
"""

import os
import re
import smtplib
import logging
import traceback
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# Email 設定
# ══════════════════════════════════════════════════════════════
class EmailConfig:
    """
    所有 Email 相關設定集中於此。
    優先讀取環境變數，若無則使用下方預設值。
    """
    # ── SMTP 設定 ──────────────────────────────────────────────
    SMTP_SERVER: str = os.environ.get("MAIL_SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT:   int = int(os.environ.get("MAIL_SMTP_PORT", "587"))

    # ── 帳號設定 ──────────────────────────────────────────────
    MAIL_USER:    str = os.environ.get("MAIL_USER",     "")
    MAIL_PASS:    str = os.environ.get("MAIL_PASSWORD", "")
    TARGET_EMAIL: str = os.environ.get("TARGET_EMAIL",  "")

    # ── 郵件外觀設定 ──────────────────────────────────────────
    SENDER_NAME:    str = "海事航運監控系統"
    SUBJECT_PREFIX: str = "Maritime News Alert"

    # ── 版面文字設定 ──────────────────────────────────────────
    EMAIL_TITLE:    str = "🚢 海事航運新聞監控快報"
    # ★ v2.1 更新：補上 v6.3 新增風險類型，移除航商動態
    EMAIL_SUBTITLE: str = (
        "火災(含鋰電池) · 碰撞(含撞橋) · 擱淺沉沒(含貨櫃落海) · "
        "海盜攻擊(含偷渡/毒品走私) · 船員傷亡 · 其他海事動態"
    )
    EMAIL_BRANDING: str = "Present by Marine Technology Division_FRM"
    FOOTER_LINE1:   str = "此內容為系統自動發送，請勿直接回覆。"
    FOOTER_LINE2:   str = (
        "Maritime News Monitoring System · "
        "Powered by WHL Fleet Risk Management"
    )
    # ── 分類副標題（顯示於每個分類區塊標題列下方）────────────
    # ★ v2.2 新增：與 keywords_config.json 的新增風險類型對應
    CAT_SUBTITLES: dict = {
        "CAT1": "🔋 含鋰電池火災 · EV 載車船 · RoRo 火災",
        "CAT2": "🌉 含橋梁撞擊 · 礁石觸礁 · 海峽擱淺",
        "CAT3": "📦 含貨櫃落海 · 船舶全損 · 沉船事故",
        "CAT4": "🚶 含偷渡事件 · 毒品走私 · 索馬利亞／幾內亞灣海盜",
        "CAT5": "",   # 無需副標題
        "CAT6": "",
        "OTHER": "",
    }
    # ── 版面寬度 ──────────────────────────────────────────────
    EMAIL_WIDTH: int = 720

    # ── 時區（TPE = UTC+8）────────────────────────────────────
    DISPLAY_TZ_OFFSET: int = 8
    DISPLAY_TZ_NAME:   str = "TPE"


# ══════════════════════════════════════════════════════════════
# HTML 渲染器
# ══════════════════════════════════════════════════════════════
class EmailRenderer:
    """
    負責將 news_data dict 渲染成完整 HTML 字串。
    所有視覺樣式集中在此，修改版面只需動這個類別。
    """

    # ── 顏色對照（CAT6 保留，關鍵字仍存在）──────────────────
    KW_COLOR_MAP: dict = {
        "#dc2626": ("#fef2f2", "#dc2626"),   # CAT1 火災
        "#b45309": ("#fffbeb", "#b45309"),   # CAT2 碰撞
        "#7c3aed": ("#f5f3ff", "#7c3aed"),   # CAT3 沉沒
        "#0369a1": ("#eff6ff", "#0369a1"),   # CAT4 海盜
        "#047857": ("#ecfdf5", "#047857"),   # CAT5 船員
        "#0891b2": ("#ecfeff", "#0891b2"),   # CAT6 航商動態
        "#475569": ("#f1f5f9", "#475569"),   # OTHER
    }

    DARKER_COLOR_MAP: dict = {
        "#dc2626": "#b91c1c",
        "#b45309": "#92400e",
        "#7c3aed": "#6d28d9",
        "#0369a1": "#075985",
        "#047857": "#065f46",
        "#0891b2": "#0e7490",   # CAT6
        "#475569": "#334155",
    }

    LANG_BADGE: dict = {
        "en":    ("#dbeafe", "#1d4ed8", "EN"),
        "zh-TW": ("#dcfce7", "#15803d", "中文"),
        "zh-CN": ("#fef9c3", "#854d0e", "中文"),
    }

    def __init__(self, incident_categories: dict,
                 rss_sources: list, cnyes_sources: list):
        self.cats          = incident_categories
        self.rss_sources   = rss_sources
        self.cnyes_sources = cnyes_sources
        self._tz           = timezone(timedelta(hours=EmailConfig.DISPLAY_TZ_OFFSET))

    # ── 工具：時間轉換 ────────────────────────────────────────
    def _fmt_pub_time(self, pub: str) -> str:
        if pub == '時間未知':
            return pub
        try:
            dt = datetime.strptime(pub, '%Y-%m-%d %H:%M UTC').replace(
                tzinfo=timezone.utc)
            return dt.astimezone(self._tz).strftime('%m/%d %H:%M')
        except Exception:
            return pub

    # ── 工具：HTML 轉義 ───────────────────────────────────────
    @staticmethod
    def _esc(text: str) -> str:
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))

    # ─────────────────────────────────────────────────────────
    # 元件 1：單篇新聞卡片
    # ─────────────────────────────────────────────────────────
    def render_card(self, item: dict) -> str:
        cat_cfg      = self.cats.get(item.get('incident_cat', 'OTHER'),
                                     self.cats.get('OTHER', {}))
        border_color = cat_cfg.get('color', '#475569')
        pub          = self._fmt_pub_time(item['published'])

        lang     = item.get('source_lang', 'en')
        badge    = self.LANG_BADGE.get(lang, self.LANG_BADGE['en'])
        lang_bg, lang_fg, lang_text = badge

        safe_title   = self._esc(item.get('title',   ''))
        safe_summary = self._esc(item.get('summary', ''))

        # 關鍵字標籤（最多 3 個）
        kw_cells = ""
        for kw, _label, color in item.get('matched', [])[:3]:
            bg_c, fg_c = self.KW_COLOR_MAP.get(color, ("#f1f5f9", "#475569"))
            kw_cells += (
                f'<td bgcolor="{bg_c}" '
                f'style="padding:4px 10px;border:1px solid {fg_c};">'
                f'<font face="Arial,Microsoft JhengHei,sans-serif" '
                f'size="1" color="{fg_c}"><b>{self._esc(kw)}</b></font>'
                f'</td><td width="6"></td>'
            )

        link = item.get('link', '#')
        return f"""
        <table width="100%" border="0" cellpadding="0" cellspacing="0"
              bgcolor="#ffffff" style="margin-bottom:14px;border:1px solid #cbd5e1;">
        <tr>
          <td width="5" bgcolor="{border_color}" style="padding:0;">&nbsp;</td>
          <td style="padding:16px 18px;">

            <!-- 來源列 -->
            <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
              <td align="left" valign="middle">
                <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
                  {item.get('source_icon','')}&nbsp;{self._esc(item.get('source_name',''))}
                </font>&nbsp;
                <table border="0" cellpadding="0" cellspacing="0"
                      style="display:inline-table;"><tr>
                  <td bgcolor="{lang_bg}" style="padding:3px 8px;">
                    <font face="Arial,sans-serif" size="1"
                          color="{lang_fg}"><b>{lang_text}</b></font>
                  </td>
                </tr></table>
              </td>
              <td align="right" valign="middle">
                <font face="Arial,sans-serif" size="2" color="#94a3b8">
                  🕐&nbsp;{pub}
                </font>
              </td>
            </tr></table>

            <!-- 標題 -->
            <table width="100%" border="0" cellpadding="0" cellspacing="0"
                  style="margin-top:10px;"><tr><td>
              <a href="{link}" target="_blank" style="text-decoration:none;">
                <font face="Microsoft JhengHei,Arial,sans-serif"
                      size="4" color="#0f172a"><b>{safe_title}</b></font>
              </a>
            </td></tr></table>

            <!-- 摘要 -->
            <table width="100%" border="0" cellpadding="10" cellspacing="0"
                  bgcolor="#f8fafc"
                  style="margin-top:10px;border-left:3px solid {border_color};"><tr><td>
              <font face="Microsoft JhengHei,Arial,sans-serif"
                    size="2" color="#475569">
                {safe_summary or '（無摘要）'}
              </font>
            </td></tr></table>

            <!-- 關鍵字 + 閱讀按鈕 -->
            <table width="100%" border="0" cellpadding="0" cellspacing="0"
                  style="margin-top:12px;"><tr>
              <td align="left" valign="middle">
                <table border="0" cellpadding="0" cellspacing="0"><tr>
                  {kw_cells}
                </tr></table>
              </td>
              <td align="right" valign="middle">
                <table border="0" cellpadding="8" cellspacing="0"
                      bgcolor="{border_color}"><tr><td>
                  <a href="{link}" target="_blank" style="text-decoration:none;">
                    <font face="Arial,sans-serif" size="2" color="#ffffff">
                      <b>閱讀原文 &rarr;</b>
                    </font>
                  </a>
                </td></tr></table>
              </td>
            </tr></table>

          </td>
        </tr>
        </table>"""

    # ─────────────────────────────────────────────────────────
    # 元件 2：單一情境分類區塊
    # ─────────────────────────────────────────────────────────
    def render_incident_section(self, cat_key: str, news_list: list) -> str:
        cfg = self.cats[cat_key]

        # ── 無新聞時：簡易標題列 ─────────────────────────────
        if not news_list:
            return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:10px;border:1px solid #e2e8f0;">
  <tr>
    <td width="5" bgcolor="{cfg['color']}">&nbsp;</td>
    <td bgcolor="#ffffff" style="padding:12px 16px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif"
                size="3" color="{cfg['color']}">
            <b>{cfg['icon']}&nbsp;{cfg['label']}</b>
          </font>
        </td>
        <td align="right" valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif"
                size="2" color="#94a3b8">本期無相關新聞</font>
        </td>
      </tr></table>
    </td>
  </tr>
</table>"""

        # ── 有新聞時：完整區塊 ───────────────────────────────
        cards    = "".join(self.render_card(item) for item in news_list)
        count_bg = self.DARKER_COLOR_MAP.get(cfg['color'], "#334155")

        # ★ v2.2：副標題（空字串則不渲染）
        subtitle = EmailConfig.CAT_SUBTITLES.get(cat_key, "")
        subtitle_row = ""
        if subtitle:
            subtitle_row = (
                f'<br>'
                f'<font face="Microsoft JhengHei,Arial,sans-serif" '
                f'size="1" color="rgba(255,255,255,0.75)">'
                f'{subtitle}'
                f'</font>'
            )

        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:20px;border:1px solid #e2e8f0;">
  <tr>
    <td bgcolor="{cfg['color']}" style="padding:12px 18px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td align="left" valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif"
                size="4" color="#ffffff">
            <b>{cfg['icon']}&nbsp;{cfg['label']}</b>
          </font>
          {subtitle_row}
        </td>
        <td align="right" valign="middle" width="60">
          <table border="0" cellpadding="6" cellspacing="0"
                 bgcolor="{count_bg}"><tr><td align="center">
            <font face="Arial,sans-serif" size="2" color="#ffffff">
              <b>{len(news_list)} 則</b>
            </font>
          </td></tr></table>
        </td>
      </tr></table>
    </td>
  </tr>
  <tr>
    <td bgcolor="{cfg['bg']}" style="padding:16px 16px 2px 16px;">
      {cards}
    </td>
  </tr>
</table>"""


    # ─────────────────────────────────────────────────────────
    # 元件 3：統計格（頂部數字列）
    # ─────────────────────────────────────────────────────────
    def render_stat_cell(self, cat_key: str, count: int) -> str:
        cfg = self.cats[cat_key]
        if count > 0:
            return f"""
<td align="center" bgcolor="{cfg['color']}"
    style="padding:16px 4px;width:12%;border-right:1px solid #ffffff;">
  <font face="Arial,sans-serif" size="6" color="#ffffff"><b>{count}</b></font><br><br>
  <font face="Arial,sans-serif" size="3" color="#ffffff">{cfg['icon']}</font><br>
  <font face="Microsoft JhengHei,Arial,sans-serif"
        size="2" color="#ffffff">{cfg['label']}</font>
</td>"""
        return f"""
<td align="center" bgcolor="#f8fafc"
    style="padding:16px 4px;width:12%;border-right:1px solid #e2e8f0;">
  <font face="Arial,sans-serif" size="6" color="#cbd5e1"><b>0</b></font><br><br>
  <font face="Arial,sans-serif" size="3" color="#cbd5e1">{cfg['icon']}</font><br>
  <font face="Microsoft JhengHei,Arial,sans-serif"
        size="2" color="#64748b">{cfg['label']}</font>
</td>"""

    # ─────────────────────────────────────────────────────────
    # 元件 4：來源格線
    # ★ v2.1：移除「11大航商官方新聞」群組（來源已移除）
    # ─────────────────────────────────────────────────────────
    def render_source_grid(self) -> str:
        SOURCE_GROUPS = [
            {
                "title":   "中文媒體（台灣）",
                "icon":    "🇹🇼",
                "color":   "#059669",
                "bg":      "#f0fdf4",
                "border":  "#bbf7d0",
                "sources": (
                    [s for s in self.rss_sources
                     if s.get("lang") == "zh-TW"
                     and s.get("category") == "中文媒體"] +
                    [s for s in self.cnyes_sources
                     if s.get("lang") == "zh-TW"]
                ),
            },
            {
                "title":   "中文媒體（大陸）",
                "icon":    "🇨🇳",
                "color":   "#dc2626",
                "bg":      "#fff5f5",
                "border":  "#fecaca",
                "sources": [s for s in self.rss_sources
                            if s.get("lang") == "zh-CN"
                            and s.get("category") == "中文媒體"],
            },
            {
                "title":   "航運專業媒體",
                "icon":    "🚢",
                "color":   "#2563eb",
                "bg":      "#f0f7ff",
                "border":  "#bfdbfe",
                "sources": [s for s in self.rss_sources
                            if s.get("category") == "航運專業"],
            },
            {
                "title":   "國際媒體",
                "icon":    "🌐",
                "color":   "#ea580c",
                "bg":      "#fff7ed",
                "border":  "#fed7aa",
                "sources": [s for s in self.rss_sources
                            if s.get("category") == "國際媒體"],
            },
        ]

        groups_html = ""
        for grp in SOURCE_GROUPS:
            sources = grp["sources"]
            if not sources:
                continue

            rows_html = ""
            for i in range(0, len(sources), 3):
                chunk = sources[i:i+3]
                while len(chunk) < 3:
                    chunk.append(None)
                cells = ""
                for src in chunk:
                    if src is None:
                        cells += (
                            f'<td width="33%" bgcolor="{grp["bg"]}" '
                            f'style="padding:8px 10px;'
                            f'border-right:1px solid {grp["border"]};"></td>'
                        )
                    else:
                        name = src.get("name", "")
                        icon = src.get("icon", "📰")
                        url  = src.get("url") or src.get("api_url", "")
                        if url == "__oneshipping_html__":
                            url = "https://www.oneshipping.info"
                        display_url = url
                        for rsshub in ("rsshub.app/", "rsshub.rssforever.com/"):
                            if rsshub in url:
                                bk = src.get("backup_url", "")
                                if (bk and "rsshub" not in bk
                                        and bk != "__oneshipping_html__"):
                                    display_url = bk
                                break
                        domain = re.sub(
                            r'^https?://(www\.)?', '', display_url
                        ).split('/')[0]
                        cells += f"""
<td width="33%" bgcolor="{grp['bg']}"
    style="padding:10px;border-right:1px solid {grp['border']};">
  <table border="0" cellpadding="0" cellspacing="0" width="100%"><tr>
    <td width="28" valign="middle" align="center">
      <font size="3">{icon}</font>
    </td>
    <td valign="middle" style="padding-left:4px;">
      <font face="Microsoft JhengHei,Arial,sans-serif"
            size="2" color="#1e293b"><b>{self._esc(name)}</b></font><br>
      <font face="Arial,sans-serif" size="1" color="#64748b">{domain}</font>
    </td>
  </tr></table>
</td>"""
                rows_html += f"""
<tr>{cells}</tr>
<tr><td colspan="3" bgcolor="{grp['border']}" height="1"></td></tr>"""

            groups_html += f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:16px;border:1px solid {grp['border']};">
  <tr>
    <td colspan="3" bgcolor="{grp['color']}" style="padding:10px 16px;">
      <font face="Microsoft JhengHei,Arial,sans-serif"
            size="3" color="#ffffff">
        <b>{grp['icon']}&nbsp;{grp['title']}&nbsp;({len(sources)} 個)</b>
      </font>
    </td>
  </tr>
  {rows_html}
</table>"""
        return groups_html

    # ─────────────────────────────────────────────────────────
    # 元件 5：命中來源統計列
    # ★ v2.1：移除航商特殊標記（無航商 RSS 來源）
    # ─────────────────────────────────────────────────────────
    def render_hit_rows(self, all_news: list) -> str:
        source_stats: dict = {}
        for item in all_news:
            key = f"{item['source_icon']} {item['source_name']}"
            source_stats[key] = source_stats.get(key, 0) + 1

        if not source_stats:
            return (
                '<tr><td colspan="2" style="padding:16px 18px;">'
                '<font face="Microsoft JhengHei,Arial,sans-serif" '
                'size="3" color="#94a3b8">本次無相關新聞</font></td></tr>'
            )

        rows = ""
        for s, c in sorted(source_stats.items(), key=lambda x: -x[1]):
            rows += (
                f'<tr>'
                f'<td bgcolor="#ffffff" '
                f'style="padding:12px 18px;border-bottom:1px solid #f1f5f9;">'
                f'<font face="Microsoft JhengHei,Arial,sans-serif" '
                f'size="3" color="#334155">{s}</font>'
                f'</td>'
                f'<td bgcolor="#ffffff" align="right" width="60" '
                f'style="padding:12px 18px;border-bottom:1px solid #f1f5f9;">'
                f'<table border="0" cellpadding="4" cellspacing="0" '
                f'bgcolor="#dbeafe">'
                f'<tr><td align="center" width="30">'
                f'<font face="Arial,sans-serif" size="3" '
                f'color="#1d4ed8"><b>{c}</b></font>'
                f'</td></tr></table>'
                f'</td></tr>'
            )
        return rows

    # ─────────────────────────────────────────────────────────
    # 主渲染：完整 HTML
    # ★ v2.1：移除 carrier_summary_row
    # ─────────────────────────────────────────────────────────
    def render_full_html(self, news_data: dict, run_time: datetime) -> str:
        cfg           = EmailConfig
        tpe_str       = run_time.astimezone(self._tz).strftime('%Y-%m-%d %H:%M')
        total_sources = len(self.rss_sources) + len(self.cnyes_sources)
        total_news    = len(news_data.get('all', []))

        cat_order = sorted(
            self.cats.keys(),
            key=lambda k: self.cats[k]['priority']
        )

        cat_sections = "".join(
            self.render_incident_section(k, news_data.get(k.lower(), []))
            for k in cat_order
        )
        stat_cells = "".join(
            self.render_stat_cell(k, len(news_data.get(k.lower(), [])))
            for k in cat_order
        )
        source_grid = self.render_source_grid()
        hit_rows    = self.render_hit_rows(news_data.get('all', []))

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<title>{cfg.EMAIL_TITLE}</title>
</head>
<body bgcolor="#f1f5f9" style="margin:0;padding:0;">
<table width="100%" border="0" cellpadding="20" cellspacing="0" bgcolor="#f1f5f9">
<tr><td align="center" valign="top">
<table width="{cfg.EMAIL_WIDTH}" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#ffffff" style="border:1px solid #cbd5e1;">

  <!-- ▌標題列 -->
  <tr>
    <td bgcolor="#f8fafc" style="padding:24px;border-bottom:1px solid #e2e8f0;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif"
                size="5" color="#0f172a">
            <b>{cfg.EMAIL_TITLE}</b>
          </font><br>
          <font face="Microsoft JhengHei,Arial,sans-serif"
                size="2" color="#64748b">
            {cfg.EMAIL_SUBTITLE}
          </font><br>
          <font face="Microsoft JhengHei,Arial,sans-serif"
                size="2" color="#AE3A16">
            <b>{cfg.EMAIL_BRANDING}</b>
          </font>
        </td>
        <td align="right" valign="middle">
          <font face="Arial,sans-serif" size="2" color="#64748b">
            <b>最後更新:&nbsp;{tpe_str}&nbsp;({cfg.DISPLAY_TZ_NAME})</b>
          </font><br><br>
          <table border="0" cellpadding="6" cellspacing="0" bgcolor="#e2e8f0"><tr><td>
            <font face="Arial,sans-serif" size="2" color="#334155">
              <b>蒐集來源&nbsp;{total_sources}&nbsp;個</b>
            </font>
          </td></tr></table>
        </td>
      </tr></table>
    </td>
  </tr>

  <!-- ▌統計列 -->
  <tr><td style="padding:0;border-bottom:1px solid #cbd5e1;">
    <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
      <td align="center" bgcolor="#0f172a"
          style="padding:16px 6px;width:12%;border-right:1px solid #ffffff;">
        <font face="Arial,sans-serif" size="6" color="#ffffff">
          <b>{total_news}</b>
        </font><br><br>
        <font face="Arial,sans-serif" size="3" color="#ffffff">📰</font><br>
        <font face="Microsoft JhengHei,Arial,sans-serif"
              size="3" color="#94a3b8"><b>本次總計</b></font>
      </td>
      {stat_cells}
    </tr></table>
  </td></tr>

  <!-- ▌新聞內容 -->
  <tr><td bgcolor="#ffffff" style="padding:24px 24px 8px 24px;">
    {cat_sections}
  </td></tr>

  <!-- ▌命中來源統計 -->
  <tr><td bgcolor="#ffffff" style="padding:0 24px 24px 24px;">
    <table width="100%" border="0" cellpadding="0" cellspacing="0"
           style="border:1px solid #e2e8f0;">
      <tr>
        <td bgcolor="#f8fafc"
            style="padding:14px 18px;border-bottom:1px solid #cbd5e1;">
          <font face="Microsoft JhengHei,Arial,sans-serif"
                size="3" color="#0f172a">
            <b>📊&nbsp;本次新聞來源統計</b>
          </font>
        </td>
      </tr>
      <tr><td>
        <table width="100%" border="0" cellpadding="0" cellspacing="0">
          {hit_rows}
        </table>
      </td></tr>
    </table>
  </td></tr>

  <!-- ▌來源清單 -->
  <tr><td bgcolor="#ffffff" style="padding:0 24px 24px 24px;">
    <table width="100%" border="0" cellpadding="0" cellspacing="0"
           style="border:1px solid #e2e8f0;">
      <tr>
        <td bgcolor="#f8fafc"
            style="padding:14px 18px;border-bottom:1px solid #cbd5e1;">
          <font face="Microsoft JhengHei,Arial,sans-serif"
                size="3" color="#0f172a">
            <b>📡&nbsp;新聞來源清單</b>
          </font>
          &nbsp;&nbsp;
          <font face="Arial,sans-serif" size="2" color="#64748b">
            共&nbsp;{total_sources}&nbsp;個&nbsp;·&nbsp;RSS&nbsp;+&nbsp;JSON&nbsp;API
          </font>
        </td>
      </tr>
      <tr><td bgcolor="#ffffff" style="padding:16px 16px 0 16px;">
        {source_grid}
      </td></tr>
    </table>
  </td></tr>

  <!-- ▌頁尾 -->
  <tr>
    <td bgcolor="#f8fafc" align="center"
        style="padding:24px 16px;border-top:1px solid #cbd5e1;">
      <font face="Microsoft JhengHei,Arial,sans-serif"
            size="2" color="#64748b">
        {cfg.FOOTER_LINE1}
      </font><br><br>
      <font face="Arial,sans-serif" size="2" color="#94a3b8">
        <b>{cfg.FOOTER_LINE2}</b>
      </font>
    </td>
  </tr>

</table>
</td></tr></table>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════
# SMTP 發送器
# ══════════════════════════════════════════════════════════════
class NewsEmailSender:
    """
    負責 SMTP 連線與發送。
    HTML 渲染委派給 EmailRenderer。
    """

    def __init__(self, incident_categories: dict,
                 rss_sources: list, cnyes_sources: list):
        cfg = EmailConfig
        self.mail_user    = cfg.MAIL_USER
        self.mail_pass    = cfg.MAIL_PASS
        self.target_email = cfg.TARGET_EMAIL
        self.smtp_server  = cfg.SMTP_SERVER
        self.smtp_port    = cfg.SMTP_PORT
        self.enabled      = all([self.mail_user, self.mail_pass,
                                  self.target_email])

        self.renderer = EmailRenderer(
            incident_categories=incident_categories,
            rss_sources=rss_sources,
            cnyes_sources=cnyes_sources,
        )

        if not self.enabled:
            logger.error(
                "❌ Email 環境變數未設定："
                "MAIL_USER / MAIL_PASSWORD / TARGET_EMAIL"
            )
        else:
            logger.info(f"✅ Email → {self.target_email}")

    def send(self, news_data: dict, run_time: datetime) -> bool:
        if not self.enabled:
            return False
        if not news_data.get('all'):
            logger.info("ℹ️  無相關新聞，跳過發送")
            return False
        try:
            cfg      = EmailConfig
            tpe_time = run_time.astimezone(
                timezone(timedelta(hours=cfg.DISPLAY_TZ_OFFSET)))

            # ★ v2.1：移除 carrier_note，主旨格式簡化
            subject = (
                f"{cfg.SUBJECT_PREFIX} "
                f"({tpe_time.strftime('%m/%d %H:%M')}) "
                f"— {len(news_data['all'])} 則"
            )

            html_body = self.renderer.render_full_html(news_data, run_time)

            msg            = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From']    = f"{cfg.SENDER_NAME} <{self.mail_user}>"
            msg['To']      = self.target_email
            msg.attach(MIMEText(html_body, 'html', 'utf-8'))

            with smtplib.SMTP(self.smtp_server, self.smtp_port,
                              timeout=30) as server:
                server.starttls()
                server.login(self.mail_user, self.mail_pass)
                server.send_message(msg)

            logger.info("✅ Email 發送成功")
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error("❌ Gmail 認證失敗，請確認 App Password")
        except Exception as e:
            logger.error(f"❌ Email 發送失敗: {e}")
            traceback.print_exc()
        return False
