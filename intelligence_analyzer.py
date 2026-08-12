#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intelligence_analyzer.py
海事航運新聞監控系統 — Phase 5 §五十一〜五十七 Orchestration

職責：
  串起 Phase 5 的完整流程：

    Eligible? → Cache? → Build Grounded Input → Provider.analyze()
    → Parse JSON → Validate → (成功: cache 寫入 + 回傳)
                             → (任何一步失敗: fallback rule-based)

  ★ LLM 是 Non-Critical Dependency（§六）：任何一個環節失敗，都只是讓
    這一個 Event 拿不到 AI Enhancement，絕不能讓整個 pipeline 或 Email
    發送失敗。呼叫端（maritime_news.py）永遠可以安全地把
    analyze_events() 的結果當成「可能是 None」來處理。

  ★ Circuit Breaker（§五十四）：同一次 run 連續失敗達到門檻後，後續
    事件直接跳過 provider 呼叫，避免對一個已經掛掉的 API 重複重試。

  ★ Console Diagnostics（§九十）：只印統計數字，絕不印 prompt 內容、
    來源全文，或任何 API 金鑰／header。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from analysis_validator import IntelligenceAnalysis, AnalysisValidationError, validate_analysis
from llm_provider import LLMProvider, DisabledProvider
from source_grounding import (
    build_grounded_input, build_user_payload, source_fingerprint, sanitize_operational_context,
)
from ai_cache import make_cache_key

logger = logging.getLogger(__name__)

_MD_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_markdown_fence(text: str) -> str:
    """
    §八十三：Prompt 已明確要求不要用 markdown code fence，但模型偶爾
    還是會加。這裡做最基本的容錯 stripping，不改變 JSON 本身內容。
    """
    if not text:
        return text
    t = text.strip()
    if t.startswith("```"):
        t = _MD_FENCE_RE.sub("", t).strip()
    return t


def load_system_prompt(prompt_file: str) -> str:
    p = Path(prompt_file)
    if not p.exists():
        p = Path(__file__).parent / prompt_file
    if not p.exists():
        raise FileNotFoundError(f"LLM system prompt file not found: {prompt_file}")
    return p.read_text(encoding="utf-8")


class IntelligenceAnalyzer:
    def __init__(self, llm_config, llm_rules: dict, provider: LLMProvider, cache,
                 own_fleet_carrier_key: Optional[str] = None,
                 system_prompt: Optional[str] = None,
                 sleep_fn=None):
        self.config = llm_config
        self.rules = llm_rules
        self.provider = provider
        self.cache = cache
        self.own_fleet_carrier_key = (
            own_fleet_carrier_key
            or llm_rules.get("own_fleet_guardrail", {}).get("carrier_key", "WAN_HAI")
        )
        self.system_prompt = system_prompt or self._load_prompt_safely()
        self._sleep_fn = sleep_fn or (lambda _seconds: None)

        # ── Circuit Breaker 狀態（每次 run 建立新的 Analyzer 實例即重置）──
        self._consecutive_failures = 0
        self._circuit_open = False

        # ── Console Diagnostics（§九十）──────────────────────────────
        self.diagnostics = {
            "eligible_events": 0,
            "cache_hits": 0,
            "llm_calls": 0,
            "successful": 0,
            "fallback": 0,
            "contradictions_detected": 0,
            "enhanced_summaries": 0,
            "rule_based_summaries": 0,
        }

    def _load_prompt_safely(self) -> str:
        try:
            return load_system_prompt(self.rules.get("prompt_file", "prompts/maritime_intelligence_v5.txt"))
        except FileNotFoundError:
            logger.warning("⚠️  找不到 LLM system prompt 檔案，LLM Enhancement 本次 run 將全部 fallback")
            return ""

    # ══════════════════════════════════════════════════════════
    # Eligibility（§九、llm_rules.json eligibility 區塊）
    # ══════════════════════════════════════════════════════════
    def is_eligible(self, event) -> bool:
        elig = self.rules.get("eligibility", {})
        priority = event.management_priority
        state = event.notification_state

        if priority in elig.get("never_analyze_priorities", []):
            return False
        if state in elig.get("never_analyze_notification_states", []):
            return False

        if state == "RESOLVED_UPDATE":
            return priority in elig.get("resolved_update_priorities", [])

        for rule in elig.get("always_analyze", []):
            if priority == rule.get("priority") and state in rule.get("notification_state", []):
                return True

        if (priority == elig.get("optional_priority")
                and state in elig.get("optional_notification_state", [])):
            if event.event_type in elig.get("optional_event_types", []):
                return True
            threshold = elig.get("optional_fleet_relevance_threshold", 15)
            if (event.fleet_relevance_score or 0) >= threshold:
                return True

        return False

    def is_own_fleet(self, event) -> bool:
        return bool(event.carrier and event.carrier == self.own_fleet_carrier_key)

    # ══════════════════════════════════════════════════════════
    # 單一事件分析
    # ══════════════════════════════════════════════════════════
    def analyze_event(self, event,
                       operational_relevance=None) -> tuple[Optional[IntelligenceAnalysis], str]:
        """
        operational_relevance：Phase 6 OperationalRelevance（可選）。只有
        在 self.config.allow_internal_operational_data 為 True 時，才會
        把它清洗過的摘要（見 source_grounding.sanitize_operational_context）
        附加進送給 LLM 的 payload；預設（False）完全不夾帶，行為與沒有
        Phase 6 時一致（§九十一〜九十三）。
        """
        if not self.config.enabled or isinstance(self.provider, DisabledProvider):
            return None, "disabled"

        if not self.is_eligible(event):
            return None, "not_eligible"

        self.diagnostics["eligible_events"] += 1

        if self._circuit_open:
            self.diagnostics["fallback"] += 1
            return None, "circuit_open"

        operational_context = None
        if self.config.allow_internal_operational_data and operational_relevance is not None:
            operational_context = sanitize_operational_context(operational_relevance)

        package = build_grounded_input(event, self.rules, operational_context=operational_context)
        fingerprint = source_fingerprint(package.sources)
        cache_key = make_cache_key(
            event.event_id, event.version, fingerprint,
            self.rules.get("prompt_version", "unknown"), self.config.model,
        )

        cached = self.cache.get(cache_key)
        if cached and cached.get("status") == "success" and cached.get("analysis_json"):
            self.diagnostics["cache_hits"] += 1
            self.diagnostics["enhanced_summaries"] += 1
            try:
                valid_ids = {s.source_id for s in package.sources}
                analysis = validate_analysis(cached["analysis_json"], valid_ids,
                                              self.is_own_fleet(event), self.rules)
                return analysis, "cache_hit"
            except AnalysisValidationError:
                # 快取內容本身驗證不過（理論上不該發生，防禦性處理）：當成 miss 重跑。
                pass

        if not package.sources:
            self.diagnostics["fallback"] += 1
            return None, "fallback_no_sources"

        user_payload = build_user_payload(package)
        raw = self._call_with_retry(user_payload)
        self.diagnostics["llm_calls"] += 1

        if not raw.success:
            self._register_failure()
            self.cache.put(cache_key, event.event_id, event.version, self.config.provider,
                            self.config.model, self.rules.get("prompt_version", "unknown"),
                            fingerprint, None, status=f"failed_{raw.error_type or 'unknown'}")
            self.diagnostics["fallback"] += 1
            return None, f"fallback_{raw.error_type or 'error'}"

        parsed_text = _strip_markdown_fence(raw.raw_text or "")
        try:
            parsed = json.loads(parsed_text)
        except json.JSONDecodeError:
            self._register_failure()
            self.cache.put(cache_key, event.event_id, event.version, self.config.provider,
                            self.config.model, self.rules.get("prompt_version", "unknown"),
                            fingerprint, None, status="invalid_json")
            self.diagnostics["fallback"] += 1
            return None, "fallback_invalid_json"

        try:
            valid_ids = {s.source_id for s in package.sources}
            analysis = validate_analysis(parsed, valid_ids, self.is_own_fleet(event), self.rules)
        except AnalysisValidationError as e:
            logger.warning(f"⚠️  Event {event.event_id} AI 分析驗證失敗，fallback rule-based：{e}")
            self._register_failure()
            self.cache.put(cache_key, event.event_id, event.version, self.config.provider,
                            self.config.model, self.rules.get("prompt_version", "unknown"),
                            fingerprint, None, status="invalid_schema")
            self.diagnostics["fallback"] += 1
            return None, "fallback_invalid_schema"

        self._register_success()
        self.cache.put(cache_key, event.event_id, event.version, self.config.provider,
                        self.config.model, self.rules.get("prompt_version", "unknown"),
                        fingerprint, _analysis_to_dict(analysis), status="success")
        self.diagnostics["successful"] += 1
        self.diagnostics["enhanced_summaries"] += 1
        if analysis.contradictions:
            self.diagnostics["contradictions_detected"] += len(analysis.contradictions)
        return analysis, "success"

    def _call_with_retry(self, user_payload: str):
        max_retries = max(1, self.config.max_retries)
        last = None
        for attempt in range(1, max_retries + 1):
            last = self.provider.analyze(self.system_prompt, user_payload, self.config.timeout_seconds)
            if last.success or not last.retryable:
                return last
            if attempt < max_retries:
                self._sleep_fn(0.5 * attempt)
        return last

    def _register_failure(self):
        self._consecutive_failures += 1
        threshold = self.config.failure_circuit_breaker
        if self._consecutive_failures >= threshold and not self._circuit_open:
            self._circuit_open = True
            logger.warning(
                f"⚠️  LLM provider temporarily disabled for current run after "
                f"{self._consecutive_failures} consecutive failures. Using rule-based fallback."
            )

    def _register_success(self):
        self._consecutive_failures = 0

    # ══════════════════════════════════════════════════════════
    # 批次分析（含 max_events_per_run 成本上限）
    # ══════════════════════════════════════════════════════════
    def analyze_events(self, events_in_priority_order: list,
                        operational_relevance_map: Optional[dict] = None) -> dict:
        """
        輸入已經依 Priority 排序好的事件列表（例如 immediate+watch+industry
        依序串接），回傳 {event_id: (IntelligenceAnalysis|None, status)}。

        只有前 max_events_per_run 個「eligible」事件會真的送進 LLM，
        其餘（不論 eligible 與否）一律 rule-based（§十 Cost Control）。

        operational_relevance_map：{event_id: OperationalRelevance}（可選，
        Phase 6）。是否真的會被送進 LLM 仍取決於
        config.allow_internal_operational_data（見 analyze_event()）。
        """
        results: dict = {}
        operational_relevance_map = operational_relevance_map or {}
        max_events = self.rules.get("cost_controls", {}).get("max_events_per_run", 8)
        analyzed_count = 0

        for event in events_in_priority_order:
            if not self.config.enabled:
                results[event.event_id] = (None, "disabled")
                self.diagnostics["rule_based_summaries"] += 1
                continue

            if not self.is_eligible(event):
                results[event.event_id] = (None, "not_eligible")
                self.diagnostics["rule_based_summaries"] += 1
                continue

            if analyzed_count >= max_events:
                results[event.event_id] = (None, "fallback_cost_limit")
                self.diagnostics["fallback"] += 1
                self.diagnostics["rule_based_summaries"] += 1
                continue

            analysis, status = self.analyze_event(
                event, operational_relevance=operational_relevance_map.get(event.event_id)
            )
            results[event.event_id] = (analysis, status)
            analyzed_count += 1
            if analysis is None:
                self.diagnostics["rule_based_summaries"] += 1

        return results

    def diagnostics_report(self) -> str:
        d = self.diagnostics
        return (
            "LLM INTELLIGENCE ENHANCEMENT\n"
            f"  Eligible events: {d['eligible_events']}\n"
            f"  Cache hits: {d['cache_hits']}\n"
            f"  LLM calls: {d['llm_calls']}\n"
            f"  Successful: {d['successful']}\n"
            f"  Fallback: {d['fallback']}\n"
            f"  Contradictions detected: {d['contradictions_detected']}\n"
            f"  Enhanced summaries: {d['enhanced_summaries']}\n"
            f"  Rule-based summaries: {d['rule_based_summaries']}"
        )


def _analysis_to_dict(analysis: IntelligenceAnalysis) -> dict:
    return {
        "management_summary_zh": analysis.management_summary_zh,
        "why_it_matters_zh": analysis.why_it_matters_zh,
        "what_changed_zh": analysis.what_changed_zh,
        "timeline": analysis.timeline,
        "confirmed_facts": analysis.confirmed_facts,
        "unconfirmed_claims": analysis.unconfirmed_claims,
        "contradictions": analysis.contradictions,
        "monitoring_points": analysis.monitoring_points,
        "source_support": analysis.source_support,
        "analysis_confidence": analysis.analysis_confidence,
    }
