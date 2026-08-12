#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm_config.py
海事航運新聞監控系統 — Phase 5 LLM 設定

★ 安全性（§七〜八）：LLM_ENABLED / provider / model / timeout / retries /
  circuit breaker 以及所有 API Key 一律從環境變數讀取，絕不硬編碼在
  原始碼、絕不寫進 llm_rules.json（那份檔案只放非機密的成本/eligibility
  規則）。API Key 本身也絕不記錄到 log。

★ 預設值（§九十四）：LLM_ENABLED 預設 false。要開啟必須明確設定
  環境變數，且建議在人工確認 Phase 5 Preview 後才開啟。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_LLM_RULES_FILENAME = "llm_rules.json"


@lru_cache(maxsize=4)
def load_llm_rules(config_path: str = DEFAULT_LLM_RULES_FILENAME) -> dict:
    p = Path(config_path)
    if not p.exists():
        p = Path(__file__).parent / config_path
    if not p.exists():
        raise FileNotFoundError(f"llm_rules.json not found: {config_path}")
    with open(p, encoding="utf-8") as f:
        rules = json.load(f)
    logger.info(f"✅ 已載入 LLM 設定檔：{p}")
    return rules


def _bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() not in ("false", "0", "no", "")


@dataclass
class LLMConfig:
    """
    ★ 這個 dataclass 只承載「怎麼呼叫 provider」的設定（開關/廠牌/模型/
    金鑰/timeout/重試），不承載任何成本控制或 eligibility 規則 —— 那些
    屬於 llm_rules.json，透過 load_llm_rules() 另外取得，兩者刻意分開
    （機密 vs 非機密設定分層，見 §七）。
    """
    enabled: bool
    provider: str          # "claude" / "openai" / "disabled"
    model: str
    anthropic_api_key: str
    openai_api_key: str
    timeout_seconds: int
    max_retries: int
    failure_circuit_breaker: int

    # ★ Phase 6 §九十一〜九十三：Fleet/Schedule/Route 屬於公司內部營運
    # 資料，預設「絕對不」送進外部 LLM Provider。只有明確設定
    # LLM_ALLOW_INTERNAL_OPERATIONAL_DATA=true 才會把經過清洗、只含
    # relevance_level/受影響船舶數/service 代碼/最近 ETA 小時數的
    # operational_context 摘要（不含船名、IMO、精確 ETA 時間戳、內部
    # 港口代碼以外的任何細節）附加進 LLM Prompt。
    allow_internal_operational_data: bool = False

    def redacted(self) -> dict:
        """供 log/診斷輸出使用，絕不包含實際金鑰內容（§八）。"""
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "anthropic_api_key_set": bool(self.anthropic_api_key),
            "openai_api_key_set": bool(self.openai_api_key),
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "failure_circuit_breaker": self.failure_circuit_breaker,
            "allow_internal_operational_data": self.allow_internal_operational_data,
        }


def load_llm_config(rules: dict | None = None) -> LLMConfig:
    rules = rules or load_llm_rules()
    reliability = rules.get("reliability", {})

    provider = os.environ.get("LLM_PROVIDER", "disabled").strip().lower()
    enabled = _bool_env("LLM_ENABLED", False) and provider != "disabled"

    return LLMConfig(
        enabled=enabled,
        provider=provider if enabled else "disabled",
        model=os.environ.get("LLM_MODEL", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        timeout_seconds=int(os.environ.get(
            "LLM_TIMEOUT_SECONDS", reliability.get("timeout_seconds", 30))),
        max_retries=int(os.environ.get(
            "LLM_MAX_RETRIES", reliability.get("max_retries", 2))),
        failure_circuit_breaker=int(os.environ.get(
            "LLM_FAILURE_CIRCUIT_BREAKER", reliability.get("failure_circuit_breaker", 3))),
        allow_internal_operational_data=_bool_env("LLM_ALLOW_INTERNAL_OPERATIONAL_DATA", False),
    )
