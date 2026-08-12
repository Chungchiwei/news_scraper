#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schedule_provider.py
海事航運新聞監控系統 — Phase 6 §十一〜十三、十五、六十六〜六十七 Schedule Data Provider

職責：
  定義 ScheduleDataProvider 介面（Port Call 資料），第一版實作
  ConfigScheduleProvider 讀 config/schedules_config.json。

  ★ §六十七：ETA 格式錯誤（例如 "ABC"）或 ETD 早於 ETA 這種物理上不
  合理的資料，一律「丟掉這一筆 Port Call」並記 WARNING，不是讓整個
  Phase 6 crash，也不是硬塞一個猜測值。
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from operational_models import PortCall

logger = logging.getLogger(__name__)

DEFAULT_SCHEDULES_CONFIG = "config/schedules_config.json"


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


class ScheduleProviderError(RuntimeError):
    pass


class ScheduleDataProvider(ABC):
    @abstractmethod
    def get_port_calls(self) -> list:
        """回傳 list[PortCall]。Provider 層級失敗時應拋出例外。"""
        raise NotImplementedError

    @abstractmethod
    def data_timestamp(self) -> Optional[datetime]:
        raise NotImplementedError


class ConfigScheduleProvider(ScheduleDataProvider):
    def __init__(self, config_path: str = DEFAULT_SCHEDULES_CONFIG):
        self.config_path = config_path
        self._data: Optional[dict] = None

    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        p = Path(self.config_path)
        if not p.exists():
            p = Path(__file__).parent / self.config_path
        if not p.exists():
            raise ScheduleProviderError(f"schedules_config.json not found: {self.config_path}")
        with open(p, encoding="utf-8") as f:
            self._data = json.load(f)
        return self._data

    def get_port_calls(self) -> list:
        data = self._load()
        calls = []
        for i, raw in enumerate(data.get("port_calls", [])):
            vessel_name = raw.get("vessel_name")
            port_code = raw.get("port_code")
            if not vessel_name or not port_code:
                logger.warning(f"⚠️  Schedule config 第 {i} 筆缺少 vessel_name/port_code，已略過")
                continue

            eta = _parse_dt(raw.get("eta_utc"))
            if raw.get("eta_utc") and eta is None:
                logger.warning(
                    f"⚠️  Schedule config 第 {i} 筆 eta_utc 格式錯誤（{raw.get('eta_utc')!r}），"
                    "已略過此筆 Port Call"
                )
                continue

            etd = _parse_dt(raw.get("etd_utc"))
            if raw.get("etd_utc") and etd is None:
                logger.warning(
                    f"⚠️  Schedule config 第 {i} 筆 etd_utc 格式錯誤（{raw.get('etd_utc')!r}），"
                    "已略過此筆 Port Call"
                )
                continue

            if eta is not None and etd is not None and etd < eta:
                logger.warning(
                    f"⚠️  Schedule config 第 {i} 筆 ETD 早於 ETA（vessel={vessel_name}），"
                    "已略過此筆 Port Call"
                )
                continue

            calls.append(PortCall(
                vessel_id=raw.get("vessel_id", vessel_name),
                vessel_name=vessel_name,
                service_code=raw.get("service_code"),
                port_code=port_code,
                port_name=raw.get("port_name"),
                country=raw.get("country"),
                eta_utc=eta,
                etd_utc=etd,
                terminal=raw.get("terminal"),
                status=raw.get("status"),
            ))
        return calls

    def data_timestamp(self) -> Optional[datetime]:
        data = self._load()
        return _parse_dt(data.get("generated_at_utc"))


class FakeScheduleProvider(ScheduleDataProvider):
    """測試/預覽專用：直接傳入 list[PortCall]，不讀任何檔案。"""

    def __init__(self, port_calls: Optional[list] = None,
                 data_timestamp: Optional[datetime] = None,
                 raise_error: bool = False):
        self._port_calls = port_calls or []
        self._data_timestamp = data_timestamp or datetime.now(timezone.utc)
        self._raise_error = raise_error

    def get_port_calls(self) -> list:
        if self._raise_error:
            raise ScheduleProviderError("Simulated schedule provider failure")
        return list(self._port_calls)

    def data_timestamp(self) -> Optional[datetime]:
        if self._raise_error:
            raise ScheduleProviderError("Simulated schedule provider failure")
        return self._data_timestamp
