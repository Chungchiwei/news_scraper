#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fleet_provider.py
海事航運新聞監控系統 — Phase 6 §十一〜十四、六十六 Fleet Data Provider

職責：
  定義 FleetDataProvider 介面，讓 operational_relevance.py 完全不知道
  船隊資料背後是本機 config、公司內部 API，還是資料庫。

  第一版實作 ConfigFleetProvider 讀 config/fleet_config.json（純本地
  檔案，不連任何內部系統）。未來要換成公司 API/DB provider，
  只需要新增一個實作這個介面的類別，不需要改 operational_relevance.py
  或任何比對邏輯。

  ★ 資料驗證原則（§六十六）：單筆資料有問題（缺船名、ETA 格式錯誤）
  只丟掉那一筆並記 WARNING，不能讓整個 Phase 6 crash。
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from operational_models import FleetVessel

logger = logging.getLogger(__name__)

DEFAULT_FLEET_CONFIG = "config/fleet_config.json"


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


class FleetProviderError(RuntimeError):
    """Provider 層級的致命錯誤（例如檔案存在但整體格式損毀）。"""
    pass


class FleetDataProvider(ABC):
    @abstractmethod
    def get_vessels(self) -> list:
        """回傳 list[FleetVessel]。Provider 層級失敗時應拋出例外，不得靜默回傳空清單假裝『沒有船』。"""
        raise NotImplementedError

    @abstractmethod
    def data_timestamp(self) -> Optional[datetime]:
        raise NotImplementedError


class ConfigFleetProvider(FleetDataProvider):
    def __init__(self, config_path: str = DEFAULT_FLEET_CONFIG):
        self.config_path = config_path
        self._data: Optional[dict] = None

    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        p = Path(self.config_path)
        if not p.exists():
            p = Path(__file__).parent / self.config_path
        if not p.exists():
            raise FleetProviderError(f"fleet_config.json not found: {self.config_path}")
        with open(p, encoding="utf-8") as f:
            self._data = json.load(f)
        return self._data

    def get_vessels(self) -> list:
        data = self._load()
        vessels = []
        seen_ids = set()
        for i, raw in enumerate(data.get("vessels", [])):
            vessel_id = raw.get("vessel_id")
            vessel_name = raw.get("vessel_name")
            if not vessel_id or not vessel_name:
                logger.warning(f"⚠️  Fleet config 第 {i} 筆缺少 vessel_id/vessel_name，已略過")
                continue
            if vessel_id in seen_ids:
                logger.warning(f"⚠️  Fleet config 出現重複 vessel_id={vessel_id}，仍保留但請檢查來源資料")
            seen_ids.add(vessel_id)
            vessels.append(FleetVessel(
                vessel_id=vessel_id,
                vessel_name=vessel_name,
                imo_number=raw.get("imo_number"),
                call_sign=raw.get("call_sign"),
                service_code=raw.get("service_code"),
                status=raw.get("status"),
                current_port=raw.get("current_port"),
                previous_port=raw.get("previous_port"),
                next_port=raw.get("next_port"),
                eta=_parse_dt(raw.get("eta")),
                etd=_parse_dt(raw.get("etd")),
            ))
        return vessels

    def data_timestamp(self) -> Optional[datetime]:
        data = self._load()
        return _parse_dt(data.get("generated_at_utc"))


class FakeFleetProvider(FleetDataProvider):
    """測試/預覽專用：直接傳入 list[FleetVessel]，不讀任何檔案。"""

    def __init__(self, vessels: Optional[list] = None,
                 data_timestamp: Optional[datetime] = None,
                 raise_error: bool = False):
        self._vessels = vessels or []
        self._data_timestamp = data_timestamp or datetime.now(timezone.utc)
        self._raise_error = raise_error

    def get_vessels(self) -> list:
        if self._raise_error:
            raise FleetProviderError("Simulated fleet provider failure")
        return list(self._vessels)

    def data_timestamp(self) -> Optional[datetime]:
        if self._raise_error:
            raise FleetProviderError("Simulated fleet provider failure")
        return self._data_timestamp
