#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
delivery_config.py
Phase 7 共用小工具：載入 delivery_rules.json。

跟 risk_config.py / memory_config.py / email_config.py / llm_config.py /
operational_config.py 同一種模式：每個設定檔各自獨立，職責分離。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DELIVERY_RULES_FILENAME = "delivery_rules.json"


@lru_cache(maxsize=4)
def load_delivery_rules(config_path: str = DEFAULT_DELIVERY_RULES_FILENAME) -> dict:
    p = Path(config_path)
    if not p.exists():
        p = Path(__file__).parent / config_path
    if not p.exists():
        raise FileNotFoundError(f"delivery_rules.json not found: {config_path}")
    with open(p, encoding="utf-8") as f:
        rules = json.load(f)
    logger.info(f"✅ 已載入 Delivery 設定檔：{p}")
    return rules
