#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm_provider.py
海事航運新聞監控系統 — Phase 5 §四 Provider Abstraction

職責：
  定義 LLMProvider 介面，讓 intelligence_analyzer.py 完全不需要知道
  背後是 Claude、OpenAI，還是根本沒有啟用 LLM。

★ 不得把任何 API Key / Authorization header / 完整 request headers
  印到 log 或例外訊息（§八）。所有 provider 的錯誤處理都刻意只保留
  「錯誤分類」（timeout/rate_limit/server_error/invalid_response/...），
  不回傳/記錄底層 exception 的完整內容（可能夾帶敏感 header）。

★ 本檔案不呼叫任何 web search / retrieval —— LLM 只分析我們自己
  scraper 已收集、由 source_grounding.py 準備好的資料（§十五）。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMRawResponse:
    """
    Provider 呼叫的原始結果。intelligence_analyzer.py 負責把 raw_text
    parse 成 JSON 並驗證 —— provider 本身不做 JSON 驗證。
    """
    success: bool
    raw_text: Optional[str] = None
    error: Optional[str] = None          # 人類可讀的簡短錯誤描述，不含敏感資訊
    error_type: Optional[str] = None     # timeout / rate_limit / server_error /
                                          # invalid_response / auth_error / unknown
    usage: dict = field(default_factory=dict)   # 例如 {"input_tokens":.., "output_tokens":..}
    retryable: bool = False


class LLMProvider(ABC):
    """所有 Provider（含 Disabled/Fake）共同介面。"""

    @abstractmethod
    def analyze(self, system_prompt: str, user_payload: str, timeout_seconds: int) -> LLMRawResponse:
        """呼叫底層模型，回傳原始文字回應（預期是 JSON 字串）。"""
        raise NotImplementedError


# ══════════════════════════════════════════════════════════════
# Disabled Provider — LLM_ENABLED=false 時的預設
# ══════════════════════════════════════════════════════════════
class DisabledProvider(LLMProvider):
    def analyze(self, system_prompt: str, user_payload: str, timeout_seconds: int) -> LLMRawResponse:
        return LLMRawResponse(success=False, error="LLM disabled", error_type="disabled", retryable=False)


# ══════════════════════════════════════════════════════════════
# Claude Provider
# ══════════════════════════════════════════════════════════════
class ClaudeProvider(LLMProvider):
    def __init__(self, model: str, api_key: str, max_retries: int = 2):
        self.model = model or "claude-sonnet-5"
        self._api_key = api_key
        self.max_retries = max_retries
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic  # 延遲載入：LLM_ENABLED=false 時完全不需要這個套件
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def analyze(self, system_prompt: str, user_payload: str, timeout_seconds: int) -> LLMRawResponse:
        try:
            client = self._get_client()
        except ImportError:
            logger.error("❌ 未安裝 anthropic SDK，無法使用 ClaudeProvider")
            return LLMRawResponse(success=False, error="anthropic SDK not installed",
                                   error_type="unavailable", retryable=False)
        except Exception:
            logger.error("❌ ClaudeProvider 初始化失敗（詳情省略，避免洩漏設定資訊）")
            return LLMRawResponse(success=False, error="client init failed",
                                   error_type="unknown", retryable=False)

        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_payload}],
                timeout=timeout_seconds,
            )
            text = "".join(
                block.text for block in resp.content if getattr(block, "type", "") == "text"
            )
            usage = {}
            if getattr(resp, "usage", None) is not None:
                usage = {
                    "input_tokens": getattr(resp.usage, "input_tokens", None),
                    "output_tokens": getattr(resp.usage, "output_tokens", None),
                }
            return LLMRawResponse(success=True, raw_text=text, usage=usage)
        except Exception as e:
            return _classify_provider_exception(e)


# ══════════════════════════════════════════════════════════════
# OpenAI Provider
# ══════════════════════════════════════════════════════════════
class OpenAIProvider(LLMProvider):
    def __init__(self, model: str, api_key: str, max_retries: int = 2):
        self.model = model or "gpt-4o-mini"
        self._api_key = api_key
        self.max_retries = max_retries
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai  # 延遲載入
            self._client = openai.OpenAI(api_key=self._api_key)
        return self._client

    def analyze(self, system_prompt: str, user_payload: str, timeout_seconds: int) -> LLMRawResponse:
        try:
            client = self._get_client()
        except ImportError:
            logger.error("❌ 未安裝 openai SDK，無法使用 OpenAIProvider")
            return LLMRawResponse(success=False, error="openai SDK not installed",
                                   error_type="unavailable", retryable=False)
        except Exception:
            logger.error("❌ OpenAIProvider 初始化失敗（詳情省略，避免洩漏設定資訊）")
            return LLMRawResponse(success=False, error="client init failed",
                                   error_type="unknown", retryable=False)

        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_payload},
                ],
                timeout=timeout_seconds,
            )
            text = resp.choices[0].message.content or ""
            usage = {}
            if getattr(resp, "usage", None) is not None:
                usage = {
                    "input_tokens": getattr(resp.usage, "prompt_tokens", None),
                    "output_tokens": getattr(resp.usage, "completion_tokens", None),
                }
            return LLMRawResponse(success=True, raw_text=text, usage=usage)
        except Exception as e:
            return _classify_provider_exception(e)


def _classify_provider_exception(e: Exception) -> LLMRawResponse:
    """
    把 SDK 例外分類成 error_type，不記錄任何底層例外的完整內容
    （可能包含 Authorization header），只取用類別名稱做粗略判斷。
    """
    name = type(e).__name__.lower()
    msg = str(e).lower()

    if "timeout" in name or "timeout" in msg:
        return LLMRawResponse(success=False, error="request timed out",
                               error_type="timeout", retryable=True)
    if "ratelimit" in name or "429" in msg or "rate limit" in msg:
        return LLMRawResponse(success=False, error="rate limited",
                               error_type="rate_limit", retryable=True)
    if "authenticat" in name or "permission" in name or "401" in msg or "403" in msg:
        return LLMRawResponse(success=False, error="authentication/permission error",
                               error_type="auth_error", retryable=False)
    if any(code in msg for code in ("500", "502", "503", "504")) or "internalservererror" in name:
        return LLMRawResponse(success=False, error="provider server error",
                               error_type="server_error", retryable=True)
    return LLMRawResponse(success=False, error="unexpected provider error",
                           error_type="unknown", retryable=False)


# ══════════════════════════════════════════════════════════════
# Fake Provider — 測試 / 本機 Preview 專用，絕不連線
# ══════════════════════════════════════════════════════════════
class FakeLLMProvider(LLMProvider):
    """
    測試與 preview_email.py 專用。不呼叫任何真實 API。

    mode 可選：
      "valid"               — 回傳 canned_json（或呼叫端注入的 dict）
      "invalid_json"        — 回傳無法 parse 的字串
      "timeout"              — 模擬 timeout 失敗（retryable）
      "rate_limit"            — 模擬 429（retryable）
      "server_error"           — 模擬 5xx（retryable）
      "hallucinated_source"     — 回傳合法 JSON 但引用不存在的 source_id
      "prompt_injection_probe"   — 回傳合法 JSON，用來驗證來源文字中的注入
                                    指令沒有被模型「執行」（本 Fake 本身
                                    永遠不會執行注入指令，用於測試
                                    Validator/Analyzer 的防護邏輯）
    """

    def __init__(self, mode: str = "valid", canned_json: Optional[dict] = None,
                 canned_text: Optional[str] = None):
        self.mode = mode
        self.canned_json = canned_json
        self.canned_text = canned_text
        self.call_count = 0

    def analyze(self, system_prompt: str, user_payload: str, timeout_seconds: int) -> LLMRawResponse:
        self.call_count += 1

        if self.mode in ("timeout",):
            return LLMRawResponse(success=False, error="fake timeout",
                                   error_type="timeout", retryable=True)
        if self.mode in ("rate_limit",):
            return LLMRawResponse(success=False, error="fake rate limit",
                                   error_type="rate_limit", retryable=True)
        if self.mode in ("server_error",):
            return LLMRawResponse(success=False, error="fake server error",
                                   error_type="server_error", retryable=True)
        if self.mode == "invalid_json":
            return LLMRawResponse(success=True, raw_text="not valid json {{{")

        if self.canned_text is not None:
            return LLMRawResponse(success=True, raw_text=self.canned_text)

        import json as _json
        payload = self.canned_json or _default_canned_analysis()
        return LLMRawResponse(success=True, raw_text=_json.dumps(payload, ensure_ascii=False))


def _default_canned_analysis() -> dict:
    return {
        "management_summary_zh": "一艘商船於紅海南部航行期間遭疑似攻擊，船體受損，目前尚無可靠來源證實人員傷亡。",
        "why_it_matters_zh": "紅海為重要商船航線，此類攻擊事件可能提高航行風險與 War Risk exposure，需持續關注是否影響航線調度。",
        "what_changed_zh": "",
        "timeline": [],
        "confirmed_facts": [{"fact": "事件發生於紅海南部", "source_ids": ["S1"]}],
        "unconfirmed_claims": [],
        "contradictions": [],
        "monitoring_points": ["持續關注官方是否發布航行警告"],
        "source_support": ["S1"],
        "analysis_confidence": "MEDIUM",
    }


# ══════════════════════════════════════════════════════════════
# Provider 工廠
# ══════════════════════════════════════════════════════════════
def build_provider(config) -> LLMProvider:
    """
    config: llm_config.LLMConfig。啟用開關已經在 LLMConfig.enabled 判斷過
    （§五：LLM_ENABLED=false 時 provider 恆為 "disabled"）。
    """
    if not config.enabled:
        return DisabledProvider()
    if config.provider == "claude":
        return ClaudeProvider(model=config.model, api_key=config.anthropic_api_key,
                               max_retries=config.max_retries)
    if config.provider == "openai":
        return OpenAIProvider(model=config.model, api_key=config.openai_api_key,
                               max_retries=config.max_retries)
    logger.warning(f"⚠️  未知的 LLM_PROVIDER={config.provider!r}，回退為 DisabledProvider")
    return DisabledProvider()
