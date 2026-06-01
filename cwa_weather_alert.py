#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cwa_weather_alert.py  v3.1
台灣重要港口氣象警報監控系統 — CWA API + Email 通知
功能：
  1. 陸上強風特報  W-C0033-006（基隆/台北/台中/高雄）
  2. 高溫特報      W-C0033-005（港口所在縣市）
  3. 颱風警報      W-C0034-001（全台）
  4. 熱帶氣旋軌跡  W-C0034-005（JSON，含預測路徑）
  5. 海嘯警報      E-A0014-001
  6. 地震報告      E-A0015-001
  7. 海上強風特報  W-C0033-001（NEW v3.0 - 船舶航行安全）
  8. 濃霧特報      W-C0033-002（NEW v3.0 - 能見度警示）
通知：HTML 格式郵件
"""

import os
import smtplib
import logging
import traceback
import requests
import xml.etree.ElementTree as ET
import time
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib3
from dotenv import load_dotenv

# ── SSL 設定 ──────────────────────────────────────────────────
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()
VERIFY_SSL: bool = os.environ.get("VERIFY_SSL", "true").lower() == "true"

# ── Logger ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("cwa_alert.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# Email 設定
# ══════════════════════════════════════════════════════════════
class EmailConfig:
    SMTP_SERVER:  str = "smtp.gmail.com"
    SMTP_PORT:    int = 587
    MAIL_USER:    str = os.environ.get("MAIL_USER",    "")
    MAIL_PASS:    str = os.environ.get("MAIL_PASS",    "")
    TARGET_EMAIL: str = os.environ.get("TARGET_EMAIL", "")

    SENDER_NAME:    str = "港口氣象警報系統"
    SUBJECT_PREFIX: str = "⚠️ CWA 港口氣象警報"

    EMAIL_TITLE:    str = "🌊 台灣港口氣象警報通報"
    EMAIL_SUBTITLE: str = "四大商港航行安全｜海上強風・濃霧・颱風路徑・海嘯・地震"
    EMAIL_BRANDING: str = "Present by Fleet Risk Management"
    FOOTER_LINE1:   str = "此內容為系統自動發送，請勿直接回覆。"
    FOOTER_LINE2:   str = "CWA Weather Alert System v3.1 · Powered by WHL Fleet Risk Management"

    EMAIL_WIDTH:       int = 720
    DISPLAY_TZ_OFFSET: int = 8
    DISPLAY_TZ_NAME:   str = "TPE"


# ══════════════════════════════════════════════════════════════
# CWA API 設定
# ══════════════════════════════════════════════════════════════
class CWAConfig:
    API_KEY:  str = os.environ.get("CWA_API_KEY", "")
    BASE_URL: str = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"

    PORTS: dict = {
        "基隆港": {
            "city": "基隆市",
            "districts": {"中正區": "1002801", "中山區": "1002802"}
        },
        "台北港": {
            "city": "新北市",
            "districts": {"八里區": "6500400"}
        },
        "台中港": {
            "city": "台中市",
            "districts": {"梧棲區": "6600200"}
        },
        "高雄港": {
            "city": "高雄市",
            "districts": {
                "鼓山區": "6400200",
                "前鎮區": "6400600",
                "旗津區": "6400700",
            }
        },
    }

    # 港口所在縣市（用於高溫特報篩選）
    PORT_CITIES: list = ["基隆市", "新北市", "台中市", "高雄市"]

    # 台灣周邊海域關鍵字（用於海上強風 / 濃霧篩選）
    MARINE_AREAS: list = [
        "台灣海峽", "台灣北部海面", "台灣東北部海面",
        "台灣東部海面", "台灣南部海面", "台灣西南部海面",
        "台灣西部海面", "巴士海峽", "東海", "南海北部",
        "彭佳嶼", "基隆", "台北", "台中", "高雄",
        "花蓮", "宜蘭", "澎湖", "金門", "馬祖",
    ]

    COLOR_MAP: dict = {
        "黃色": {"bg": "#fffbeb", "border": "#f59e0b", "badge": "#b45309", "emoji": "🟡"},
        "橙色": {"bg": "#fff7ed", "border": "#f97316", "badge": "#c2410c", "emoji": "🟠"},
        "紅色": {"bg": "#fef2f2", "border": "#ef4444", "badge": "#b91c1c", "emoji": "🔴"},
    }

    @classmethod
    def get_geocode_map(cls) -> dict:
        return {
            geocode: (port_name, district)
            for port_name, info in cls.PORTS.items()
            for district, geocode in info["districts"].items()
        }


# ══════════════════════════════════════════════════════════════
# CWA 資料抓取 v3.0
# ══════════════════════════════════════════════════════════════
class CWAFetcher:
    def __init__(self):
        self.base_url     = CWAConfig.BASE_URL
        self.cap_base_url = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi"
        self.api_key      = CWAConfig.API_KEY
        self.geocode_map  = CWAConfig.get_geocode_map()
        self.marine_areas = CWAConfig.MARINE_AREAS

    # ── 共用：解析 CAP XML ────────────────────────────────────
    def _parse_cap_xml(self, xml_text: str) -> list:
        alerts = []
        try:
            ns   = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
            root = ET.fromstring(xml_text)
            msg_type = root.findtext("cap:msgType", namespaces=ns, default="Alert")

            for info in root.findall("cap:info", ns):
                alert = {
                    "identifier":     root.findtext("cap:identifier", namespaces=ns, default=""),
                    "sent":           root.findtext("cap:sent",       namespaces=ns, default=""),
                    "msg_type":       msg_type,
                    "event":          info.findtext("cap:event",       namespaces=ns, default=""),
                    "headline":       info.findtext("cap:headline",    namespaces=ns, default=""),
                    "description":    info.findtext("cap:description", namespaces=ns, default=""),
                    "instruction":    info.findtext("cap:instruction", namespaces=ns, default=""),
                    "effective":      info.findtext("cap:effective",   namespaces=ns, default=""),
                    "onset":          info.findtext("cap:onset",       namespaces=ns, default=""),
                    "expires":        info.findtext("cap:expires",     namespaces=ns, default=""),
                    "web":            info.findtext("cap:web",         namespaces=ns, default="https://www.cwa.gov.tw"),
                    "alert_color":    "",
                    "severity_level": "",
                    "alert_title":    "",
                    "areas":          [],
                }
                for param in info.findall("cap:parameter", ns):
                    name  = param.findtext("cap:valueName", namespaces=ns, default="")
                    value = param.findtext("cap:value",     namespaces=ns, default="")
                    if name == "alert_color":
                        alert["alert_color"] = value
                    elif name == "severity_level":
                        alert["severity_level"] = value
                    elif name == "alert_title":
                        alert["alert_title"] = value

                for area in info.findall("cap:area", ns):
                    area_desc = area.findtext("cap:areaDesc", namespaces=ns, default="")
                    geocodes  = {}
                    for geocode in area.findall("cap:geocode", ns):
                        val_name = geocode.findtext("cap:valueName", namespaces=ns, default="")
                        val      = geocode.findtext("cap:value",     namespaces=ns, default="")
                        geocodes[val_name] = val
                    alert["areas"].append({
                        "areaDesc": area_desc,
                        "geocodes": geocodes,
                        "geocode":  geocodes.get("Taiwan_Geocode_103", ""),
                    })

                alerts.append(alert)
        except ET.ParseError as e:
            logger.error(f"CAP XML 解析失敗: {e}")
        return alerts

    # ── 共用：從 API（CAP 格式）讀取 ─────────────────────────
    def _fetch_cap(self, dataset_id: str, fallback_file: str = None) -> list:
        try:
            url  = f"{self.cap_base_url}/{dataset_id}"
            resp = requests.get(
                url,
                params={
                    "Authorization": self.api_key,
                    "downloadType":  "WEB",
                    "format":        "CAP",
                },
                timeout=15,
                verify=VERIFY_SSL
            )
            resp.raise_for_status()

            text = resp.text.strip()
            if not text.startswith("<?xml") and not text.startswith("<alert"):
                raise ValueError(f"非 XML 回應：{text[:120]}")

            logger.info(f"  ✓ {dataset_id} API 取得成功（{len(text)} bytes）")
            return self._parse_cap_xml(text)

        except Exception as e:
            logger.warning(f"{dataset_id} API 失敗: {e}")
            if fallback_file:
                try:
                    with open(fallback_file, encoding="utf-8") as f:
                        logger.info(f"  ✓ 使用本地備援檔案：{fallback_file}")
                        return self._parse_cap_xml(f.read())
                except FileNotFoundError:
                    logger.warning(f"  ✗ 本地備援檔案不存在：{fallback_file}")
            return []

    # ── 共用：從 API（JSON 格式）讀取 ────────────────────────
    def _fetch_json(self, dataset_id: str, fallback_file: str = None) -> dict:
        try:
            resp = requests.get(
                f"{self.base_url}/{dataset_id}",
                params={"Authorization": self.api_key},
                timeout=15,
                verify=VERIFY_SSL
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"{dataset_id} JSON API 失敗: {e}")
            if fallback_file:
                try:
                    import json
                    with open(fallback_file, encoding="utf-8") as f:
                        logger.info(f"  ✓ 使用本地備援檔案：{fallback_file}")
                        return json.load(f)
                except FileNotFoundError:
                    logger.warning(f"  ✗ 本地備援檔案不存在：{fallback_file}")
            return {}

    # ── 1. 陸上強風特報 W-C0033-006（CAP）───────────────────
    def fetch_wind_alerts_for_ports(self) -> list:
        logger.info("🔍 抓取陸上強風特報...")
        raw_alerts = self._fetch_cap("W-C0033-006", "W-C0033-006.cap")
        alerts = []
        for alert in raw_alerts:
            for area in alert.get("areas", []):
                geocode = area.get("geocode", "")
                if geocode in self.geocode_map:
                    port_name, district = self.geocode_map[geocode]
                    alerts.append({
                        **alert,
                        "port_name": port_name,
                        "district":  district,
                        "area_desc": area.get("areaDesc", ""),
                    })
        logger.info(f"  → 找到 {len(alerts)} 筆港口強風警報")
        return alerts

    # ── 2. 高溫特報 W-C0033-005（CAP）────────────────────────
    def fetch_heat_alerts_for_ports(self) -> list:
        logger.info("🔍 抓取高溫特報...")
        raw_alerts = self._fetch_cap("W-C0033-005", "W-C0033-005.cap")
        alerts = []
        port_cities = CWAConfig.PORT_CITIES

        for alert in raw_alerts:
            matched_areas = []
            for area in alert.get("areas", []):
                area_desc = area.get("areaDesc", "")
                for city in port_cities:
                    if city in area_desc or area_desc in city:
                        matched_areas.append(area_desc)
                        break
            if matched_areas:
                alerts.append({**alert, "matched_areas": matched_areas})

        logger.info(f"  → 找到 {len(alerts)} 筆港口縣市高溫警報")
        return alerts

    # ── 3. 颱風警報 W-C0034-001（CAP）────────────────────────
    def fetch_typhoon_alerts(self) -> list:
        logger.info("🔍 抓取颱風警報...")
        raw_alerts = self._fetch_cap("W-C0034-001", "W-C0034-001.cap")
        now = datetime.now(timezone.utc)

        alerts = []
        for a in raw_alerts:
            # 只保留含颱風字眼的
            if "颱風" not in a.get("event", "") and "颱風" not in a.get("headline", ""):
                continue

            # 排除解除通報（msg_type = Cancel 或 headline 含「解除」）
            if a.get("msg_type", "").lower() == "cancel":
                logger.info(f"  ↳ 跳過解除通報：{a.get('headline', '')}")
                continue
            if "解除" in a.get("headline", ""):
                logger.info(f"  ↳ 跳過解除通報：{a.get('headline', '')}")
                continue

            # 排除已過期的警報
            expires_str = a.get("expires", "")
            if expires_str:
                try:
                    expires_dt = datetime.fromisoformat(expires_str)
                    if expires_dt.tzinfo is None:
                        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
                    if expires_dt < now:
                        logger.info(f"  ↳ 跳過已過期警報（{expires_str}）：{a.get('headline', '')}")
                        continue
                except Exception:
                    pass  # 無法解析時間則保守保留

            alerts.append(a)

        logger.info(f"  → 找到 {len(alerts)} 筆有效颱風警報")
        return alerts


    # ── 4. 熱帶氣旋軌跡 W-C0034-005（JSON）──────────────────
    def fetch_tropical_cyclone_track(self) -> list:
        logger.info("🔍 抓取熱帶氣旋軌跡...")
        data = self._fetch_json("W-C0034-005", "W-C0034-005.json")
        if not data:
            logger.info("  → 無熱帶氣旋資料")
            return []
        try:
            cwa     = data.get("cwaopendata", {})
            dataset = cwa.get("Dataset", {})
            cyclone = dataset.get("TropicalCyclones", {}).get("TropicalCyclone", {})
            if not cyclone:
                logger.info("  → 目前無活躍熱帶氣旋")
                return []

            analysis_data = cyclone.get("AnalysisData", {})
            fixes = analysis_data.get("Fix", [])
            if not isinstance(fixes, list):
                fixes = [fixes]

            latest_fix = fixes[-1] if fixes else {}
            result = {
                "typhoon_name":     cyclone.get("TyphoonName", ""),
                "cwa_typhoon_name": cyclone.get("CwaTyphoonName", ""),
                "cwa_td_no":        cyclone.get("CwaTdNo", ""),
                "cwa_ty_no":        cyclone.get("CwaTyNo", ""),
                "year":             cyclone.get("Year", ""),
                "sent":             cwa.get("Sent", ""),
                "msg_type":         cwa.get("MsgType", ""),
                "datetime":         latest_fix.get("DateTime", ""),
                "longitude":        latest_fix.get("CoordinateLongitude", ""),
                "latitude":         latest_fix.get("CoordinateLatitude", ""),
                "max_wind":         latest_fix.get("MaxWindSpeed", ""),
                "max_gust":         latest_fix.get("MaxGustSpeed", ""),
                "pressure":         latest_fix.get("Pressure", ""),
                "moving_speed":     latest_fix.get("MovingSpeed", ""),
                "moving_direction": latest_fix.get("MovingDirection", ""),
                "total_fixes":      len(fixes),
            }
            logger.info(f"  → 颱風：{result['cwa_typhoon_name']}（{result['typhoon_name']}），共 {len(fixes)} 筆軌跡")
            return [result]
        except Exception as e:
            logger.error(f"熱帶氣旋資料解析失敗: {e}")
            return []

    # ── 5. 海嘯警報 E-A0014-001（JSON）──────────────────────
    def fetch_tsunami_alerts(self) -> list:
        logger.info("🔍 抓取海嘯警報...")
        data    = self._fetch_json("E-A0014-001")
        records = data.get("records", {})
        items   = records.get("tsunami", [])
        result  = items if isinstance(items, list) else [items]
        logger.info(f"  → 找到 {len(result)} 筆海嘯警報")
        return result

    # ── 6. 地震報告 E-A0015-001（JSON）──────────────────────
    def fetch_earthquake_reports(self) -> list:
        logger.info("🔍 抓取地震報告...")
        data    = self._fetch_json("E-A0015-001")
        records = data.get("records", {})
        items   = records.get("earthquake", [])
        result  = items if isinstance(items, list) else [items]
        logger.info(f"  → 找到 {len(result)} 筆地震報告")
        return result

    # ── 7. 海上強風特報 W-C0033-001（CAP）【NEW v3.0】────────
    def fetch_marine_wind_alerts(self) -> list:
        logger.info("🔍 抓取海上強風特報...")
        raw_alerts = self._fetch_cap("W-C0033-001", "W-C0033-001.cap")

        alerts = []
        for alert in raw_alerts:
            # 排除已解除的警報
            if alert.get("msg_type", "").lower() == "cancel":
                continue
            if "解除" in alert.get("headline", ""):
                continue

            # 收集所有受影響海域名稱
            area_names = [
                area.get("areaDesc", "")
                for area in alert.get("areas", [])
                if area.get("areaDesc", "")
            ]

            # 篩選與台灣周邊相關的海域
            matched_areas = [
                a for a in area_names
                if any(kw in a for kw in self.marine_areas)
            ]

            # 若有符合海域 或 headline 含台灣相關字眼，則納入
            headline = alert.get("headline", "")
            is_relevant = (
                matched_areas or
                any(kw in headline for kw in self.marine_areas) or
                "台灣" in headline or
                not area_names  # 若無地區資訊，保守納入
            )

            if is_relevant:
                alerts.append({
                    **alert,
                    "matched_areas": matched_areas if matched_areas else area_names,
                    "all_areas":     area_names,
                })

        logger.info(f"  → 找到 {len(alerts)} 筆海上強風警報")
        return alerts

    # ── 8. 濃霧特報 W-C0033-002（CAP）【NEW v3.0】────────────
    def fetch_fog_alerts(self) -> list:
        logger.info("🔍 抓取濃霧特報...")
        raw_alerts = self._fetch_cap("W-C0033-002", "W-C0033-002.cap")

        alerts = []
        for alert in raw_alerts:
            # 排除已解除的警報
            if alert.get("msg_type", "").lower() == "cancel":
                continue
            if "解除" in alert.get("headline", ""):
                continue

            # 收集受影響地區
            area_names = [
                area.get("areaDesc", "")
                for area in alert.get("areas", [])
                if area.get("areaDesc", "")
            ]

            # 篩選港口相關地區
            port_cities = CWAConfig.PORT_CITIES
            matched_port_areas = [
                a for a in area_names
                if any(city in a for city in port_cities)
            ]

            # 篩選海域相關地區
            matched_marine_areas = [
                a for a in area_names
                if any(kw in a for kw in self.marine_areas)
            ]

            matched = matched_port_areas + matched_marine_areas

            # 若有符合地區，或 headline/description 含港口/海域關鍵字，則納入
            headline = alert.get("headline", "")
            desc     = alert.get("description", "")
            is_relevant = (
                matched or
                any(city in headline or city in desc for city in port_cities) or
                any(kw in headline or kw in desc for kw in self.marine_areas) or
                not area_names
            )

            if is_relevant:
                alerts.append({
                    **alert,
                    "matched_areas": matched if matched else area_names,
                    "all_areas":     area_names,
                })

        logger.info(f"  → 找到 {len(alerts)} 筆濃霧警報")
        return alerts


# ══════════════════════════════════════════════════════════════
# HTML 渲染器 v3.0
# ══════════════════════════════════════════════════════════════
class AlertEmailRenderer:
    def __init__(self):
        self._tz = timezone(timedelta(hours=EmailConfig.DISPLAY_TZ_OFFSET))

    @staticmethod
    def _esc(text: str) -> str:
        return (str(text)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))

    def _fmt_time(self, iso_str: str) -> str:
        if not iso_str:
            return "—"
        try:
            dt = datetime.fromisoformat(iso_str)
            return dt.astimezone(self._tz).strftime("%m/%d %H:%M")
        except Exception:
            return iso_str

    # ── 強風特報卡片（陸上）──────────────────────────────────
    def _render_wind_card(self, alert: dict) -> str:
        color     = alert.get("alert_color", "黃色")
        cfg       = CWAConfig.COLOR_MAP.get(color, CWAConfig.COLOR_MAP["黃色"])
        emoji     = cfg["emoji"]
        border    = cfg["border"]
        bg        = cfg["bg"]
        badge_bg  = cfg["badge"]
        severity  = self._esc(alert.get("severity_level", ""))
        port      = self._esc(alert.get("port_name", ""))
        district  = self._esc(alert.get("district", ""))
        area_desc = self._esc(alert.get("area_desc", ""))
        headline  = self._esc(alert.get("headline", ""))
        desc      = self._esc(alert.get("description", ""))
        instr     = self._esc(alert.get("instruction", ""))
        onset     = self._fmt_time(alert.get("onset", ""))
        expires   = self._fmt_time(alert.get("expires", ""))
        web       = alert.get("web", "https://www.cwa.gov.tw/V8/C/P/Warning/W25.html")

        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#ffffff" style="margin-bottom:14px;border:1px solid {border};">
  <tr>
    <td width="5" bgcolor="{border}" style="padding:0;">&nbsp;</td>
    <td style="padding:16px 18px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
            <b>🚢 {port}</b>
          </font>&nbsp;
          <table border="0" cellpadding="0" cellspacing="0" style="display:inline-table;"><tr>
            <td bgcolor="{badge_bg}" style="padding:3px 10px;">
              <font face="Arial,sans-serif" size="2" color="#ffffff"><b>{emoji} {severity}</b></font>
            </td>
          </tr></table>
        </td>
        <td align="right" valign="middle">
          <font face="Arial,sans-serif" size="2" color="#94a3b8">
            📍 {area_desc}（{district}）
          </font>
        </td>
      </tr></table>
      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:10px;"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#0f172a"><b>{headline}</b></font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="10" cellspacing="0"
             bgcolor="{bg}" style="margin-top:10px;border-left:3px solid {border};"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">{desc}</font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="8" cellspacing="0"
             bgcolor="#f8fafc" style="margin-top:8px;"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
          ⚠️ <b>注意事項：</b>{instr}
        </font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:12px;"><tr>
        <td>
          <font face="Arial,sans-serif" size="2" color="#64748b">
            🕐 開始：<b>{onset}</b>&nbsp;&nbsp;|&nbsp;&nbsp;🕕 結束：<b>{expires}</b>
          </font>
        </td>
        <td align="right">
          <table border="0" cellpadding="6" cellspacing="0" bgcolor="{border}"><tr><td>
            <a href="{web}" target="_blank" style="text-decoration:none;">
              <font face="Arial,sans-serif" size="2" color="#ffffff"><b>氣象署詳情 &rarr;</b></font>
            </a>
          </td></tr></table>
        </td>
      </tr></table>
    </td>
  </tr>
</table>"""

    # ── 海上強風特報卡片【NEW v3.0】──────────────────────────
    def _render_marine_wind_card(self, alert: dict) -> str:
        color    = alert.get("alert_color", "橙色")
        cfg      = CWAConfig.COLOR_MAP.get(color, CWAConfig.COLOR_MAP["橙色"])
        emoji    = cfg["emoji"]
        border   = cfg["border"]
        bg       = cfg["bg"]
        badge_bg = cfg["badge"]
        severity = self._esc(alert.get("severity_level", ""))
        headline = self._esc(alert.get("headline", ""))
        desc     = self._esc(alert.get("description", ""))
        instr    = self._esc(alert.get("instruction", ""))
        onset    = self._fmt_time(alert.get("onset", ""))
        expires  = self._fmt_time(alert.get("expires", ""))
        areas    = "、".join(alert.get("matched_areas", []))
        web      = alert.get("web", "https://www.cwa.gov.tw/V8/C/P/Warning/W25.html")

        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#ffffff" style="margin-bottom:14px;border:1px solid {border};">
  <tr>
    <td width="5" bgcolor="{border}" style="padding:0;">&nbsp;</td>
    <td style="padding:16px 18px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
            <b>⚓ 海上強風特報</b>
          </font>&nbsp;
          <table border="0" cellpadding="0" cellspacing="0" style="display:inline-table;"><tr>
            <td bgcolor="{badge_bg}" style="padding:3px 10px;">
              <font face="Arial,sans-serif" size="2" color="#ffffff"><b>{emoji} {severity}</b></font>
            </td>
          </tr></table>
        </td>
        <td align="right" valign="middle">
          <font face="Arial,sans-serif" size="2" color="#94a3b8">🌊 航行安全警示</font>
        </td>
      </tr></table>
      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:10px;"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#0f172a"><b>{headline}</b></font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="8" cellspacing="0"
             bgcolor="#fff3cd" style="margin-top:8px;border-left:3px solid #f59e0b;"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#92400e">
          📍 <b>影響海域：</b>{areas if areas else "（詳見氣象署公告）"}
        </font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="10" cellspacing="0"
             bgcolor="{bg}" style="margin-top:8px;border-left:3px solid {border};"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">{desc}</font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="8" cellspacing="0"
             bgcolor="#f8fafc" style="margin-top:8px;"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
          ⚠️ <b>航行注意：</b>{instr if instr else "船舶應注意海上強風，採取適當安全措施。"}
        </font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:12px;"><tr>
        <td>
          <font face="Arial,sans-serif" size="2" color="#64748b">
            🕐 開始：<b>{onset}</b>&nbsp;&nbsp;|&nbsp;&nbsp;🕕 結束：<b>{expires}</b>
          </font>
        </td>
        <td align="right">
          <table border="0" cellpadding="6" cellspacing="0" bgcolor="{border}"><tr><td>
            <a href="{web}" target="_blank" style="text-decoration:none;">
              <font face="Arial,sans-serif" size="2" color="#ffffff"><b>氣象署詳情 &rarr;</b></font>
            </a>
          </td></tr></table>
        </td>
      </tr></table>
    </td>
  </tr>
</table>"""

    # ── 濃霧特報卡片【NEW v3.0】──────────────────────────────
    def _render_fog_card(self, alert: dict) -> str:
        color    = alert.get("alert_color", "黃色")
        cfg      = CWAConfig.COLOR_MAP.get(color, CWAConfig.COLOR_MAP["黃色"])
        emoji    = cfg["emoji"]
        border   = cfg["border"]
        bg       = cfg["bg"]
        badge_bg = cfg["badge"]
        severity = self._esc(alert.get("severity_level", ""))
        headline = self._esc(alert.get("headline", ""))
        desc     = self._esc(alert.get("description", ""))
        instr    = self._esc(alert.get("instruction", ""))
        onset    = self._fmt_time(alert.get("onset", ""))
        expires  = self._fmt_time(alert.get("expires", ""))
        areas    = "、".join(alert.get("matched_areas", []))
        web      = alert.get("web", "https://www.cwa.gov.tw/V8/C/P/Warning/W27.html")

        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#ffffff" style="margin-bottom:14px;border:1px solid {border};">
  <tr>
    <td width="5" bgcolor="{border}" style="padding:0;">&nbsp;</td>
    <td style="padding:16px 18px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
            <b>🌫️ 濃霧特報</b>
          </font>&nbsp;
          <table border="0" cellpadding="0" cellspacing="0" style="display:inline-table;"><tr>
            <td bgcolor="{badge_bg}" style="padding:3px 10px;">
              <font face="Arial,sans-serif" size="2" color="#ffffff"><b>{emoji} {severity}</b></font>
            </td>
          </tr></table>
        </td>
        <td align="right" valign="middle">
          <font face="Arial,sans-serif" size="2" color="#94a3b8">👁️ 能見度警示</font>
        </td>
      </tr></table>
      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:10px;"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#0f172a"><b>{headline}</b></font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="8" cellspacing="0"
             bgcolor="#e0e7ef" style="margin-top:8px;border-left:3px solid #64748b;"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#1e293b">
          📍 <b>影響地區：</b>{areas if areas else "（詳見氣象署公告）"}
        </font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="10" cellspacing="0"
             bgcolor="{bg}" style="margin-top:8px;border-left:3px solid {border};"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">{desc}</font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="8" cellspacing="0"
             bgcolor="#f0f4f8" style="margin-top:8px;"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
          🚢 <b>COLREGS 提醒：</b>
          能見度不良時，依 COLREGS Rule 19 行駛，降速並加強瞭望，適時鳴放霧號。<br>
          {f"⚠️ {instr}" if instr else ""}
        </font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:12px;"><tr>
        <td>
          <font face="Arial,sans-serif" size="2" color="#64748b">
            🕐 開始：<b>{onset}</b>&nbsp;&nbsp;|&nbsp;&nbsp;🕕 結束：<b>{expires}</b>
          </font>
        </td>
        <td align="right">
          <table border="0" cellpadding="6" cellspacing="0" bgcolor="{border}"><tr><td>
            <a href="{web}" target="_blank" style="text-decoration:none;">
              <font face="Arial,sans-serif" size="2" color="#ffffff"><b>氣象署詳情 &rarr;</b></font>
            </a>
          </td></tr></table>
        </td>
      </tr></table>
    </td>
  </tr>
</table>"""

    # ── 高溫特報卡片 ──────────────────────────────────────────
    def _render_heat_card(self, alert: dict) -> str:
        color    = alert.get("alert_color", "黃色")
        cfg      = CWAConfig.COLOR_MAP.get(color, CWAConfig.COLOR_MAP["黃色"])
        emoji    = cfg["emoji"]
        border   = cfg["border"]
        bg       = cfg["bg"]
        badge_bg = cfg["badge"]
        severity = self._esc(alert.get("severity_level", ""))
        headline = self._esc(alert.get("headline", ""))
        desc     = self._esc(alert.get("description", ""))
        instr    = self._esc(alert.get("instruction", ""))
        onset    = self._fmt_time(alert.get("onset", ""))
        expires  = self._fmt_time(alert.get("expires", ""))
        areas    = "、".join(alert.get("matched_areas", []))
        web      = alert.get("web", "https://www.cwa.gov.tw/V8/C/P/Warning/W29.html")

        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#ffffff" style="margin-bottom:14px;border:1px solid {border};">
  <tr>
    <td width="5" bgcolor="{border}" style="padding:0;">&nbsp;</td>
    <td style="padding:16px 18px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
            <b>🌡️ 高溫特報</b>
          </font>&nbsp;
          <table border="0" cellpadding="0" cellspacing="0" style="display:inline-table;"><tr>
            <td bgcolor="{badge_bg}" style="padding:3px 10px;">
              <font face="Arial,sans-serif" size="2" color="#ffffff"><b>{emoji} {severity}</b></font>
            </td>
          </tr></table>
        </td>
        <td align="right" valign="middle">
          <font face="Arial,sans-serif" size="2" color="#94a3b8">📍 {areas}</font>
        </td>
      </tr></table>
      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:10px;"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#0f172a"><b>{headline}</b></font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="10" cellspacing="0"
             bgcolor="{bg}" style="margin-top:10px;border-left:3px solid {border};"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">{desc}</font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="8" cellspacing="0"
             bgcolor="#f8fafc" style="margin-top:8px;"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
          ⚠️ <b>注意事項：</b>{instr}
        </font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:12px;"><tr>
        <td>
          <font face="Arial,sans-serif" size="2" color="#64748b">
            🕐 開始：<b>{onset}</b>&nbsp;&nbsp;|&nbsp;&nbsp;🕕 結束：<b>{expires}</b>
          </font>
        </td>
        <td align="right">
          <table border="0" cellpadding="6" cellspacing="0" bgcolor="{border}"><tr><td>
            <a href="{web}" target="_blank" style="text-decoration:none;">
              <font face="Arial,sans-serif" size="2" color="#ffffff"><b>氣象署詳情 &rarr;</b></font>
            </a>
          </td></tr></table>
        </td>
      </tr></table>
    </td>
  </tr>
</table>"""

    # ── 颱風警報卡片 ──────────────────────────────────────────
    def _render_typhoon_card(self, alert: dict) -> str:
        msg_type = alert.get("msg_type", "Alert")
        headline = self._esc(alert.get("headline", "颱風警報"))
        raw_desc = alert.get("description", "")
        try:
            desc_root = ET.fromstring(f"<root>{raw_desc}</root>")
            desc = self._esc(" ".join(desc_root.itertext()).strip())
        except Exception:
            desc = self._esc(str(raw_desc)[:500])

        onset   = self._fmt_time(alert.get("onset", ""))
        expires = self._fmt_time(alert.get("expires", ""))
        web     = alert.get("web", "https://www.cwa.gov.tw/V8/C/P/Warning/FIFOWS.html")

        is_cancel = msg_type == "Cancel" or "解除" in headline
        border = "#94a3b8" if is_cancel else "#ef4444"
        bg     = "#f8fafc" if is_cancel else "#fef2f2"
        badge  = "#64748b" if is_cancel else "#b91c1c"
        icon   = "✅ 解除颱風" if is_cancel else "🌀 颱風警報"

        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#ffffff" style="margin-bottom:14px;border:1px solid {border};">
  <tr>
    <td width="5" bgcolor="{border}" style="padding:0;">&nbsp;</td>
    <td style="padding:16px 18px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td>
          <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
            <b>{icon}</b>
          </font>
        </td>
        <td align="right">
          <table border="0" cellpadding="4" cellspacing="0" bgcolor="{badge}"><tr><td>
            <font face="Arial,sans-serif" size="2" color="#ffffff"><b>{headline}</b></font>
          </td></tr></table>
        </td>
      </tr></table>
      <table width="100%" border="0" cellpadding="10" cellspacing="0"
             bgcolor="{bg}" style="margin-top:10px;border-left:3px solid {border};"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">
          {desc[:400]}{"..." if len(desc) > 400 else ""}
        </font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:12px;"><tr>
        <td>
          <font face="Arial,sans-serif" size="2" color="#64748b">
            🕐 生效：<b>{onset}</b>&nbsp;&nbsp;|&nbsp;&nbsp;🕕 結束：<b>{expires}</b>
          </font>
        </td>
        <td align="right">
          <table border="0" cellpadding="6" cellspacing="0" bgcolor="{border}"><tr><td>
            <a href="{web}" target="_blank" style="text-decoration:none;">
              <font face="Arial,sans-serif" size="2" color="#ffffff"><b>颱風詳情 &rarr;</b></font>
            </a>
          </td></tr></table>
        </td>
      </tr></table>
    </td>
  </tr>
</table>"""

    # ── 熱帶氣旋軌跡卡片 ─────────────────────────────────────
    def _render_cyclone_track_card(self, track: dict) -> str:
        name     = self._esc(track.get("cwa_typhoon_name", ""))
        en_name  = self._esc(track.get("typhoon_name", ""))
        ty_no    = self._esc(track.get("cwa_ty_no", ""))
        lon      = self._esc(track.get("longitude", ""))
        lat      = self._esc(track.get("latitude", ""))
        max_wind = self._esc(track.get("max_wind", ""))
        max_gust = self._esc(track.get("max_gust", ""))
        pressure = self._esc(track.get("pressure", ""))
        mov_spd  = self._esc(track.get("moving_speed", ""))
        mov_dir  = self._esc(track.get("moving_direction", ""))
        dt       = self._fmt_time(track.get("datetime", ""))
        total    = track.get("total_fixes", 0)

        # 航安預防提示：以目前中心位置、風速與移動資訊做保守分級。
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except Exception:
            lat_f = lon_f = 0.0
        try:
            gust_f = float(max_gust)
        except Exception:
            gust_f = 0.0

        near_tw = 18 <= lat_f <= 28 and 118 <= lon_f <= 126
        if gust_f >= 51 or near_tw:
            risk_badge = "🔴 航行高度警戒"
            risk_bg = "#fef2f2"
            risk_color = "#b91c1c"
        elif gust_f >= 33:
            risk_badge = "🟠 航行警戒"
            risk_bg = "#fff7ed"
            risk_color = "#c2410c"
        else:
            risk_badge = "🟡 持續監控"
            risk_bg = "#fffbeb"
            risk_color = "#b45309"

        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#ffffff" style="margin-bottom:14px;border:1px solid #8b5cf6;">
  <tr>
    <td width="5" bgcolor="#8b5cf6" style="padding:0;">&nbsp;</td>
    <td style="padding:16px 18px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td>
          <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
            <b>🌀 熱帶氣旋軌跡與航安預警</b>
          </font>
        </td>
        <td align="right">
          <table border="0" cellpadding="5" cellspacing="0" bgcolor="#6d28d9"><tr><td>
            <font face="Arial,sans-serif" size="2" color="#ffffff">
              <b>第 {ty_no} 號 {name}（{en_name}）</b>
            </font>
          </td></tr></table>
        </td>
      </tr></table>
      <table width="100%" border="0" cellpadding="10" cellspacing="0"
             bgcolor="#f5f3ff" style="margin-top:10px;border-left:3px solid #8b5cf6;"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">
          🕐 <b>最新時間：</b>{dt}<br>
          📍 <b>中心位置：</b>北緯 {lat}° 東經 {lon}°<br>
          💨 <b>最大風速：</b>{max_wind} m/s（陣風 {max_gust} m/s）<br>
          🌡️ <b>中心氣壓：</b>{pressure} hPa<br>
          🧭 <b>移動：</b>{mov_dir} 方向，{mov_spd} km/h<br>
          📊 <b>軌跡資料筆數：</b>{total} 筆（含歷史與預測）
        </font>
      </td></tr></table>

      <table width="100%" border="0" cellpadding="10" cellspacing="0"
             bgcolor="{risk_bg}" style="margin-top:10px;border-left:4px solid {risk_color};"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="{risk_color}">
          <b>{risk_badge}</b>
        </font><br>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">
          <b>建議顯示與處置：</b><br>
          1. 於信件中固定顯示「中心位置、移動方向/速度、最大風速、陣風、中心氣壓」。<br>
          2. 若中心進入臺灣周邊海域或陣風達強烈等級，標示紅色航行高度警戒。<br>
          3. 對四大商港與近岸航線進行 24/48/72 小時趨勢追蹤，提醒避開迎風浪、湧浪與強陣風區。<br>
          4. 船舶端可同步檢查航路調整、ETA/ETD、甲板綁紮、靠離泊窗口與備援港。<br>
        </font>
      </td></tr></table>

      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:12px;"><tr>
        <td align="right">
          <table border="0" cellpadding="6" cellspacing="0" bgcolor="#8b5cf6"><tr><td>
            <a href="https://www.cwa.gov.tw/V8/C/P/Warning/FIFOWS.html"
               target="_blank" style="text-decoration:none;">
              <font face="Arial,sans-serif" size="2" color="#ffffff"><b>颱風路徑 &rarr;</b></font>
            </a>
          </td></tr></table>
        </td>
      </tr></table>
    </td>
  </tr>
</table>"""

    # ── 海嘯警報卡片 ──────────────────────────────────────────
    def _render_tsunami_card(self, item: dict) -> str:
        report_type    = self._esc(item.get("reportType", "海嘯資訊"))
        report_content = self._esc(item.get("reportContent", ""))
        origin_time    = self._esc(item.get("originTime", ""))
        magnitude      = self._esc(str(item.get("magnitudeValue", "")))
        location       = self._esc(item.get("location", ""))
        wave_height    = self._esc(str(item.get("waveHeight", "")))

        is_warning = "警報" in report_type
        border = "#ef4444" if is_warning else "#f97316"
        bg     = "#fef2f2" if is_warning else "#fff7ed"
        badge  = "#b91c1c" if is_warning else "#c2410c"
        icon   = "🌊🚨" if is_warning else "🌊⚠️"

        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#ffffff" style="margin-bottom:14px;border:1px solid {border};">
  <tr>
    <td width="5" bgcolor="{border}" style="padding:0;">&nbsp;</td>
    <td style="padding:16px 18px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td>
          <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
            <b>{icon} {report_type}</b>
          </font>
        </td>
        <td align="right">
          <table border="0" cellpadding="4" cellspacing="0" bgcolor="{badge}"><tr><td>
            <font face="Arial,sans-serif" size="2" color="#ffffff"><b>M {magnitude}</b></font>
          </td></tr></table>
        </td>
      </tr></table>
      <table width="100%" border="0" cellpadding="10" cellspacing="0"
             bgcolor="{bg}" style="margin-top:10px;border-left:3px solid {border};"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">
          📍 <b>位置：</b>{location}<br>
          🕐 <b>發生時間：</b>{origin_time}<br>
          🌊 <b>預估波高：</b>{wave_height} 公尺<br><br>
          {report_content}
        </font>
      </td></tr></table>
    </td>
  </tr>
</table>"""

    # ── 地震報告卡片 ──────────────────────────────────────────
    def _render_earthquake_card(self, item: dict) -> str:
        eq_info     = item.get("earthquakeInfo", {})
        origin_time = self._esc(eq_info.get("originTime", ""))
        epicenter   = eq_info.get("epiCenter", {})
        location    = self._esc(epicenter.get("location", ""))
        depth       = self._esc(str(eq_info.get("depth", {}).get("value", "")))
        magnitude   = self._esc(str(eq_info.get("magnitude", {}).get("magnitudeValue", "")))
        report_content = self._esc(item.get("reportContent", ""))
        report_url  = item.get("web", "https://www.cwa.gov.tw")

        try:
            mag_val = float(magnitude)
        except (ValueError, TypeError):
            mag_val = 0.0

        if mag_val >= 6.0:
            border, bg, badge = "#ef4444", "#fef2f2", "#b91c1c"
            icon = "🔴🌍"
        elif mag_val >= 5.0:
            border, bg, badge = "#f97316", "#fff7ed", "#c2410c"
            icon = "🟠🌍"
        else:
            border, bg, badge = "#f59e0b", "#fffbeb", "#b45309"
            icon = "🟡🌍"

        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#ffffff" style="margin-bottom:14px;border:1px solid {border};">
  <tr>
    <td width="5" bgcolor="{border}" style="padding:0;">&nbsp;</td>
    <td style="padding:16px 18px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td>
          <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
            <b>{icon} 地震報告</b>
          </font>
        </td>
        <td align="right">
          <table border="0" cellpadding="4" cellspacing="0" bgcolor="{badge}"><tr><td>
            <font face="Arial,sans-serif" size="2" color="#ffffff"><b>規模 M {magnitude}</b></font>
          </td></tr></table>
        </td>
      </tr></table>
      <table width="100%" border="0" cellpadding="10" cellspacing="0"
             bgcolor="{bg}" style="margin-top:10px;border-left:3px solid {border};"><tr><td>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">
          📍 <b>震央：</b>{location}<br>
          🕐 <b>發生時間：</b>{origin_time}<br>
          📏 <b>震源深度：</b>{depth} 公里<br><br>
          {report_content}
        </font>
      </td></tr></table>
      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:10px;"><tr>
        <td align="right">
          <table border="0" cellpadding="6" cellspacing="0" bgcolor="{border}"><tr><td>
            <a href="{report_url}" target="_blank" style="text-decoration:none;">
              <font face="Arial,sans-serif" size="2" color="#ffffff"><b>地震詳情 &rarr;</b></font>
            </a>
          </td></tr></table>
        </td>
      </tr></table>
    </td>
  </tr>
</table>"""

    # ── 分類區塊 ──────────────────────────────────────────────
    def _render_section(self, title: str, icon: str, color: str,
                        bg: str, cards_html: str, count: int) -> str:
        darker = {
            "#f59e0b": "#b45309",
            "#ef4444": "#b91c1c",
            "#3b82f6": "#1d4ed8",
            "#f97316": "#c2410c",
            "#8b5cf6": "#6d28d9",
            "#94a3b8": "#64748b",
            "#0ea5e9": "#0369a1",
            "#64748b": "#334155",
        }.get(color, "#334155")

        if count == 0:
            return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:10px;border:1px solid #e2e8f0;">
  <tr>
    <td width="5" bgcolor="{color}">&nbsp;</td>
    <td bgcolor="#ffffff" style="padding:12px 16px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td><font face="Microsoft JhengHei,Arial,sans-serif"
                  size="3" color="{color}"><b>{icon}&nbsp;{title}</b></font></td>
        <td align="right"><font face="Microsoft JhengHei,Arial,sans-serif"
                                size="2" color="#94a3b8">本次無相關警報</font></td>
      </tr></table>
    </td>
  </tr>
</table>"""

        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:20px;border:1px solid #e2e8f0;">
  <tr>
    <td bgcolor="{color}" style="padding:12px 18px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td><font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#ffffff">
          <b>{icon}&nbsp;{title}</b>
        </font></td>
        <td align="right" width="60">
          <table border="0" cellpadding="6" cellspacing="0" bgcolor="{darker}"><tr>
            <td align="center">
              <font face="Arial,sans-serif" size="2" color="#ffffff"><b>{count} 則</b></font>
            </td>
          </tr></table>
        </td>
      </tr></table>
    </td>
  </tr>
  <tr>
    <td bgcolor="{bg}" style="padding:16px 16px 2px 16px;">
      {cards_html}
    </td>
  </tr>
</table>"""

    # ── 統計格 ────────────────────────────────────────────────
    def _render_stat_cell(self, icon: str, label: str, count: int, color: str) -> str:
        if count > 0:
            return f"""
<td align="center" bgcolor="{color}"
    style="padding:12px 4px;width:12%;border-right:1px solid #ffffff;">
  <font face="Arial,sans-serif" size="5" color="#ffffff"><b>{count}</b></font><br><br>
  <font face="Arial,sans-serif" size="3" color="#ffffff">{icon}</font><br>
  <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#ffffff">{label}</font>
</td>"""
        return f"""
<td align="center" bgcolor="#f8fafc"
    style="padding:12px 4px;width:12%;border-right:1px solid #e2e8f0;">
  <font face="Arial,sans-serif" size="5" color="#cbd5e1"><b>0</b></font><br><br>
  <font face="Arial,sans-serif" size="3" color="#cbd5e1">{icon}</font><br>
  <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">{label}</font>
</td>"""

    # ── 完整 HTML ─────────────────────────────────────────────
    def render_full_html(self, alert_data: dict, run_time: datetime) -> str:
        cfg     = EmailConfig
        tpe_str = run_time.astimezone(
            timezone(timedelta(hours=cfg.DISPLAY_TZ_OFFSET))
        ).strftime("%Y-%m-%d %H:%M")

        wind_alerts         = alert_data.get("wind",         [])
        heat_alerts         = alert_data.get("heat",         [])
        typhoon_alerts      = alert_data.get("typhoon",      [])
        cyclone_tracks      = alert_data.get("cyclone_track",[])
        tsunami_list        = alert_data.get("tsunami",      [])
        eq_list             = alert_data.get("earthquake",   [])
        marine_wind_alerts  = alert_data.get("marine_wind",  [])   # NEW v3.0
        fog_alerts          = alert_data.get("fog",          [])   # NEW v3.0

        total = (len(wind_alerts) + len(heat_alerts) + len(typhoon_alerts) +
                 len(cyclone_tracks) + len(tsunami_list) + len(eq_list) +
                 len(marine_wind_alerts) + len(fog_alerts))

        # ── 各區塊 HTML ───────────────────────────────────────
        marine_wind_section = self._render_section(
            "海上強風特報（航行安全）", "⚓", "#0ea5e9", "#f0f9ff",
            "".join(self._render_marine_wind_card(a) for a in marine_wind_alerts),
            len(marine_wind_alerts)
        )
        fog_section = self._render_section(
            "濃霧特報（能見度警示）", "🌫️", "#64748b", "#f1f5f9",
            "".join(self._render_fog_card(a) for a in fog_alerts),
            len(fog_alerts)
        )
        wind_section = self._render_section(
            "陸上強風特報（港口）", "🌬️", "#f59e0b", "#fffbeb",
            "".join(self._render_wind_card(a) for a in wind_alerts),
            len(wind_alerts)
        )
        heat_section = self._render_section(
            "高溫特報（港口縣市）", "🌡️", "#f97316", "#fff7ed",
            "".join(self._render_heat_card(a) for a in heat_alerts),
            len(heat_alerts)
        )
        typhoon_section = self._render_section(
            "颱風警報", "🌀", "#ef4444", "#fef2f2",
            "".join(self._render_typhoon_card(a) for a in typhoon_alerts),
            len(typhoon_alerts)
        )
        cyclone_section = self._render_section(
            "熱帶氣旋軌跡", "🗺️", "#8b5cf6", "#f5f3ff",
            "".join(self._render_cyclone_track_card(t) for t in cyclone_tracks),
            len(cyclone_tracks)
        )
        tsunami_section = self._render_section(
            "海嘯警報", "🌊", "#ef4444", "#fef2f2",
            "".join(self._render_tsunami_card(t) for t in tsunami_list),
            len(tsunami_list)
        )
        eq_section = self._render_section(
            "地震報告", "🌍", "#3b82f6", "#eff6ff",
            "".join(self._render_earthquake_card(e) for e in eq_list),
            len(eq_list)
        )

        # ── 統計列（8 格）────────────────────────────────────
        stat_cells = (
            self._render_stat_cell("⚓", "海上強風", len(marine_wind_alerts), "#0ea5e9") +
            self._render_stat_cell("🌫️", "濃霧特報",  len(fog_alerts),         "#64748b") +
            self._render_stat_cell("🌬️", "陸上強風",  len(wind_alerts),        "#f59e0b") +
            self._render_stat_cell("🌡️", "高溫特報",  len(heat_alerts),        "#f97316") +
            self._render_stat_cell("🌀", "颱風警報",  len(typhoon_alerts),     "#ef4444") +
            self._render_stat_cell("🌊", "海嘯警報",  len(tsunami_list),       "#ef4444") +
            self._render_stat_cell("🌍", "地震報告",  len(eq_list),            "#3b82f6")
        )

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{cfg.EMAIL_TITLE}</title></head>
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
          <font face="Microsoft JhengHei,Arial,sans-serif" size="5" color="#0f172a">
            <b>{cfg.EMAIL_TITLE}</b>
          </font><br>
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
            {cfg.EMAIL_SUBTITLE}
          </font><br>
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#AE3A16">
            <b>{cfg.EMAIL_BRANDING}</b>
          </font>
        </td>
        <td align="right" valign="middle">
          <font face="Arial,sans-serif" size="2" color="#64748b">
            <b>更新時間：{tpe_str} ({cfg.DISPLAY_TZ_NAME})</b>
          </font><br><br>
          <table border="0" cellpadding="6" cellspacing="0" bgcolor="#e2e8f0"><tr><td>
            <font face="Arial,sans-serif" size="2" color="#334155">
              <b>資料來源：中央氣象署</b>
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
          style="padding:12px 6px;width:16%;border-right:1px solid #ffffff;">
        <font face="Arial,sans-serif" size="5" color="#ffffff"><b>{total}</b></font><br><br>
        <font face="Arial,sans-serif" size="3" color="#ffffff">⚠️</font><br>
        <font face="Microsoft JhengHei,Arial,sans-serif"
              size="3" color="#94a3b8"><b>本次總計</b></font>
      </td>
      {stat_cells}
    </tr></table>
  </td></tr>

  <!-- ▌警報內容（優先順序：海上強風 > 濃霧 > 颱風 > 陸上強風 > 高溫 > 氣旋 > 海嘯 > 地震）-->
  <tr><td bgcolor="#ffffff" style="padding:24px 24px 8px 24px;">
    {marine_wind_section}
    {fog_section}
    {typhoon_section}
    {wind_section}
    {heat_section}
    {cyclone_section}
    {tsunami_section}
    {eq_section}
  </td></tr>

  <!-- ▌頁尾 -->
  <tr>
    <td bgcolor="#f8fafc" align="center"
        style="padding:24px 16px;border-top:1px solid #cbd5e1;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
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
# Email 發送器
# ══════════════════════════════════════════════════════════════
class AlertEmailSender:
    def __init__(self):
        cfg = EmailConfig
        self.mail_user    = cfg.MAIL_USER
        self.mail_pass    = cfg.MAIL_PASS
        self.target_email = cfg.TARGET_EMAIL
        self.smtp_server  = cfg.SMTP_SERVER
        self.smtp_port    = cfg.SMTP_PORT
        self.enabled      = all([self.mail_user, self.mail_pass, self.target_email])
        self.renderer     = AlertEmailRenderer()

        if not self.enabled:
            logger.warning("⚠️ Email 設定未填寫，將僅輸出至 console")
        else:
            logger.info(f"✅ Email 設定完成 → {self.target_email}")

    def send(self, alert_data: dict, run_time: datetime) -> bool:
        total = sum(len(v) for v in alert_data.values())

        tpe_str = run_time.astimezone(
            timezone(timedelta(hours=8))
        ).strftime("%Y-%m-%d %H:%M")

        # ── Console 輸出摘要 ──────────────────────────────────
        print(f"\n{'═'*60}")
        print(f"  ⚠️  CWA 港口氣象警報 v3.0  |  {tpe_str}")
        print(f"{'═'*60}")
        print(f"  ⚓  海上強風特報：{len(alert_data.get('marine_wind', []))} 筆")
        print(f"  🌫️  濃霧特報：    {len(alert_data.get('fog',         []))} 筆")
        print(f"  🌬️  陸上強風特報：{len(alert_data.get('wind',        []))} 筆")
        print(f"  🌡️  高溫特報：    {len(alert_data.get('heat',        []))} 筆")
        print(f"  🌀  颱風警報：    {len(alert_data.get('typhoon',     []))} 筆")
        print(f"  🗺️  氣旋軌跡：    {len(alert_data.get('cyclone_track',[]))} 筆")
        print(f"  🌊  海嘯警報：    {len(alert_data.get('tsunami',     []))} 筆")
        print(f"  🌍  地震報告：    {len(alert_data.get('earthquake',  []))} 筆")
        print(f"{'═'*60}\n")

        if total == 0:
            logger.info("ℹ️  無警報，跳過 Email 發送")
            return False

        if not self.enabled:
            logger.warning("ℹ️  Email 未設定，跳過發送")
            return False

        try:
            cfg      = EmailConfig
            tpe_time = run_time.astimezone(
                timezone(timedelta(hours=cfg.DISPLAY_TZ_OFFSET)))

            # ── 主旨等級判斷 ──────────────────────────────────
            wind_alerts        = alert_data.get("wind",        [])
            marine_wind_alerts = alert_data.get("marine_wind", [])
            typhoon_alerts     = alert_data.get("typhoon",     [])
            fog_alerts         = alert_data.get("fog",         [])

            has_red = any(
                "紅" in a.get("alert_color", "")
                for a in wind_alerts + marine_wind_alerts
            )
            has_orange = any(
                "橙" in a.get("alert_color", "")
                for a in wind_alerts + marine_wind_alerts
            )
            has_fog = len(fog_alerts) > 0
            has_tsunami = len(alert_data.get("tsunami", [])) > 0
            has_typhoon = len(typhoon_alerts) > 0
            has_eq_major = any(
                float(e.get("earthquakeInfo", {})
                       .get("magnitude", {})
                       .get("magnitudeValue", 0) or 0) >= 6.0
                for e in alert_data.get("earthquake", [])
            )
            has_marine_wind = len(marine_wind_alerts) > 0

            if has_red or has_tsunami or has_typhoon or has_eq_major:
                prefix = "🔴 緊急警報"
            elif has_orange or has_marine_wind:
                prefix = "🟠 警戒通知"
            elif has_fog:
                prefix = "🌫️ 能見度警示"
            else:
                prefix = "🟡 注意通知"

            subject = (
                f"{prefix} | {cfg.SUBJECT_PREFIX} "
                f"({tpe_time.strftime('%m/%d %H:%M')}) "
                f"— 共 {total} 則"
            )

            html_body = self.renderer.render_full_html(alert_data, run_time)

            msg            = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = f"{cfg.SENDER_NAME} <{self.mail_user}>"
            msg["To"]      = self.target_email
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.mail_user, self.mail_pass)
                server.send_message(msg)

            logger.info(f"✅ Email 發送成功：{subject}")
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error("❌ Gmail 認證失敗，請確認 App Password 是否正確")
        except Exception as e:
            logger.error(f"❌ Email 發送失敗: {e}")
            traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════════════
def run_once() -> dict:
    fetcher = CWAFetcher()
    sender  = AlertEmailSender()
    now     = datetime.now(timezone.utc)

    logger.info("=" * 55)
    logger.info("  CWA 港口氣象警報系統 v3.0 啟動")
    logger.info("=" * 55)

    alert_data = {
        "marine_wind":   fetcher.fetch_marine_wind_alerts(),    # ⭐ NEW v3.0
        "fog":           fetcher.fetch_fog_alerts(),            # ⭐ NEW v3.0
        "wind":          fetcher.fetch_wind_alerts_for_ports(),
        "heat":          fetcher.fetch_heat_alerts_for_ports(),
        "typhoon":       fetcher.fetch_typhoon_alerts(),
        "cyclone_track": fetcher.fetch_tropical_cyclone_track(),
        "tsunami":       fetcher.fetch_tsunami_alerts(),
        "earthquake":    fetcher.fetch_earthquake_reports(),
    }

    sender.send(alert_data, now)
    return alert_data


def run_monitor(interval_minutes: int = 10):
    logger.info(f"🚀 啟動持續監控，每 {interval_minutes} 分鐘檢查一次")
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            logger.info("⛔ 使用者中斷")
            break
        except Exception as e:
            logger.error(f"未預期錯誤: {e}")
            traceback.print_exc()
        logger.info(f"⏳ 等待 {interval_minutes} 分鐘...")
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    if "--monitor" in args:
        idx      = args.index("--monitor")
        interval = int(args[idx + 1]) if idx + 1 < len(args) else 10
        run_monitor(interval)
    else:
        run_once()

