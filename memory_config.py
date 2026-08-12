#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_config.py
Phase 3 共用小工具：載入 memory_rules.json（Persistent Event Memory 設定）。

刻意跟 risk_config.py 分開，維持「risk_rules.json 回答事件有多嚴重、
memory_rules.json 回答事件跟上次相比是否值得再次通知」的職責分離，
也避免 event_store / persistent_matcher / material_change_detector /
event_lifecycle / notification_policy 互相 import 造成循環依賴。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_RULES_FILENAME = "memory_rules.json"


@lru_cache(maxsize=4)
def load_memory_rules(config_path: str = DEFAULT_MEMORY_RULES_FILENAME) -> dict:
    """
    載入 memory_rules.json。找不到檔案時明確拋錯，不得靜默回傳空規則
    ——否則 matching window / expiry / material change 全部會用程式內
    的保守 fallback 值悄悄跑，難以追蹤。
    """
    p = Path(config_path)
    if not p.exists():
        p = Path(__file__).parent / config_path
    if not p.exists():
        raise FileNotFoundError(f"memory_rules.json not found: {config_path}")
    with open(p, encoding="utf-8") as f:
        rules = json.load(f)
    logger.info(f"✅ 已載入 Persistent Memory 設定檔：{p}")
    return rules
