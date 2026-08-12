#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
operational_models.py
海事航運新聞監控系統 — Phase 6 §四、十四〜十六、三十六 資料模型

職責：
  定義 Fleet / Schedule / Route 資料的標準結構，以及 Operational
  Relevance Engine 的輸出結構。跟 models.py（Phase 2 的 NewsArticle /
  MaritimeEvent）同樣原則：只用標準庫 dataclasses，未知欄位一律 None，
  不猜測值。

  ★ Event Severity（Phase 1-5）與 Operational Relevance（Phase 6）是
  兩個完全獨立的概念 —— 本檔案定義的所有結構都只回答「這件事跟本公司
  船隊有多相關」，不回答「這件事本身有多嚴重」。見 operational_
  relevance.py docstring。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ══════════════════════════════════════════════════════════════
# 常數命名空間
# ══════════════════════════════════════════════════════════════
class RelevanceLevel:
    DIRECT   = "DIRECT"
    HIGH     = "HIGH"
    MODERATE = "MODERATE"
    LOW      = "LOW"
    NONE     = "NONE"

    ORDER = (DIRECT, HIGH, MODERATE, LOW, NONE)
    RANK  = {DIRECT: 0, HIGH: 1, MODERATE: 2, LOW: 3, NONE: 4}


class RelevanceStatus:
    """
    §六十五：Provider 失敗 ≠ NONE。UNAVAILABLE 是獨立狀態，不可以被
    誤寫成「已評估過、確認沒有曝險」。
    """
    ASSESSED    = "ASSESSED"
    DATA_STALE  = "DATA_STALE"
    UNAVAILABLE = "UNAVAILABLE"


class ExposureType:
    OWN_VESSEL    = "OWN_VESSEL"
    PORT_CALL     = "PORT_CALL"
    SERVICE_ROUTE = "SERVICE_ROUTE"
    SHIPPING_LANE = "SHIPPING_LANE"
    REGIONAL      = "REGIONAL"
    GLOBAL_FLEET  = "GLOBAL_FLEET"
    NONE          = "NONE"


class OperationalNotificationState:
    """
    §五十三：與 Phase 3 的 NotificationState 完全獨立的第二條時間軸——
    這條軸描述的是「我們公司的曝險」如何隨時間變化，不是「事件本身」
    如何變化。兩者可以互不相同（事件 UNCHANGED，曝險仍可能 ESCALATED）。
    """
    EXPOSURE_NEW         = "EXPOSURE_NEW"
    EXPOSURE_ESCALATED   = "EXPOSURE_ESCALATED"
    EXPOSURE_UNCHANGED   = "EXPOSURE_UNCHANGED"
    EXPOSURE_REDUCED     = "EXPOSURE_REDUCED"
    EXPOSURE_CLEARED     = "EXPOSURE_CLEARED"
    EXPOSURE_UNAVAILABLE = "EXPOSURE_UNAVAILABLE"


# ══════════════════════════════════════════════════════════════
# Fleet / Schedule / Route 資料模型（§十四〜十六）
# ══════════════════════════════════════════════════════════════
@dataclass
class FleetVessel:
    vessel_id: str
    vessel_name: str
    imo_number: Optional[str] = None
    call_sign: Optional[str] = None
    service_code: Optional[str] = None
    status: Optional[str] = None
    current_port: Optional[str] = None
    previous_port: Optional[str] = None
    next_port: Optional[str] = None
    eta: Optional[datetime] = None
    etd: Optional[datetime] = None


@dataclass
class PortCall:
    vessel_id: str
    vessel_name: str
    service_code: Optional[str] = None
    port_code: str = ""
    port_name: Optional[str] = None
    country: Optional[str] = None
    eta_utc: Optional[datetime] = None
    etd_utc: Optional[datetime] = None
    terminal: Optional[str] = None
    status: Optional[str] = None


@dataclass
class Service:
    service_code: str
    service_name: Optional[str] = None
    ports: list = field(default_factory=list)                  # port_code 列表
    regions: list = field(default_factory=list)                 # 例如 ["ASIA","EUROPE"]
    major_shipping_lanes: list = field(default_factory=list)     # 例如 ["RED_SEA","SUEZ_CANAL"]


# ══════════════════════════════════════════════════════════════
# Operational Relevance 輸出結構（§四、三十六）
# ══════════════════════════════════════════════════════════════
@dataclass
class AffectedVessel:
    vessel_name: str
    service_code: Optional[str]
    next_port: Optional[str]
    eta_display: Optional[str]
    exposure_type: str
    hours_to_exposure: Optional[float]


@dataclass
class OperationalRelevance:
    event_id: str

    relevance_level: Optional[str]          # DIRECT/HIGH/MODERATE/LOW/NONE，UNAVAILABLE 時為 None
    relevance_score: Optional[float]
    relevance_status: str                    # ASSESSED / DATA_STALE / UNAVAILABLE

    own_fleet_involved: bool = False

    affected_vessels: list = field(default_factory=list)   # list[AffectedVessel]
    affected_services: list = field(default_factory=list)   # list[str]
    affected_ports: list = field(default_factory=list)       # list[str]
    exposure_types: list = field(default_factory=list)       # list[str]（ExposureType）

    closest_eta_hours: Optional[float] = None

    direct_match_count: int = 0
    potential_match_count: int = 0

    relevance_reasons: list = field(default_factory=list)     # list[str]，英文、deterministic

    data_timestamp: Optional[datetime] = None
    is_stale: bool = False

    assessed_at: Optional[datetime] = None
    run_id: Optional[str] = None
