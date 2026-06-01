#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cwa_marine_forecast.py  v4.5.0
四大商港 7 日天氣預報通報系統 — CWA Marine API + Email 通知

v4.4.0 修正：
  - F-B0053-049 : 修正 ElementValue 為 dict 結構（如 {'MaxTemperature': '30'}），
                  正確對應中文 ElementName（最高溫度/最低溫度/天氣現象/風速/風向/24小時降雨機率/紫外線指數）
  - 潮汐 TARGET_TIDE_PORTS : 縮減為四大商港附近
  - 異常浪 _render_rogue_wave_risk : 加入四大商港附近地點過濾
  - 藍色公路 _render_blue_highway : 加入四大商港航段過濾
  - 新增 MAJOR_PORT_KEYWORDS 設定
"""

import os
import sys
import re
import json
import smtplib
import logging
import traceback
import requests
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib3
from dotenv import load_dotenv

# ── SSL ───────────────────────────────────────────────────────
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()
VERIFY_SSL: bool = os.environ.get("VERIFY_SSL", "true").lower() == "true"

# ── Logger ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("cwa_marine_forecast.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 設定
# ══════════════════════════════════════════════════════════════
class Config:
    # ── Email ─────────────────────────────────────────────────
    SMTP_SERVER:  str = "smtp.gmail.com"
    SMTP_PORT:    int = 587
    MAIL_USER:    str = os.environ.get("MAIL_USER",    "")
    MAIL_PASS:    str = os.environ.get("MAIL_PASS",    "")
    TARGET_EMAIL: str = os.environ.get("TARGET_EMAIL", "")

    SENDER_NAME:    str = "港口海象預報系統"
    SUBJECT_PREFIX: str = "CWA 海象預報通報"
    EMAIL_TITLE:    str = "🚢 四大商港 7 日天氣預報"
    EMAIL_SUBTITLE: str = "基隆港・台北港・台中港・高雄港｜港區天氣・降雨・風速・蒲福・UV"
    EMAIL_BRANDING: str = "Present by Fleet Risk Management"
    FOOTER_LINE1:   str = "此內容為系統自動發送，請勿直接回覆。"
    FOOTER_LINE2:   str = "CWA Major Port Weather System v4.5.0 · Powered by WHL Fleet Risk Management"
    EMAIL_WIDTH:       int = 880
    DISPLAY_TZ_OFFSET: int = 8
    DISPLAY_TZ_NAME:   str = "TPE"

    # ── CWA API 端點 ──────────────────────────────────────────
    CWA_API_KEY:  str = os.environ.get("CWA_API_KEY", "")
    BASE_URL:     str = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
    FILEAPI_URL:  str = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi"

    FILEAPI_DATASETS: set = {
        "F-B0083-006",
        "F-A0012-001",
        "F-A0037-001", "F-A0037-002", "F-A0037-003", "F-A0037-026",
        "F-B0053-049",
        "F-B0082-001",
        "O-B0076-001",
    }

    TARGET_PORTS: list = ["基隆", "台北", "台中", "高雄"]

    TARGET_SEA_AREAS: list = [
        "臺灣北部", "台灣北部", "臺灣東北部", "台灣東北部",]

    # ── 四大商港潮汐
    TARGET_TIDE_PORTS: list = [
        "基隆", "臺北港", "台北港", "臺中港", "台中港","高雄"]

    # ── 四大商港 7 日天氣
    TARGET_PORT_WEATHER: list = ["基隆", "台北港", "臺北港", "台中港", "臺中港", "高雄"]

    # ── 四大商港附近關鍵字（用於異常浪 & 藍色公路過濾）
    MAJOR_PORT_KEYWORDS: list = [
        # 基隆
        "基隆",
        # 台北港
        "台北港", "臺北港",
        # 台中港
        "台中港", "臺中港",
        # 高雄
        "高雄"
    ]

    BLUE_HIGHWAY_DATASETS: dict = {
        "F-A0037-001": "基隆 ⇄ 馬祖",
        "F-A0037-002": "台中 ⇄ 馬公",
        "F-A0037-003": "高雄 ⇄ 馬公",
        "F-A0037-026": "基隆 ⇄ 彭佳嶼",
    }

    TARGET_OBS_STATIONS: list = []

    WAVE_WARN_M:     float = 2.5
    WAVE_CAUTION_M:  float = 1.5
    WIND_WARN_MS:    float = 13.9
    WIND_CAUTION_MS: float = 8.0
    CURRENT_WARN_MS: float = 1.5
    BF_WARN:         int   = 7
    BF_CAUTION:      int   = 5


# ══════════════════════════════════════════════════════════════
# 共用工具函式
# ══════════════════════════════════════════════════════════════
def safe_list(val) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        return [val]
    return []


def safe_get(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v is not None and str(v) not in ("", "-99", "-999", "None"):
            return v
    return default


# ══════════════════════════════════════════════════════════════
# 資料抓取器
# ══════════════════════════════════════════════════════════════
class MarineFetcher:
    def __init__(self, debug: bool = False):
        self.api_key     = Config.CWA_API_KEY
        self.base_url    = Config.BASE_URL
        self.fileapi_url = Config.FILEAPI_URL
        self.debug       = debug

    def _fetch_fileapi(self, dataset_id: str, fallback_file: str = None) -> dict:
        step1_url = f"{self.fileapi_url}/{dataset_id}"
        params = {"Authorization": self.api_key, "downloadType": "WEB", "format": "JSON"}
        try:
            r1 = requests.get(step1_url, params=params, timeout=30, verify=VERIFY_SSL)
            r1.raise_for_status()
            meta = r1.json()
            if self.debug:
                logger.info(f"  [DEBUG] {dataset_id} fileapi Step1 keys: "
                            f"{list(meta.keys()) if isinstance(meta, dict) else type(meta).__name__}")
            if isinstance(meta, dict) and ("cwaopendata" in meta or "records" in meta):
                logger.info(f"  ✓ {dataset_id} fileapi 直接取得資料")
                return meta
            download_url = ""
            if isinstance(meta, dict):
                download_url = (meta.get("url") or meta.get("downloadUrl") or
                                meta.get("link") or (meta.get("result") or {}).get("url", ""))
            if not download_url:
                raise ValueError("No download URL")
            logger.info(f"  ↓ {dataset_id} 下載資料中...")
            r2 = requests.get(download_url, timeout=60, verify=VERIFY_SSL)
            r2.raise_for_status()
            logger.info(f"  ✓ {dataset_id} fileapi 取得成功")
            return r2.json()
        except Exception as e:
            logger.warning(f"{dataset_id} fileapi 失敗: {e}")
            if fallback_file:
                try:
                    with open(fallback_file, encoding="utf-8") as f:
                        logger.info(f"  ✓ 使用本地備援：{fallback_file}")
                        return json.load(f)
                except FileNotFoundError:
                    logger.warning(f"  ✗ 備援檔案不存在：{fallback_file}")
            return {}

    def _fetch_datastore(self, dataset_id: str, extra_params: dict = None,
                         fallback_file: str = None) -> dict:
        url    = f"{self.base_url}/{dataset_id}"
        params = {"Authorization": self.api_key}
        if extra_params:
            params.update(extra_params)
        try:
            resp = requests.get(url, params=params, timeout=30, verify=VERIFY_SSL)
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"  ✓ {dataset_id} datastore 取得成功")
            return data
        except Exception as e:
            logger.warning(f"{dataset_id} datastore 失敗: {e}")
            if fallback_file:
                try:
                    with open(fallback_file, encoding="utf-8") as f:
                        logger.info(f"  ✓ 使用本地備援：{fallback_file}")
                        return json.load(f)
                except FileNotFoundError:
                    logger.warning(f"  ✗ 備援檔案不存在：{fallback_file}")
            return {}

    def _fetch(self, dataset_id: str, extra_params: dict = None,
               fallback_file: str = None) -> dict:
        if dataset_id in Config.FILEAPI_DATASETS:
            return self._fetch_fileapi(dataset_id, fallback_file=fallback_file)
        return self._fetch_datastore(dataset_id, extra_params=extra_params,
                                     fallback_file=fallback_file)

    def _debug_structure(self, data: dict, label: str, depth: int = 3) -> None:
        def _walk(obj, prefix="", d=0):
            if d > depth:
                return
            if isinstance(obj, dict):
                for k, v in list(obj.items())[:8]:
                    vtype = type(v).__name__
                    vlen  = f"[{len(v)}]" if isinstance(v, (list, dict)) else f" = {repr(v)[:50]}"
                    logger.info(f"  [STRUCT] {prefix}{k}: {vtype}{vlen}")
                    _walk(v, prefix + "  ", d + 1)
            elif isinstance(obj, list) and obj:
                logger.info(f"  [STRUCT] {prefix}[list {len(obj)} 筆] [0]:")
                _walk(obj[0], prefix + "  ", d + 1)
        logger.info(f"  [STRUCT] ══ {label} ══")
        _walk(data)

    # ══════════════════════════════════════════════════════════
    # F-A0012-001 海面天氣預報
    # ══════════════════════════════════════════════════════════
    def fetch_marine_weather(self) -> dict:
        logger.info("🔍 抓取海面天氣預報 F-A0012-001...")
        raw    = self._fetch("F-A0012-001", fallback_file="F-A0012-001.json")
        result = {"dataset_id": "F-A0012-001", "title": "海面天氣預報",
                  "records": [], "raw": raw}
        if not raw:
            return result
        try:
            cwa       = raw.get("cwaopendata", {})
            dataset   = cwa.get("dataset") or cwa.get("Dataset") or {}
            sea_areas = []

            logger.info(f"  [DEBUG] F-A0012 dataset keys: "
                        f"{list(dataset.keys()) if isinstance(dataset, dict) else type(dataset)}")

            contents = dataset.get("contents") or dataset.get("Contents") or {}
            if isinstance(contents, dict):
                for content in safe_list(contents.get("content") or contents.get("Content")):
                    if not isinstance(content, dict):
                        continue
                    for key in ["seaArea", "SeaArea", "location", "Location"]:
                        val = content.get(key)
                        if val:
                            sea_areas.extend(safe_list(val))

            if not sea_areas:
                for key in ["seaArea", "SeaArea", "SeaAreaForecast", "location", "Location"]:
                    val = dataset.get(key)
                    if val:
                        sea_areas = safe_list(val)
                        break

            if not sea_areas and isinstance(dataset, dict):
                for k, v in dataset.items():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        first = v[0]
                        if any(key in first for key in
                               ["seaAreaName", "SeaAreaName", "locationName", "LocationName"]):
                            sea_areas = v
                            logger.info(f"  [DEBUG] F-A0012 路徑3: dataset['{k}'] ({len(v)}筆)")
                            break
                    elif isinstance(v, dict):
                        for k2, v2 in v.items():
                            if isinstance(v2, list) and v2 and isinstance(v2[0], dict):
                                first = v2[0]
                                if any(key in first for key in
                                       ["seaAreaName", "SeaAreaName", "locationName", "LocationName"]):
                                    sea_areas = v2
                                    logger.info(f"  [DEBUG] F-A0012 路徑3b: "
                                                f"dataset['{k}']['{k2}'] ({len(v2)}筆)")
                                    break
                        if sea_areas:
                            break

            if not sea_areas:
                records_node = raw.get("records", {})
                for outer in ["SeaAreaForecasts", "seaAreaForecasts"]:
                    node = records_node.get(outer, {})
                    if isinstance(node, dict):
                        for inner in ["SeaAreaForecast", "seaAreaForecast"]:
                            val = node.get(inner)
                            if val:
                                sea_areas = safe_list(val)
                                break
                    if sea_areas:
                        break
                if not sea_areas:
                    for key in ["location", "Location", "SeaAreaForecast"]:
                        val = records_node.get(key)
                        if val:
                            sea_areas = safe_list(val)
                            break

            logger.info(f"  [DEBUG] F-A0012 sea_areas: {len(sea_areas)}")

            parsed = []
            for area in sea_areas:
                if not isinstance(area, dict):
                    continue
                loc_name = (area.get("seaAreaName") or area.get("SeaAreaName") or
                            area.get("locationName") or area.get("LocationName") or
                            area.get("areaName", ""))
                if not any(kw in loc_name for kw in Config.TARGET_SEA_AREAS):
                    continue

                time_periods = []
                for outer in ["timePeriods", "TimePeriods", "Period", "period"]:
                    tp_raw = area.get(outer)
                    if tp_raw:
                        if isinstance(tp_raw, list):
                            time_periods = tp_raw
                        elif isinstance(tp_raw, dict):
                            for inner in ["timePeriod", "TimePeriod", "time", "Time"]:
                                val = tp_raw.get(inner)
                                if val:
                                    time_periods = safe_list(val)
                                    break
                        break
                if not time_periods:
                    val = area.get("time") or area.get("Time")
                    if val:
                        time_periods = safe_list(val)

                forecasts = []
                for tp in time_periods[:5]:
                    if not isinstance(tp, dict):
                        continue
                    we_raw = []
                    for outer in ["weatherElement", "WeatherElement",
                                  "weatherElements", "WeatherElements"]:
                        we_node = tp.get(outer)
                        if we_node:
                            if isinstance(we_node, list):
                                we_raw = we_node
                            elif isinstance(we_node, dict):
                                for inner in ["weatherElement", "WeatherElement"]:
                                    val = we_node.get(inner)
                                    if val:
                                        we_raw = safe_list(val)
                                        break
                            break

                    elements = {}
                    for we in we_raw:
                        if not isinstance(we, dict):
                            continue
                        ename = we.get("elementName") or we.get("ElementName") or ""
                        ev    = we.get("elementValue") or we.get("ElementValue") or ""
                        if isinstance(ev, list) and ev:
                            v0 = ev[0]
                            ev = (v0.get("value") or v0.get("Value", "")
                                  if isinstance(v0, dict) else str(v0))
                        elif isinstance(ev, dict):
                            ev = ev.get("value") or ev.get("Value", "")
                        elements[ename] = str(ev) if ev is not None else ""

                    forecasts.append({
                        "start_time":  (tp.get("startTime") or tp.get("StartTime") or
                                        tp.get("dataTime", "")),
                        "end_time":    tp.get("endTime") or tp.get("EndTime", ""),
                        "weather":     elements.get("Weather") or elements.get("天氣現象", ""),
                        "wind_dir":    (elements.get("WindDirectionDescription") or
                                        elements.get("風向描述", "")),
                        "beaufort":    (elements.get("BeaufortScaleDescription") or
                                        elements.get("蒲福風級描述", "")),
                        "wave_height": (elements.get("WaveHeightDescription") or
                                        elements.get("浪高描述", "")),
                        "wave_type":   (elements.get("WaveTypeDescription") or
                                        elements.get("浪型描述", "")),
                    })

                parsed.append({"location": loc_name, "forecasts": forecasts})

            result["records"] = parsed
            logger.info(f"  → 解析 {len(parsed)} 個海域天氣預報")
        except Exception as e:
            logger.error(f"海面天氣解析失敗: {e}")
            traceback.print_exc()
        return result

    # ══════════════════════════════════════════════════════════
    # F-A0021-001 鄉鎮潮汐預報（datastore）
    # ══════════════════════════════════════════════════════════
    def fetch_tide_forecast(self) -> dict:
        logger.info("🔍 抓取鄉鎮潮汐預報 F-A0021-001...")
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        raw   = self._fetch_datastore("F-A0021-001",
                                      extra_params={"timeFrom": today},
                                      fallback_file="F-A0021-001.json")
        result = {"dataset_id": "F-A0021-001", "title": "港口潮汐預報",
                  "records": [], "raw": raw}
        try:
            records_node = raw.get("records", {})
            tide_list = []
            for outer in ["TideForecasts", "tideForecast"]:
                node = records_node.get(outer)
                if node:
                    if isinstance(node, list):
                        tide_list = node
                    elif isinstance(node, dict):
                        for inner in ["TideForecast", "tideForecast"]:
                            val = node.get(inner)
                            if val:
                                tide_list = safe_list(val)
                                break
                    break

            if not tide_list:
                cwa     = raw.get("cwaopendata", {})
                dataset = cwa.get("dataset", cwa.get("Dataset", {}))
                for key in ["TideForecast", "tideForecast", "Location", "location"]:
                    val = dataset.get(key)
                    if val:
                        tide_list = safe_list(val)
                        break

            parsed = []
            for item in tide_list:
                if not isinstance(item, dict):
                    continue
                loc = item.get("Location") or item.get("location") or item
                if isinstance(loc, list):
                    loc = loc[0] if loc else {}
                if not isinstance(loc, dict):
                    continue

                loc_name = (loc.get("LocationName") or loc.get("locationName") or
                            item.get("LocationName") or item.get("locationName", ""))
                if not any(p in loc_name for p in Config.TARGET_TIDE_PORTS):
                    continue

                time_periods = loc.get("TimePeriods") or loc.get("timePeriods") or {}
                if isinstance(time_periods, dict):
                    daily_list = safe_list(
                        time_periods.get("Daily") or time_periods.get("daily"))
                elif isinstance(time_periods, list):
                    daily_list = time_periods
                else:
                    daily_list = []

                tides = []
                for day in daily_list[:3]:
                    if not isinstance(day, dict):
                        continue
                    tide_times = safe_list(day.get("Time") or day.get("time"))
                    highs, lows = [], []
                    for tt in tide_times:
                        if not isinstance(tt, dict):
                            continue
                        t_type = tt.get("TideType") or tt.get("tideType") or ""
                        t_dt   = str(tt.get("DateTime") or tt.get("dateTime") or "")
                        t_hhmm = t_dt[11:16] if len(t_dt) >= 16 else t_dt
                        th     = tt.get("TideHeights") or tt.get("tideHeights") or {}
                        t_h_cd = (th.get("AboveChartDatum") or
                                  th.get("aboveChartDatum") or
                                  tt.get("TideHeight", ""))
                        entry  = f"{t_hhmm} ({t_h_cd}cm)" if t_h_cd else t_hhmm
                        if t_type in ("H", "高潮", "滿潮"):
                            highs.append(entry)
                        elif t_type in ("L", "低潮", "乾潮"):
                            lows.append(entry)

                    tides.append({
                        "date":       day.get("Date")      or day.get("date", ""),
                        "lunar":      day.get("LunarDate") or day.get("lunarDate", ""),
                        "tide_range": day.get("TideRange") or day.get("tideRange", ""),
                        "highs":      highs,
                        "lows":       lows,
                    })

                if tides:
                    parsed.append({"location": loc_name, "tides": tides})

            result["records"] = parsed
            logger.info(f"  → 解析 {len(parsed)} 個港口潮汐預報")
        except Exception as e:
            logger.error(f"潮汐解析失敗: {e}")
            traceback.print_exc()
        return result

    # ══════════════════════════════════════════════════════════
    # F-A0037-xxx 藍色公路海氣象預報
    # 真實結構：扁平 list，每筆 = 一地點 × 一時間點
    # ══════════════════════════════════════════════════════════
    def fetch_blue_highway(self) -> dict:
        logger.info("🔍 抓取藍色公路海氣象預報 F-A0037-xxx...")
        result = {"dataset_id": "F-A0037-xxx", "title": "藍色公路海氣象預報",
                  "records": [], "meta": {}}
        parsed_routes = []

        for dataset_id, route_name in Config.BLUE_HIGHWAY_DATASETS.items():
            raw = self._fetch(dataset_id, fallback_file=f"{dataset_id}.json")
            if not raw:
                continue
            try:
                cwa     = raw.get("cwaopendata", {})
                issued  = cwa.get("sent") or cwa.get("Sent", "")
                dataset = cwa.get("dataset") or cwa.get("Dataset") or {}

                locations = []
                dataset_info = dataset.get("datasetInfo") or dataset.get("DatasetInfo") or {}
                if isinstance(dataset_info, dict):
                    for key in ["location", "Location"]:
                        val = dataset_info.get(key)
                        if val:
                            locations = safe_list(val)
                            break

                if not locations:
                    for key in ["location", "Location"]:
                        val = dataset.get(key)
                        if val:
                            locations = safe_list(val)
                            break

                if not locations:
                    records_node = raw.get("records", {})
                    for key in ["location", "Location"]:
                        val = records_node.get(key)
                        if val:
                            locations = safe_list(val)
                            break

                logger.info(f"  [DEBUG] {dataset_id} locations: {len(locations)}")
                if not locations:
                    logger.warning(f"  {dataset_id} 無 locations 資料")
                    continue

                if isinstance(locations[0], dict):
                    logger.info(f"  [DEBUG] {dataset_id} location[0] keys: "
                                f"{list(locations[0].keys())}")

                loc_groups = defaultdict(list)
                for loc in locations:
                    if not isinstance(loc, dict):
                        continue
                    code = (loc.get("LocationCode") or loc.get("locationCode") or
                            loc.get("stationCode") or loc.get("StationCode") or
                            loc.get("locationName") or loc.get("LocationName") or
                            "UNKNOWN")
                    loc_groups[code].append(loc)

                logger.info(f"  [DEBUG] {dataset_id} 分組後: {len(loc_groups)} 個地點")

                segments = []
                for code, items in loc_groups.items():
                    items_sorted = sorted(
                        items,
                        key=lambda x: (x.get("DateTime") or x.get("dateTime") or
                                       x.get("dataTime") or "")
                    )

                    forecasts = []
                    for item in items_sorted[:8]:
                        def _fv(item, *keys):
                            for k in keys:
                                v = item.get(k)
                                if v is not None and str(v) not in ("", "-99", "-999", "None"):
                                    return str(v)
                            return ""

                        forecasts.append({
                            "datetime":      _fv(item, "DateTime", "dateTime", "dataTime"),
                            "wave_height":   _fv(item, "SignificantWaveHeight",
                                                 "significantWaveHeight", "waveHeight"),
                            "wave_dir":      _fv(item, "WaveDirection", "waveDirection"),
                            "wave_period":   _fv(item, "WavePeriod", "wavePeriod"),
                            "beaufort":      _fv(item, "BeaufortScale", "beaufortScale"),
                            "wind_speed":    _fv(item, "WindSpeed", "windSpeed"),
                            "wind_dir":      _fv(item, "WindDirection", "windDirection"),
                            "current_speed": _fv(item, "OceanCurrentSpeed",
                                                 "oceanCurrentSpeed", "currentSpeed"),
                            "current_dir":   _fv(item, "OceanCurrentDirection",
                                                 "oceanCurrentDirection", "currentDirection"),
                        })

                    if forecasts:
                        segments.append({"location": code, "forecasts": forecasts})

                if segments:
                    parsed_routes.append({
                        "route_code": dataset_id,
                        "route_name": route_name,
                        "issued":     issued,
                        "segments":   segments,
                    })
                    logger.info(f"  → {dataset_id} ({route_name})：{len(segments)} 航段")
                else:
                    logger.warning(f"  {dataset_id} 解析 0 航段")

            except Exception as e:
                logger.warning(f"  {dataset_id} 解析失敗: {e}")
                traceback.print_exc()

        result["records"] = parsed_routes
        logger.info(f"  → 共解析 {len(parsed_routes)} 條藍色公路航線")
        return result

    # ══════════════════════════════════════════════════════════
    # F-B0053-049 港口 7 日天氣預報
    # 真實 ElementValue 結構：{'MaxTemperature': '30'} 等 dict
    # 真實 ElementName：中文（最高溫度/最低溫度/天氣現象/風速/風向/24小時降雨機率/紫外線指數）
    # ══════════════════════════════════════════════════════════
    def fetch_port_weather_7d(self) -> dict:
        logger.info("🔍 抓取港口遊憩地點 7 日天氣預報 F-B0053-049...")
        raw    = self._fetch("F-B0053-049", fallback_file="F-B0053-049.json")
        result = {"dataset_id": "F-B0053-049", "title": "港口 7 日天氣預報",
                  "records": [], "raw": raw}
        if not raw:
            return result
        try:
            cwa       = raw.get("cwaopendata", {})
            dataset   = cwa.get("dataset") or cwa.get("Dataset") or {}
            locations = []

            logger.info(f"  [DEBUG] F-B0053 dataset keys: "
                        f"{list(dataset.keys()) if isinstance(dataset, dict) else type(dataset)}")

            locs_node = dataset.get("Locations") or dataset.get("locations") or {}
            if isinstance(locs_node, dict):
                for key in ["Location", "location"]:
                    val = locs_node.get(key)
                    if val:
                        locations = safe_list(val)
                        break
            elif isinstance(locs_node, list):
                for node in locs_node:
                    if isinstance(node, dict):
                        for key in ["Location", "location"]:
                            val = node.get(key)
                            if val:
                                locations.extend(safe_list(val))

            if not locations and isinstance(dataset, dict):
                for k, v in dataset.items():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        first = v[0]
                        if any(key in first for key in ["locationName", "LocationName"]):
                            locations = v
                            break
                    elif isinstance(v, dict):
                        for k2, v2 in v.items():
                            if isinstance(v2, list) and v2 and isinstance(v2[0], dict):
                                first = v2[0]
                                if any(key in first for key in ["locationName", "LocationName"]):
                                    locations = v2
                                    break
                        if locations:
                            break

            logger.info(f"  [DEBUG] F-B0053 locations 共 {len(locations)} 筆")

            TARGET = Config.TARGET_PORT_WEATHER
            PORT_ALIASES = {
                "基隆港": ["基隆港", "基隆"],
                "台北港": ["台北港", "臺北港"],
                "台中港": ["台中港", "臺中港"],
                "高雄港": ["高雄港", "高雄"],
            }
            seen_ports = set()

            parsed = []
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                loc_name = (loc.get("LocationName") or loc.get("locationName") or "")
                canonical_port = ""
                for port_name, aliases in PORT_ALIASES.items():
                    if any(alias in loc_name for alias in aliases):
                        canonical_port = port_name
                        break
                if not canonical_port:
                    continue
                if canonical_port in seen_ports:
                    logger.info(f"  ↳ 跳過重複港口：{loc_name} → {canonical_port}")
                    continue
                seen_ports.add(canonical_port)

                elements_raw = safe_list(
                    loc.get("WeatherElement") or loc.get("weatherElement"))

                # ── 建立 elementName → time[] 對照表
                elem_map = {}
                for elem in elements_raw:
                    if not isinstance(elem, dict):
                        continue
                    ename = (elem.get("ElementName") or elem.get("elementName") or "")
                    etime = safe_list(elem.get("Time") or elem.get("time"))
                    if ename:
                        elem_map[ename] = etime

                logger.info(f"  [DEBUG] F-B0053 {loc_name} elem_map keys: "
                            f"{list(elem_map.keys())}")

                def _get_elem(*names):
                    for n in names:
                        if n in elem_map and elem_map[n]:
                            return elem_map[n]
                    return []

                # ── 對應真實中文 ElementName（已由 log 確認）
                max_t_list = _get_elem("最高溫度", "MaxT", "MaxTemperature")
                min_t_list = _get_elem("最低溫度", "MinT", "MinTemperature")
                wx_list    = _get_elem("天氣現象", "Wx", "WeatherDescription", "Weather")
                ws_list    = _get_elem("風速", "WS", "WindSpeed")
                wd_list    = _get_elem("風向", "WD", "WindDirection")
                pop_list   = _get_elem("24小時降雨機率", "PoP24h", "PoP12h", "PoP", "降雨機率")
                uvi_list   = _get_elem("紫外線指數", "UVI", "UVIndex")
                bf_list    = _get_elem("BeaufortScale", "蒲福風級", "Bf")

                def _val(lst, idx):
                    """
                    從 time[idx].ElementValue 取值
                    真實結構：ElementValue 是 dict，如 {'MaxTemperature': '30'}
                    直接取 dict 的第一個 value
                    """
                    try:
                        item = lst[idx]
                        if not isinstance(item, dict):
                            return "—"
                        ev = (item.get("ElementValue") or item.get("elementValue") or "")
                        # ── 真實結構：dict，如 {'MaxTemperature': '30'}
                        if isinstance(ev, dict):
                            v = next(iter(ev.values()), "")
                            return str(v) if str(v) not in ("", "None", "-99") else "—"
                        # ── list 結構（備用）
                        if isinstance(ev, list) and ev:
                            v = ev[0]
                            if isinstance(v, dict):
                                return (v.get("Value") or v.get("value") or
                                        next(iter(v.values()), "—"))
                            return str(v) if str(v) not in ("", "None") else "—"
                        # ── 純字串
                        return str(ev) if str(ev) not in ("", "None", "-99") else "—"
                    except (IndexError, AttributeError, TypeError):
                        return "—"

                def _time(lst, idx):
                    try:
                        item = lst[idx]
                        if isinstance(item, dict):
                            return (item.get("StartTime") or item.get("startTime") or
                                    item.get("DataTime") or item.get("dataTime") or "")
                        return ""
                    except (IndexError, TypeError):
                        return ""

                base_list = max_t_list or wx_list or ws_list or pop_list
                forecasts = []
                for i in range(min(7, len(base_list))):
                    forecasts.append({
                        "start_time": _time(base_list, i),
                        "max_temp":   _val(max_t_list, i),
                        "min_temp":   _val(min_t_list, i),
                        "weather":    _val(wx_list,    i),
                        "wind_speed": _val(ws_list,    i),
                        "wind_dir":   _val(wd_list,    i),
                        "pop":        _val(pop_list,   i),
                        "uvi":        _val(uvi_list,   i),
                        "beaufort":   _val(bf_list,    i),
                    })

                if forecasts:
                    parsed.append({"location": canonical_port, "source_location": loc_name, "forecasts": forecasts})

            result["records"] = parsed
            logger.info(f"  → 解析 {len(parsed)} 個港口 7 日天氣預報")
        except Exception as e:
            logger.error(f"港口 7 日天氣解析失敗: {e}")
            traceback.print_exc()
        return result

    # ══════════════════════════════════════════════════════════
    # F-B0083-006 海岸異常浪風險
    # ══════════════════════════════════════════════════════════
    def fetch_rogue_wave_risk(self) -> dict:
        logger.info("🔍 抓取海岸異常浪風險 F-B0083-006...")
        raw    = self._fetch("F-B0083-006", fallback_file="F-B0083-006.json")
        result = {"dataset_id": "F-B0083-006", "title": "海岸異常浪風險",
                  "records": [], "meta": {}, "raw": raw}
        try:
            cwa          = raw.get("cwaopendata", {})
            dataset      = cwa.get("dataset") or cwa.get("Dataset") or {}
            issued       = dataset.get("DateTime") or cwa.get("sent") or cwa.get("Sent", "")
            dataset_info = dataset.get("datasetInfo") or dataset.get("DatasetInfo") or {}
            description  = (dataset_info.get("datasetDescription") or
                            dataset_info.get("DatasetDescription", ""))
            result["meta"] = {"issued": issued, "description": description}

            locations = []
            contents = dataset.get("contents") or dataset.get("Contents") or {}
            if isinstance(contents, dict):
                for content in safe_list(contents.get("content") or contents.get("Content")):
                    if isinstance(content, dict):
                        for key in ["location", "Location"]:
                            val = content.get(key)
                            if val:
                                locations.extend(safe_list(val))

            if not locations:
                for key in ["location", "Location"]:
                    val = dataset.get(key)
                    if val:
                        locations = safe_list(val)
                        break

            if not locations:
                records_node = raw.get("records", {})
                for key in ["location", "Location"]:
                    val = records_node.get(key)
                    if val:
                        locations = safe_list(val)
                        break

            parsed = []
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                loc_name  = loc.get("locationName") or loc.get("LocationName", "")
                params    = safe_list(loc.get("parameter") or loc.get("Parameter"))
                param_map = {}
                for p in params:
                    if not isinstance(p, dict):
                        continue
                    pname = p.get("parameterName")  or p.get("ParameterName",  "")
                    pval  = p.get("parameterValue") or p.get("ParameterValue", "")
                    param_map[pname] = pval

                risk_level  = (param_map.get("RogueWaveRisk") or
                               param_map.get("RiskLevel") or
                               param_map.get("異常浪風險") or "—")
                description = param_map.get("Description") or param_map.get("說明") or ""
                parsed.append({
                    "location":    loc_name,
                    "risk_level":  risk_level,
                    "description": description,
                    "params":      param_map,
                })

            result["records"] = parsed
            logger.info(f"  → 解析 {len(parsed)} 個海岸異常浪風險地點")
        except Exception as e:
            logger.error(f"海岸異常浪風險解析失敗: {e}")
            traceback.print_exc()
        return result

    # ══════════════════════════════════════════════════════════
    # F-B0082-001 船級作業風險
    # ══════════════════════════════════════════════════════════
    def fetch_vessel_risk(self) -> dict:
        logger.info("🔍 抓取船級作業風險 F-B0082-001...")
        raw    = self._fetch("F-B0082-001", fallback_file="F-B0082-001.json")
        result = {"dataset_id": "F-B0082-001", "title": "船級作業風險",
                  "records": [], "raw": raw}
        try:
            cwa            = raw.get("cwaopendata", {})
            resources_node = cwa.get("Resources") or cwa.get("resources") or {}
            resource_list  = safe_list(
                resources_node.get("Resource") or resources_node.get("resource"))
            if not resource_list:
                resource_list = [resources_node] if resources_node else []

            records = []
            for resource in resource_list:
                if not isinstance(resource, dict):
                    continue
                meta = resource.get("Metadata") or resource.get("metadata") or {}
                records.append({
                    "product_url":   resource.get("ProductURL") or resource.get("productURL", ""),
                    "resource_name": (meta.get("ResourceName") or
                                      meta.get("resourceName", "船級作業風險")),
                    "description":   (meta.get("ResourceDescription") or
                                      meta.get("resourceDescription", "")),
                    "file_format":   (meta.get("FileFormat") or
                                      meta.get("fileFormat", "NetCDF")),
                    "sent":          cwa.get("Sent") or cwa.get("sent", ""),
                    "dataset_name":  cwa.get("DatasetName") or cwa.get("datasetName", ""),
                })

            result["records"] = records
            logger.info(f"  → 解析船級作業風險 {len(records)} 筆 NetCDF 連結")
        except Exception as e:
            logger.error(f"船級作業風險解析失敗: {e}")
            traceback.print_exc()
        return result

    # ══════════════════════════════════════════════════════════
    # O-B0076-001 浮標站與潮位站
    # ══════════════════════════════════════════════════════════
    def fetch_realtime_obs(self) -> dict:
        logger.info("🔍 抓取浮標站與潮位站測站資料 O-B0076-001...")
        raw    = self._fetch("O-B0076-001", fallback_file="O-B0076-001.json")
        result = {"dataset_id": "O-B0076-001", "title": "浮標站與潮位站測站資料",
                  "records": [], "raw": raw}
        if not raw:
            return result
        try:
            cwa = raw.get("cwaopendata", {})
            logger.info(f"  [DEBUG] O-B0076 cwaopendata keys: {list(cwa.keys())}")

            resources_node = cwa.get("Resources") or cwa.get("resources") or {}
            resource_list  = safe_list(
                resources_node.get("Resource") or resources_node.get("resource"))
            if not resource_list and resources_node:
                resource_list = [resources_node]

            records = []
            for resource in resource_list:
                if not isinstance(resource, dict):
                    continue
                meta = resource.get("Metadata") or resource.get("metadata") or {}
                records.append({
                    "product_url":   resource.get("ProductURL") or resource.get("productURL", ""),
                    "resource_name": (meta.get("ResourceName") or
                                      meta.get("resourceName", "浮標站與潮位站資料")),
                    "description":   (meta.get("ResourceDescription") or
                                      meta.get("resourceDescription", "")),
                    "file_format":   (meta.get("FileFormat") or
                                      meta.get("fileFormat", "JSON")),
                    "sent":          cwa.get("Sent") or cwa.get("sent", ""),
                    "dataset_name":  cwa.get("DatasetName") or cwa.get("datasetName", ""),
                    "_type":         "resource_link",
                })

            result["records"] = records
            if records:
                logger.info(f"  → O-B0076-001 取得 {len(records)} 筆資源連結")
            else:
                logger.info("  → O-B0076-001 無測站清單（僅提供資源連結型 API）")
        except Exception as e:
            logger.error(f"觀測站資料解析失敗: {e}")
            traceback.print_exc()
        return result


# ══════════════════════════════════════════════════════════════
# HTML 渲染器
# ══════════════════════════════════════════════════════════════
class MarineForecastRenderer:
    def __init__(self):
        self._tz = timezone(timedelta(hours=Config.DISPLAY_TZ_OFFSET))

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
            dt = datetime.fromisoformat(str(iso_str))
            return dt.astimezone(self._tz).strftime("%m/%d %H:%M")
        except Exception:
            s = str(iso_str)
            return s[5:16] if len(s) >= 16 else s

    def _wave_color(self, height_str) -> tuple:
        try:
            h = float(height_str)
        except (ValueError, TypeError):
            return "#94a3b8", "#f8fafc", "#64748b", "— 無資料"
        if h >= Config.WAVE_WARN_M:
            return "#ef4444", "#fef2f2", "#b91c1c", f"⚠️ {h:.1f}m 警戒"
        elif h >= Config.WAVE_CAUTION_M:
            return "#f97316", "#fff7ed", "#c2410c", f"🟠 {h:.1f}m 注意"
        else:
            return "#22c55e", "#f0fdf4", "#15803d", f"✅ {h:.1f}m 正常"

    def _wind_color(self, speed_str) -> tuple:
        try:
            s = float(speed_str)
        except (ValueError, TypeError):
            return "#94a3b8", "— 無資料"
        if s >= Config.WIND_WARN_MS:
            return "#ef4444", f"⚠️ {s:.1f} m/s"
        elif s >= Config.WIND_CAUTION_MS:
            return "#f97316", f"🟠 {s:.1f} m/s"
        else:
            return "#22c55e", f"✅ {s:.1f} m/s"

    def _wave_level(self, wave_desc: str) -> tuple:
        desc  = str(wave_desc)
        nums  = re.findall(r'\d+\.?\d*', desc)
        max_h = max((float(n) for n in nums), default=0)
        if max_h >= Config.WAVE_WARN_M or "大浪" in desc:
            return "#fef2f2", "#b91c1c", f"⚠️ {desc}"
        elif max_h >= Config.WAVE_CAUTION_M or "中浪" in desc:
            return "#fff7ed", "#c2410c", f"🟠 {desc}"
        else:
            return "#f0fdf4", "#15803d", f"✅ {desc}"

    def _bf_level(self, bf_desc: str) -> tuple:
        nums   = re.findall(r'\d+', str(bf_desc))
        max_bf = max((int(n) for n in nums), default=0)
        if max_bf >= Config.BF_WARN:
            return "#ef4444", f"⚠️ {bf_desc}"
        elif max_bf >= Config.BF_CAUTION:
            return "#f97316", f"🟠 {bf_desc}"
        else:
            return "#22c55e", f"✅ {bf_desc}"

    def _tide_range_color(self, tide_range: str) -> str:
        s = str(tide_range)
        return "#1d4ed8" if "大" in s else ("#0ea5e9" if "中" in s else "#94a3b8")

    def _risk_color(self, risk_level: str) -> tuple:
        r = str(risk_level).upper()
        if any(w in r for w in ["高", "HIGH", "3", "危險"]):
            return "#ef4444", "#fef2f2", "⚠️ 高風險"
        elif any(w in r for w in ["中", "MEDIUM", "2", "注意"]):
            return "#f97316", "#fff7ed", "🟠 中風險"
        elif any(w in r for w in ["低", "LOW", "1", "正常"]):
            return "#22c55e", "#f0fdf4", "✅ 低風險"
        else:
            return "#94a3b8", "#f8fafc", f"ℹ️ {risk_level}"

    def _uvi_color(self, uvi_str: str) -> tuple:
        try:
            uvi = float(uvi_str)
        except (ValueError, TypeError):
            return "#94a3b8", "—"
        if uvi >= 11:
            return "#7c3aed", f"☀️ {uvi:.0f} 極端"
        elif uvi >= 8:
            return "#ef4444", f"☀️ {uvi:.0f} 過量"
        elif uvi >= 6:
            return "#f97316", f"☀️ {uvi:.0f} 高量"
        elif uvi >= 3:
            return "#eab308", f"☀️ {uvi:.0f} 中量"
        else:
            return "#22c55e", f"☀️ {uvi:.0f} 低量"

    def _section_header(self, icon: str, title: str, color: str,
                        subtitle: str = "") -> str:
        sub = (f"&nbsp;&nbsp;<font face='Arial,sans-serif' size='2' "
               f"color='#e2e8f0'>{subtitle}</font>") if subtitle else ""
        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:6px;">
  <tr>
    <td bgcolor="{color}" style="padding:10px 16px;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#ffffff">
        <b>{icon}&nbsp;{title}</b>
      </font>{sub}
    </td>
  </tr>
</table>"""

    def _no_data_row(self, title: str, dataset_id: str) -> str:
        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:12px;border:1px solid #e2e8f0;">
  <tr>
    <td bgcolor="#f8fafc" style="padding:12px 16px;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#94a3b8">
        ℹ️ <b>{title}</b>（{dataset_id}）— 本次無資料或 API 暫時無法取得
      </font>
    </td>
  </tr>
</table>"""

    # ══════════════════════════════════════════════════════════
    # 渲染：F-B0083-006 海岸異常浪風險（過濾四大商港附近）
    # ══════════════════════════════════════════════════════════
    def _render_rogue_wave_risk(self, data: dict) -> str:
        records = data.get("records", [])
        meta    = data.get("meta", {})
        if not records:
            return self._no_data_row("海岸異常浪風險", "F-B0083-006")

        issued   = self._fmt_time(meta.get("issued", ""))
        keywords = Config.MAJOR_PORT_KEYWORDS

        # ── 過濾四大商港附近地點
        filtered = [r for r in records
                    if any(kw in r.get("location", "") for kw in keywords)]
        display  = filtered if filtered else records  # 若無匹配則顯示全部

        filter_note = (f"&nbsp;·&nbsp;顯示 {len(display)}/{len(records)} 個地點（四大商港附近）"
                       if filtered else "")

        cards_html = ""
        for loc in display:
            loc_name    = self._esc(loc.get("location", ""))
            risk_level  = self._esc(loc.get("risk_level", "—"))
            description = self._esc(loc.get("description", ""))
            r_color, r_bg, r_label = self._risk_color(risk_level)

            cards_html += f"""
<table width="23%" border="0" cellpadding="0" cellspacing="0" bgcolor="#ffffff"
       style="display:inline-table;margin:0 1% 10px 1%;
              border:2px solid {r_color};vertical-align:top;">
  <tr>
    <td bgcolor="{r_color}" style="padding:6px 10px;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#ffffff">
        <b>{loc_name}</b>
      </font>
    </td>
  </tr>
  <tr>
    <td bgcolor="{r_bg}" style="padding:8px 10px;text-align:center;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="{r_color}">
        <b>{r_label}</b>
      </font><br>
      <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#64748b">
        {description if description else "&nbsp;"}
      </font>
    </td>
  </tr>
</table>"""

        return f"""
{self._section_header("🌊", "海岸異常浪（瘋狗浪）風險", "#b91c1c",
                       f"F-B0083-006 · 未來18小時 · 發布：{issued}{filter_note}")}
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:20px;background:#fff5f5;border:1px solid #fecaca;">
  <tr>
    <td style="padding:10px 16px;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#7f1d1d">
        ⚠️ <b>注意：</b>異常浪（瘋狗浪）可在無明顯預警下突然出現，
        沿岸作業人員及遊客請遠離海岸危險區域。
      </font>
    </td>
  </tr>
  <tr>
    <td style="padding:4px 16px 16px 16px;">{cards_html}</td>
  </tr>
</table>"""

    # ══════════════════════════════════════════════════════════
    # 渲染：O-B0076-001 觀測站資料
    # ══════════════════════════════════════════════════════════
    def _render_realtime_obs(self, data: dict) -> str:
        records = data.get("records", [])

        if records and records[0].get("_type") == "resource_link":
            rec          = records[0]
            res_name     = self._esc(rec.get("resource_name", "浮標站與潮位站資料"))
            description  = self._esc(rec.get("description", ""))
            file_format  = self._esc(rec.get("file_format", "JSON"))
            product_url  = rec.get("product_url", "")
            sent         = self._fmt_time(rec.get("sent", ""))
            dataset_name = self._esc(rec.get("dataset_name", ""))

            return f"""
{self._section_header("📡", "浮標站與潮位站資料", "#0f172a",
                       f"O-B0076-001 · 發布：{sent}")}
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:20px;border:1px solid #e2e8f0;">
  <tr>
    <td bgcolor="#f8fafc" style="padding:14px 18px;">
      <table width="100%" border="0" cellpadding="6" cellspacing="0">
        <tr>
          <td width="25%">
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
              <b>資料集名稱：</b>
            </font>
          </td>
          <td>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#0f172a">
              {dataset_name or res_name}
            </font>
          </td>
        </tr>
        <tr>
          <td>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
              <b>資料說明：</b>
            </font>
          </td>
          <td>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#0f172a">
              {description if description else "浮標站與潮位站觀測資料"}
            </font>
          </td>
        </tr>
        <tr>
          <td>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
              <b>檔案格式：</b>
            </font>
          </td>
          <td>
            <font face="Arial,sans-serif" size="2" color="#7c3aed">
              <b>{file_format}</b>
            </font>
          </td>
        </tr>
        <tr>
          <td>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
              <b>下載連結：</b>
            </font>
          </td>
          <td>
            <font face="Arial,sans-serif" size="2" color="#0369a1">
              <a href="{product_url}" style="color:#0369a1;">
                {product_url[:80]}{"..." if len(product_url) > 80 else ""}
              </a>
            </font>
          </td>
        </tr>
      </table>
      <br>
      <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#94a3b8">
        ℹ️ O-B0076-001 為資料下載型 API，提供浮標站與潮位站完整觀測資料檔案下載。
        如需即時數值，請下載後解析該資料檔。
      </font>
    </td>
  </tr>
</table>"""

        if not records:
            return self._no_data_row("浮標站與潮位站測站資料", "O-B0076-001")

        buoy_stations  = [r for r in records
                          if any(r.get("station_id", "").startswith(p)
                                 for p in ("46", "C6", "OAC"))]
        tidal_stations = [r for r in records if r not in buoy_stations]

        def _render_station_card(st: dict) -> str:
            st_id        = self._esc(st.get("station_id", ""))
            st_name      = self._esc(st.get("station_name", ""))
            st_type      = self._esc(st.get("station_type", ""))
            status       = self._esc(st.get("status", "運作中"))
            lat          = self._esc(str(st.get("lat", "")))
            lon          = self._esc(str(st.get("lon", "")))
            county       = self._esc(st.get("county", ""))
            sensors      = st.get("sensors", [])
            status_color = "#22c55e" if "運作" in status or status == "1" else "#94a3b8"
            sensor_html  = "、".join(sensors[:6]) if sensors else "—"
            return f"""
<table width="48%" border="0" cellpadding="0" cellspacing="0" bgcolor="#ffffff"
       style="display:inline-table;margin:0 1% 10px 1%;
              border:1px solid #cbd5e1;vertical-align:top;">
  <tr>
    <td bgcolor="#1e293b" style="padding:8px 12px;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#ffffff">
        <b>📡 {st_name}</b>
      </font>
      <font face="Arial,sans-serif" size="1" color="#94a3b8">&nbsp;({st_id})</font><br>
      <font face="Arial,sans-serif" size="1" color="{status_color}">● {status}</font>
      <font face="Arial,sans-serif" size="1" color="#64748b">
        &nbsp;｜&nbsp;{st_type}&nbsp;｜&nbsp;{county}
      </font>
    </td>
  </tr>
  <tr>
    <td bgcolor="#f8fafc" style="padding:8px 12px;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
        📍 座標：{lat}°N, {lon}°E<br>
        🔧 感測器：<font color="#0369a1">{sensor_html}</font>
      </font>
    </td>
  </tr>
</table>"""

        buoy_html     = "".join(_render_station_card(s) for s in buoy_stations)
        tidal_html    = "".join(_render_station_card(s) for s in tidal_stations)
        buoy_section  = (f"<b>🔵 浮標站</b><div style='margin:8px 0;'>{buoy_html}</div>"
                         if buoy_html else "")
        tidal_section = (f"<b>🟢 潮位站</b><div style='margin:8px 0;'>{tidal_html}</div>"
                         if tidal_html else "")

        return f"""
{self._section_header("📡", "浮標站與潮位站測站清單", "#0f172a",
                       "O-B0076-001 · 測站位置・類型・感測器清單")}
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:20px;border:1px solid #e2e8f0;">
  <tr><td style="padding:14px 16px;">{buoy_section}{tidal_section}</td></tr>
</table>"""

    # ══════════════════════════════════════════════════════════
    # 渲染：F-A0012-001 海面天氣預報
    # ══════════════════════════════════════════════════════════
    def _render_marine_weather(self, data: dict) -> str:
        records = data.get("records", [])
        if not records:
            return self._no_data_row("海面天氣預報", "F-A0012-001")
        all_tables = ""
        for loc in records:
            loc_name  = self._esc(loc.get("location", ""))
            forecasts = loc.get("forecasts", [])
            if not forecasts:
                continue
            rows = ""
            for fc in forecasts:
                st       = self._fmt_time(fc.get("start_time", ""))
                et       = self._fmt_time(fc.get("end_time", ""))
                weather  = self._esc(fc.get("weather", ""))
                wind_dir = self._esc(fc.get("wind_dir", ""))
                beaufort = fc.get("beaufort", "")
                wave_h   = fc.get("wave_height", "")
                bf_color, bf_label         = self._bf_level(beaufort)
                wave_bg, wave_tc, wave_lbl = self._wave_level(wave_h)
                rows += f"""
<tr>
  <td bgcolor="#f8fafc"
      style="padding:6px 10px;border:1px solid #e2e8f0;width:18%;white-space:nowrap;">
    <font face="Arial,sans-serif" size="1" color="#64748b">{st}<br>～{et}</font>
  </td>
  <td bgcolor="#ffffff" style="padding:6px 10px;border:1px solid #e2e8f0;width:24%;">
    <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">{weather}</font>
  </td>
  <td bgcolor="#ffffff" style="padding:6px 10px;border:1px solid #e2e8f0;width:18%;">
    <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">{wind_dir}</font>
  </td>
  <td bgcolor="#ffffff" style="padding:6px 10px;border:1px solid #e2e8f0;width:20%;">
    <font face="Arial,sans-serif" size="2" color="{bf_color}"><b>{bf_label}</b></font>
  </td>
  <td bgcolor="{wave_bg}" style="padding:6px 10px;border:1px solid #e2e8f0;width:20%;">
    <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="{wave_tc}">
      <b>{wave_lbl}</b>
    </font>
  </td>
</tr>"""
            all_tables += f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:14px;border:1px solid #bae6fd;">
  <tr>
    <td colspan="5" bgcolor="#0284c7" style="padding:8px 14px;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#ffffff">
        <b>🌊 {loc_name}</b>
      </font>
    </td>
  </tr>
  <tr>
    <td bgcolor="#e0f2fe" style="padding:5px 10px;border:1px solid #bae6fd;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#0369a1"><b>時段</b></font>
    </td>
    <td bgcolor="#e0f2fe" style="padding:5px 10px;border:1px solid #bae6fd;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#0369a1"><b>天氣現象</b></font>
    </td>
    <td bgcolor="#e0f2fe" style="padding:5px 10px;border:1px solid #bae6fd;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#0369a1"><b>風向</b></font>
    </td>
    <td bgcolor="#e0f2fe" style="padding:5px 10px;border:1px solid #bae6fd;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#0369a1"><b>蒲福風級</b></font>
    </td>
    <td bgcolor="#e0f2fe" style="padding:5px 10px;border:1px solid #bae6fd;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#0369a1"><b>浪高</b></font>
    </td>
  </tr>
  {rows}
</table>"""
        return f"""
{self._section_header("📋", "海面天氣預報（5 天）", "#0369a1",
                       "F-A0012-001 · 文字預報・風向・蒲福・浪高")}
<div style="padding:6px 0;margin-bottom:20px;">{all_tables}</div>"""

    # ══════════════════════════════════════════════════════════
    # 渲染：F-A0021-001 港口潮汐預報
    # ══════════════════════════════════════════════════════════
    def _render_tide_forecast(self, data: dict) -> str:
        records = data.get("records", [])
        if not records:
            return self._no_data_row("港口潮汐預報", "F-A0021-001")
        cards_html = ""
        for loc in records:
            loc_name = self._esc(loc.get("location", ""))
            tides    = loc.get("tides", [])
            if not tides:
                continue
            day_cells = ""
            for day in tides:
                date       = self._esc(day.get("date", ""))
                lunar      = self._esc(day.get("lunar", ""))
                tide_range = self._esc(day.get("tide_range", ""))
                highs      = day.get("highs", [])
                lows       = day.get("lows", [])
                rng_color  = self._tide_range_color(tide_range)
                highs_html = "<br>".join(
                    f"<font color='#1d4ed8'><b>▲ {h}</b></font>" for h in highs
                ) or "<font color='#94a3b8'>—</font>"
                lows_html  = "<br>".join(
                    f"<font color='#64748b'>▼ {l}</font>" for l in lows
                ) or "<font color='#94a3b8'>—</font>"
                day_cells += f"""
<td align="center" bgcolor="#ffffff"
    style="padding:10px 6px;border:1px solid #e2e8f0;
           width:33%;vertical-align:top;">
  <font face="Arial,sans-serif" size="2" color="#0f172a"><b>{date}</b></font><br>
  <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#94a3b8">
    農曆 {lunar}
  </font><br>
  <table border="0" cellpadding="2" cellspacing="0"
         bgcolor="{rng_color}" style="margin:5px auto;"><tr><td>
    <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#ffffff">
      &nbsp;{tide_range}潮&nbsp;
    </font>
  </td></tr></table>
  <font face="Arial,sans-serif" size="1">{highs_html}</font><br>
  <font face="Arial,sans-serif" size="1">{lows_html}</font>
</td>"""
            cards_html += f"""
<table width="48%" border="0" cellpadding="0" cellspacing="0" bgcolor="#ffffff"
       style="display:inline-table;margin:0 1% 14px 1%;
              border:2px solid #bae6fd;vertical-align:top;">
  <tr>
    <td colspan="3" bgcolor="#0369a1" style="padding:8px 14px;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#ffffff">
        <b>⚓ {loc_name}</b>
      </font>
      <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#bae6fd">
        &nbsp;近 3 日潮汐 (Chart Datum)
      </font>
    </td>
  </tr>
  <tr>{day_cells}</tr>
</table>"""
        return f"""
{self._section_header("🌙", "港口潮汐預報（近 3 日）", "#1d4ed8",
                       "F-A0021-001 · 高潮▲ / 低潮▼ · Chart Datum (cm)")}
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:20px;">
  <tr><td style="padding:10px 0;">{cards_html}</td></tr>
</table>"""

    # ══════════════════════════════════════════════════════════
    # 渲染：F-A0037-xxx 藍色公路（過濾四大商港附近航段）
    # ══════════════════════════════════════════════════════════
    def _render_blue_highway(self, data: dict) -> str:
        records = data.get("records", [])
        if not records:
            return self._no_data_row("藍色公路海氣象預報", "F-A0037-xxx")

        keywords        = Config.MAJOR_PORT_KEYWORDS
        all_routes_html = ""

        for route in records:
            route_code = self._esc(route.get("route_code", ""))
            route_name = self._esc(route.get("route_name", f"航線 {route_code}"))
            issued     = self._fmt_time(route.get("issued", ""))
            segments   = route.get("segments", [])

            # ── 過濾四大商港附近航段
            filtered_segs = [s for s in segments
                             if any(kw in s.get("location", "") for kw in keywords)]
            display_segs  = filtered_segs if filtered_segs else segments[:4]

            segs_html = ""
            for seg in display_segs:
                loc_name  = self._esc(seg.get("location", ""))
                forecasts = seg.get("forecasts", [])
                if not forecasts:
                    continue

                cells = ""
                for fc in forecasts:
                    dt       = self._fmt_time(fc.get("datetime", ""))
                    wave_h   = fc.get("wave_height", "")
                    curr_spd = fc.get("current_speed", "")
                    beaufort = self._esc(str(fc.get("beaufort", "")))
                    wind_dir = self._esc(str(fc.get("wind_dir", "")))

                    try:
                        wh = float(wave_h)
                        if wh >= Config.WAVE_WARN_M:
                            w_color, w_label = "#b91c1c", f"⚠️ {wh:.1f}m"
                        elif wh >= Config.WAVE_CAUTION_M:
                            w_color, w_label = "#c2410c", f"🟠 {wh:.1f}m"
                        else:
                            w_color, w_label = "#15803d", f"✅ {wh:.1f}m"
                    except (ValueError, TypeError):
                        w_color, w_label = "#94a3b8", "—"

                    try:
                        cs = float(curr_spd)
                        if cs >= Config.CURRENT_WARN_MS:
                            c_color, c_label = "#ef4444", f"⚠️ {cs:.2f}"
                        elif cs >= 0.8:
                            c_color, c_label = "#f97316", f"🟠 {cs:.2f}"
                        else:
                            c_color, c_label = "#22c55e", f"✅ {cs:.2f}"
                    except (ValueError, TypeError):
                        c_color, c_label = "#94a3b8", "—"

                    cells += f"""
<td align="center" bgcolor="#ffffff"
    style="padding:6px 3px;border:1px solid #e2e8f0;min-width:70px;">
  <font face="Arial,sans-serif" size="1" color="#94a3b8">{dt}</font><br>
  <font face="Arial,sans-serif" size="2" color="{w_color}"><b>{w_label}</b></font><br>
  <font face="Arial,sans-serif" size="1" color="#64748b">
    Bf.{beaufort}&nbsp;{wind_dir}<br>
    <font color="{c_color}">{c_label} m/s</font>
  </font>
</td>"""

                segs_html += f"""
<tr>
  <td bgcolor="#ecfdf5"
      style="padding:6px 12px;border:1px solid #d1fae5;white-space:nowrap;width:15%;">
    <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#065f46">
      <b>📍 {loc_name}</b>
    </font>
  </td>
  {cells}
</tr>"""

            if not segs_html:
                continue

            filter_note = (f"&nbsp;·&nbsp;顯示 {len(display_segs)}/{len(segments)} 航段（四大商港附近）"
                           if filtered_segs else "")
            all_routes_html += f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:14px;border:1px solid #a7f3d0;">
  <tr>
    <td colspan="20" bgcolor="#059669" style="padding:8px 14px;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#ffffff">
        <b>🛳️ {route_name}</b>
      </font>
      <font face="Arial,sans-serif" size="1" color="#a7f3d0">
        &nbsp;&nbsp;{route_code} · 發布：{issued}{filter_note}
      </font>
    </td>
  </tr>
  <tr>
    <td bgcolor="#d1fae5" style="padding:5px 12px;border:1px solid #a7f3d0;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#065f46">
        <b>航段</b>
      </font>
    </td>
    <td colspan="19" bgcolor="#d1fae5" style="padding:5px 10px;border:1px solid #a7f3d0;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#065f46">
        <b>浪高 ✅&lt;1.5m 🟠≥1.5m ⚠️≥2.5m</b>&nbsp;&nbsp;
        <b>Bf. 蒲福風級</b>&nbsp;&nbsp;
        <b>海流 (m/s)</b>
      </font>
    </td>
  </tr>
  {segs_html}
</table>"""

        return f"""
{self._section_header("🛳️", "藍色公路海氣象預報（48 小時）", "#059669",
                       "F-A0037-001/002/003/026 · 四大商港附近航段")}
<div style="padding:6px 0;margin-bottom:20px;">{all_routes_html}</div>"""

    # ══════════════════════════════════════════════════════════
    # 渲染：F-B0053-049 港口 7 日天氣預報
    # ══════════════════════════════════════════════════════════
    def _render_port_weather_7d(self, data: dict) -> str:
        records = data.get("records", [])
        if not records:
            return self._no_data_row("港口 7 日天氣預報", "F-B0053-049")

        WX_ICON = {
            "晴": "☀️", "多雲": "⛅", "陰": "☁️",
            "雨": "🌧", "雷": "⛈", "霧": "🌫",
            "雪": "❄️", "有雨": "🌧", "短暫雨": "🌦",
        }

        def _wx_icon(wx: str) -> str:
            for k, v in WX_ICON.items():
                if k in wx:
                    return v
            return "🌤"

        all_cards = ""
        for loc in records:
            loc_name  = self._esc(loc.get("location", ""))
            forecasts = loc.get("forecasts", [])
            if not forecasts:
                continue

            day_cells = ""
            for fc in forecasts:
                st       = self._fmt_time(fc.get("start_time", ""))
                max_t    = fc.get("max_temp",   "—")
                min_t    = fc.get("min_temp",   "—")
                weather  = fc.get("weather",    "—")
                wind_spd = fc.get("wind_speed", "")
                wind_dir = self._esc(str(fc.get("wind_dir",  "—")))
                pop      = fc.get("pop",        "—")
                uvi      = fc.get("uvi",        "")
                beaufort = self._esc(str(fc.get("beaufort",  "—")))

                wx_icon = _wx_icon(str(weather))
                wx_text = self._esc(str(weather))

                try:
                    mx      = float(max_t)
                    t_color = "#ef4444" if mx >= 35 else "#f97316" if mx >= 30 else "#0ea5e9"
                except (ValueError, TypeError):
                    t_color = "#94a3b8"

                try:
                    pop_v     = float(pop)
                    pop_bg    = "#fef2f2" if pop_v >= 70 else "#fff7ed" if pop_v >= 40 else "#f0fdf4"
                    pop_color = "#ef4444" if pop_v >= 70 else "#f97316" if pop_v >= 40 else "#22c55e"
                    pop_label = f"{pop_v:.0f}%"
                except (ValueError, TypeError):
                    pop_bg, pop_color, pop_label = "#f8fafc", "#94a3b8", str(pop)

                wnd_c, wnd_label = self._wind_color(wind_spd)
                uvi_c, uvi_label = self._uvi_color(uvi)
                bf_c,  bf_label  = self._bf_level(beaufort)

                day_cells += f"""
<td align="center" valign="top" bgcolor="#ffffff"
    style="padding:10px 6px;border:1px solid #e2e8f0;
           min-width:88px;vertical-align:top;">
  <font face="Arial,sans-serif" size="1" color="#64748b"><b>{st}</b></font><br>
  <div style="font-size:22px;margin:4px 0;">{wx_icon}</div>
  <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#475569">{wx_text}</font><br>
  <br>
  <font face="Arial,sans-serif" size="2" color="{t_color}"><b>{max_t}°</b></font>
  <font face="Arial,sans-serif" size="1" color="#94a3b8">/</font>
  <font face="Arial,sans-serif" size="2" color="#0ea5e9"><b>{min_t}°</b></font><br>
  <br>
  <table border="0" cellpadding="2" cellspacing="0"
         bgcolor="{pop_bg}" style="margin:2px auto;border-radius:3px;"><tr><td>
    <font face="Arial,sans-serif" size="1" color="{pop_color}">
      💧<b>{pop_label}</b>
    </font>
  </td></tr></table>
  <br>
  <font face="Arial,sans-serif" size="1" color="{wnd_c}"><b>{wnd_label}</b></font><br>
  <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#64748b">
    {wind_dir}&nbsp;Bf.<font color="{bf_c}">{beaufort}</font>
  </font><br>
  <br>
  <font face="Arial,sans-serif" size="1" color="{uvi_c}">{uvi_label}</font>
</td>"""

            PORT_COLOR = {
                "基隆": "#0369a1",
                "台北": "#0284c7", "臺北": "#0284c7",
                "台中": "#0891b2", "臺中": "#0891b2",
                "高雄": "#0e7490",
            }
            hdr_color = "#0369a1"
            for k, v in PORT_COLOR.items():
                if k in loc_name:
                    hdr_color = v
                    break

            all_cards += f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:18px;border:2px solid {hdr_color};">
  <tr>
    <td bgcolor="{hdr_color}" style="padding:10px 16px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td>
          <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#ffffff">
            <b>🏙️ {loc_name}</b>
          </font>
        </td>
        <td align="right">
          <font face="Arial,sans-serif" size="1" color="#bae6fd">
            F-B0053-049 · 7 日預報
          </font>
        </td>
      </tr></table>
    </td>
  </tr>
  <tr>
    <td bgcolor="#e0f2fe" style="padding:6px 10px;border-bottom:2px solid {hdr_color};">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#0369a1">
        &nbsp;&nbsp;<b>日期</b>&nbsp;|&nbsp;<b>天氣</b>&nbsp;|&nbsp;
        <b>高°/低°C</b>&nbsp;|&nbsp;<b>💧降雨機率</b>&nbsp;|&nbsp;
        <b>風速/風向</b>&nbsp;|&nbsp;<b>☀️UV</b>
      </font>
    </td>
  </tr>
  <tr>
    <td style="padding:6px 8px;overflow-x:auto;">
      <table border="0" cellpadding="0" cellspacing="4"
             style="border-collapse:separate;border-spacing:4px;">
        <tr>{day_cells}</tr>
      </table>
    </td>
  </tr>
</table>"""

        return f"""
{self._section_header("🏙️", "四大商港 7 日天氣預報", "#0369a1",
                       "F-B0053-049 · 基隆・台北港・台中港・高雄")}
<div style="padding:6px 0;margin-bottom:20px;">{all_cards}</div>"""

    # ══════════════════════════════════════════════════════════
    # 渲染：F-B0082-001 船級作業風險
    # ══════════════════════════════════════════════════════════
    def _render_vessel_risk(self, data: dict) -> str:
        records = data.get("records", [])
        if not records:
            return self._no_data_row("船級作業風險", "F-B0082-001")
        rec          = records[0]
        res_name     = self._esc(rec.get("resource_name", "船級作業風險"))
        description  = self._esc(rec.get("description", ""))
        file_format  = self._esc(rec.get("file_format", "NetCDF"))
        product_url  = rec.get("product_url", "")
        sent         = self._fmt_time(rec.get("sent", ""))
        dataset_name = self._esc(rec.get("dataset_name", ""))

        return f"""
{self._section_header("🚢", "船級作業風險", "#475569",
                       f"F-B0082-001 · 發布：{sent}")}
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:20px;border:1px solid #cbd5e1;">
  <tr>
    <td bgcolor="#f8fafc" style="padding:14px 18px;">
      <table width="100%" border="0" cellpadding="6" cellspacing="0">
        <tr>
          <td width="30%">
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
              <b>資料集名稱：</b>
            </font>
          </td>
          <td>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#0f172a">
              {dataset_name or res_name}
            </font>
          </td>
        </tr>
        <tr>
          <td>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
              <b>資料說明：</b>
            </font>
          </td>
          <td>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#0f172a">
              {description}
            </font>
          </td>
        </tr>
        <tr>
          <td>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
              <b>檔案格式：</b>
            </font>
          </td>
          <td>
            <font face="Arial,sans-serif" size="2" color="#7c3aed">
              <b>{file_format}</b>
            </font>
          </td>
        </tr>
        <tr>
          <td>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
              <b>下載連結：</b>
            </font>
          </td>
          <td>
            <font face="Arial,sans-serif" size="2" color="#0369a1">
              <a href="{product_url}" style="color:#0369a1;">
                {product_url[:80]}{"..." if len(product_url) > 80 else ""}
              </a>
            </font>
          </td>
        </tr>
      </table>
      <br>
      <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#94a3b8">
        ℹ️ 本資料集為 NetCDF 格式，需使用 netCDF4 / xarray 等工具解析，
        包含各船型（小型漁船、客輪、貨輪等）之作業風險指數網格資料。
      </font>
    </td>
  </tr>
</table>"""

    # ══════════════════════════════════════════════════════════
    # 圖例
    # ══════════════════════════════════════════════════════════
    def _render_legend(self) -> str:
        return f"""
<table width="100%" border="0" cellpadding="10" cellspacing="0"
       bgcolor="#f8fafc" style="margin-bottom:20px;border:1px solid #e2e8f0;">
  <tr>
    <td>
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
        <b>📖 圖例</b>&nbsp;&nbsp;
        <font color="#22c55e">✅ 正常</font>&nbsp;&nbsp;
        <font color="#f97316">🟠 注意
          （浪高≥{Config.WAVE_CAUTION_M}m / 風速≥{Config.WIND_CAUTION_MS}m/s /
           蒲福≥{Config.BF_CAUTION}級）
        </font>&nbsp;&nbsp;
        <font color="#ef4444">⚠️ 警戒
          （浪高≥{Config.WAVE_WARN_M}m / 風速≥{Config.WIND_WARN_MS}m/s /
           蒲福≥{Config.BF_WARN}級）
        </font>
      </font><br><br>
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
        <font color="#1d4ed8">■ 大潮</font>&nbsp;
        <font color="#0ea5e9">■ 中潮</font>&nbsp;
        <font color="#94a3b8">■ 小潮</font>&nbsp;&nbsp;
        <font color="#b91c1c">⚠️ 異常浪高風險</font>&nbsp;&nbsp;
        <font color="#7c3aed">☀️ UV 極端≥11</font>&nbsp;
        <font color="#ef4444">☀️ UV 過量≥8</font>&nbsp;
        <font color="#f97316">☀️ UV 高量≥6</font>
      </font>
    </td>
  </tr>
</table>"""

    # ══════════════════════════════════════════════════════════
    # 完整 HTML 輸出
    # ══════════════════════════════════════════════════════════
    def render_full_html(self, forecast_data: dict, run_time: datetime) -> str:
        cfg     = Config
        tpe_str = run_time.astimezone(
            timezone(timedelta(hours=cfg.DISPLAY_TZ_OFFSET))
        ).strftime("%Y-%m-%d %H:%M")

        port_wx_7d_html = self._render_port_weather_7d(
            forecast_data.get("port_weather_7d", {}))

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{cfg.EMAIL_TITLE}</title></head>
<body bgcolor="#eef6fb" style="margin:0;padding:0;">
<table width="100%" border="0" cellpadding="20" cellspacing="0" bgcolor="#eef6fb">
<tr><td align="center" valign="top">
<table width="{cfg.EMAIL_WIDTH}" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#ffffff" style="border:1px solid #b7d7e8;box-shadow:0 10px 30px rgba(15,23,42,.10);">

  <tr>
    <td bgcolor="#075985" style="padding:24px 28px;border-bottom:5px solid #38bdf8;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="5" color="#ffffff">
            <b>{cfg.EMAIL_TITLE}</b>
          </font><br>
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#dbeafe">
            {cfg.EMAIL_SUBTITLE}
          </font><br>
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#fde68a">
            <b>{cfg.EMAIL_BRANDING}</b>
          </font>
        </td>
        <td align="right" valign="middle">
          <font face="Arial,sans-serif" size="2" color="#e0f2fe">
            <b>更新時間：{tpe_str} ({cfg.DISPLAY_TZ_NAME})</b>
          </font><br><br>
          <table border="0" cellpadding="7" cellspacing="0" bgcolor="#0c4a6e"><tr><td>
            <font face="Arial,sans-serif" size="2" color="#ffffff">
              <b>資料來源：中央氣象署 F-B0053-049</b>
            </font>
          </td></tr></table>
        </td>
      </tr></table>
    </td>
  </tr>

  <tr>
    <td bgcolor="#e0f2fe" style="padding:12px 28px;border-bottom:1px solid #bae6fd;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#075985">
        <b>顯示範圍：</b>僅保留四大商港港區 7 日預報；其他海面天氣、潮汐、藍色公路、異常浪、即時觀測與船級風險已隱藏。
      </font>
    </td>
  </tr>

  <tr><td bgcolor="#ffffff" style="padding:24px 24px 8px 24px;">
    {port_wx_7d_html}
  </td></tr>

  <tr>
    <td bgcolor="#0f172a" align="center" style="padding:20px 16px;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#94a3b8">
        {cfg.FOOTER_LINE1}
      </font><br><br>
      <font face="Arial,sans-serif" size="2" color="#64748b">
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
class MarineForecastSender:
    def __init__(self):
        self.mail_user    = Config.MAIL_USER
        self.mail_pass    = Config.MAIL_PASS
        self.target_email = Config.TARGET_EMAIL
        self.smtp_server  = Config.SMTP_SERVER
        self.smtp_port    = Config.SMTP_PORT
        self.enabled      = all([self.mail_user, self.mail_pass, self.target_email])
        self.renderer     = MarineForecastRenderer()

        if not self.enabled:
            logger.warning("⚠️ Email 設定未填寫，將僅輸出至 console")
        else:
            logger.info(f"✅ Email 設定完成 → {self.target_email}")

    def send(self, forecast_data: dict, run_time: datetime) -> bool:
        cfg      = Config
        tpe_time = run_time.astimezone(
            timezone(timedelta(hours=cfg.DISPLAY_TZ_OFFSET)))
        tpe_str  = tpe_time.strftime("%Y-%m-%d %H:%M")

        print(f"\n{'═' * 65}")
        print(f"  🚢  CWA 四大商港 7 日天氣預報 v4.5.0  |  {tpe_str}")
        print(f"{'═' * 65}")
        d = forecast_data.get("port_weather_7d", {})
        count = len(d.get("records", []))
        status = f"✅ {count} 個港區" if count > 0 else "⚠️ 0 筆（API 待確認）"
        print(f"  🏙️  四大商港 7 日天氣：{status}")
        print(f"{'═' * 65}\n")

        if not self.enabled:
            logger.warning("ℹ️  Email 未設定，跳過發送")
            return False

        try:
            subject = (
                f"🌊 {cfg.SUBJECT_PREFIX} "
                f"({tpe_time.strftime('%m/%d %H:%M')}) "
                f"— 四大商港 7 日天氣 v4.5.0"
            )
            html_body = self.renderer.render_full_html(forecast_data, run_time)

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
# 診斷工具
# ══════════════════════════════════════════════════════════════
def debug_raw_structure():
    """印出各 API 的真實 JSON 結構，協助排查解析路徑"""
    fetcher = MarineFetcher(debug=True)

    for dataset_id in ["O-B0076-001", "F-A0037-001", "F-A0012-001", "F-B0053-049"]:
        print(f"\n{'='*60}")
        print(f"  {dataset_id} 結構診斷")
        print(f"{'='*60}")
        raw = fetcher._fetch(dataset_id)
        if not raw:
            print("  ✗ 無法取得資料")
            continue

        cwa = raw.get("cwaopendata", {})
        print(f"  cwaopendata keys: {list(cwa.keys())}")

        for k, v in cwa.items():
            if isinstance(v, dict):
                print(f"  cwa['{k}'] (dict) keys: {list(v.keys())}")
                for k2, v2 in list(v.items())[:5]:
                    if isinstance(v2, list):
                        print(f"    ['{k2}'] list len={len(v2)}")
                        if v2 and isinstance(v2[0], dict):
                            print(f"      [0] keys: {list(v2[0].keys())}")
                    elif isinstance(v2, dict):
                        print(f"    ['{k2}'] dict keys: {list(v2.keys())}")
                    else:
                        print(f"    ['{k2}'] = {repr(v2)[:60]}")
            elif isinstance(v, list):
                print(f"  cwa['{k}'] (list) len={len(v)}")
                if v and isinstance(v[0], dict):
                    print(f"    [0] keys: {list(v[0].keys())}")


# ══════════════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════════════
def run_once(debug: bool = False) -> dict:
    fetcher = MarineFetcher(debug=debug)
    sender  = MarineForecastSender()
    now     = datetime.now(timezone.utc)

    logger.info("=" * 60)
    logger.info("  CWA 海象預報系統 v4.4.0 啟動")
    logger.info("=" * 60)

    if debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("  [DEBUG 模式開啟]")

    # v4.5.0：依需求只抓取並顯示四大商港 7 日天氣預報，避免信件內容過長。
    forecast_data = {
        "port_weather_7d": fetcher.fetch_port_weather_7d(),
    }

    sender.send(forecast_data, now)
    return forecast_data


def run_monitor(interval_minutes: int = 60, debug: bool = False):
    logger.info(f"🚀 啟動持續監控，每 {interval_minutes} 分鐘更新一次")
    while True:
        try:
            run_once(debug=debug)
        except KeyboardInterrupt:
            logger.info("⛔ 使用者中斷")
            break
        except Exception as e:
            logger.error(f"未預期錯誤: {e}")
            traceback.print_exc()
        logger.info(f"⏳ 等待 {interval_minutes} 分鐘...")
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    args  = sys.argv[1:]
    debug = "--debug" in args

    if "--diagnose" in args:
        debug_raw_structure()
    elif "--monitor" in args:
        idx      = args.index("--monitor")
        interval = int(args[idx + 1]) if idx + 1 < len(args) else 60
        run_monitor(interval, debug=debug)
    else:
        run_once(debug=debug)

            
