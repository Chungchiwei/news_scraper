#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
route_provider.py
海事航運新聞監控系統 — Phase 6 §十一〜十三、十六、三十八 Route/Service Data Provider

職責：
  定義 RouteDataProvider 介面（Service/航線資料），第一版實作
  ConfigRouteProvider 讀 config/services_config.json。
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from operational_models import Service

logger = logging.getLogger(__name__)

DEFAULT_SERVICES_CONFIG = "config/services_config.json"


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


class RouteProviderError(RuntimeError):
    pass


class RouteDataProvider(ABC):
    @abstractmethod
    def get_services(self) -> list:
        """回傳 list[Service]。Provider 層級失敗時應拋出例外。"""
        raise NotImplementedError

    @abstractmethod
    def data_timestamp(self) -> Optional[datetime]:
        raise NotImplementedError


class ConfigRouteProvider(RouteDataProvider):
    def __init__(self, config_path: str = DEFAULT_SERVICES_CONFIG):
        self.config_path = config_path
        self._data: Optional[dict] = None

    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        p = Path(self.config_path)
        if not p.exists():
            p = Path(__file__).parent / self.config_path
        if not p.exists():
            raise RouteProviderError(f"services_config.json not found: {self.config_path}")
        with open(p, encoding="utf-8") as f:
            self._data = json.load(f)
        return self._data

    def get_services(self) -> list:
        data = self._load()
        services = []
        for i, raw in enumerate(data.get("services", [])):
            service_code = raw.get("service_code")
            if not service_code:
                logger.warning(f"⚠️  Services config 第 {i} 筆缺少 service_code，已略過")
                continue
            services.append(Service(
                service_code=service_code,
                service_name=raw.get("service_name"),
                ports=list(raw.get("ports", [])),
                regions=list(raw.get("regions", [])),
                major_shipping_lanes=list(raw.get("major_shipping_lanes", [])),
            ))
        return services

    def data_timestamp(self) -> Optional[datetime]:
        data = self._load()
        return _parse_dt(data.get("generated_at_utc"))


class FakeRouteProvider(RouteDataProvider):
    """測試/預覽專用：直接傳入 list[Service]，不讀任何檔案。"""

    def __init__(self, services: Optional[list] = None,
                 data_timestamp: Optional[datetime] = None,
                 raise_error: bool = False):
        self._services = services or []
        self._data_timestamp = data_timestamp or datetime.now(timezone.utc)
        self._raise_error = raise_error

    def get_services(self) -> list:
        if self._raise_error:
            raise RouteProviderError("Simulated route provider failure")
        return list(self._services)

    def data_timestamp(self) -> Optional[datetime]:
        if self._raise_error:
            raise RouteProviderError("Simulated route provider failure")
        return self._data_timestamp
