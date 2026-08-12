#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
email_config.py
Phase 4 共用小工具：載入 email_rules.json。

跟 risk_config.py / memory_config.py 分開，維持三個設定檔各自獨立的
職責分離（risk_rules 回答嚴重度、memory_rules 回答要不要再通知、
email_rules 回答 Executive Email 要怎麼選/怎麼排版）。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_EMAIL_RULES_FILENAME = "email_rules.json"


@lru_cache(maxsize=4)
def load_email_rules(config_path: str = DEFAULT_EMAIL_RULES_FILENAME) -> dict:
    p = Path(config_path)
    if not p.exists():
        p = Path(__file__).parent / config_path
    if not p.exists():
        raise FileNotFoundError(f"email_rules.json not found: {config_path}")
    with open(p, encoding="utf-8") as f:
        rules = json.load(f)
    logger.info(f"✅ 已載入 Executive Email 設定檔：{p}")
    return rules
