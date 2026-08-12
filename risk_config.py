#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
risk_config.py
Phase 2 共用小工具：載入 risk_rules.json。

獨立成小檔案，是為了讓 event_extractor.py / carrier_news_filter.py /
risk_scorer.py / event_clusterer.py 都能直接載入設定，
而不需要互相 import 對方（避免循環 import）。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_RULES_FILENAME = "risk_rules.json"


@lru_cache(maxsize=4)
def load_risk_rules(config_path: str = DEFAULT_RULES_FILENAME) -> dict:
    """
    載入 risk_rules.json。找不到檔案時明確拋錯（不得靜默回傳空規則，
    否則 risk scoring 會在完全沒有規則的情況下悄悄跑出全 0 分）。
    """
    p = Path(config_path)
    if not p.exists():
        p = Path(__file__).parent / config_path
    if not p.exists():
        raise FileNotFoundError(f"risk_rules.json not found: {config_path}")
    with open(p, encoding="utf-8") as f:
        rules = json.load(f)
    logger.info(f"✅ 已載入風險規則設定檔：{p}")
    return rules
