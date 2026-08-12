#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analysis_validator.py
海事航運新聞監控系統 — Phase 5 §二十九〜三十四、六十一〜六十二 Output Validation

職責：
  把 LLM 回傳、已經 json.loads() 成功的 dict，驗證/轉換成
  IntelligenceAnalysis dataclass。任何一項檢查沒過，一律拋出
  AnalysisValidationError —— 呼叫端（intelligence_analyzer.py）
  必須接住並 fallback 回 Phase 4 Rule-Based Summary，絕不能把
  未經驗證的內容直接送進 Email。

  檢查項目：
    - 必要欄位存在、型別正確
    - analysis_confidence 為合法 enum
    - 各文字欄位長度上限（llm_rules.json.output_limits）
    - 所有 source_ids 引用都必須存在於這次呼叫提供的 valid_source_ids
      （§六十二：引用不存在的 source_id → INVALID → Fallback）
    - Own Fleet 事件：文字中不得出現「捏造公司已採取行動」的字樣
      （§三十二）
    - monitoring_points（以及所有文字欄位保險起見）不得出現對船舶/船長
      下達操作指令的字樣（§三十三〜三十四）
    - 清單類欄位依 output_limits 的上限裁切（不是拒絕整包，只是不顯示
      超過上限的部分，見 §八十九：Timeline 過長時只顯示前幾個）

  ★ 本模組完全不知道任何 event 的 priority/confidence 等 deterministic
    欄位該是什麼值 —— 這裡不做風險判斷，只做「LLM 有沒有老實照 schema
    回答、有沒有偷渡不該講的話」的把關（§三十）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class AnalysisValidationError(Exception):
    """驗證失敗時拋出，呼叫端必須 fallback 回 Rule-Based Summary。"""
    pass


_VALID_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}

_REQUIRED_STRING_FIELDS = (
    "management_summary_zh", "why_it_matters_zh", "what_changed_zh", "analysis_confidence",
)
_REQUIRED_LIST_FIELDS = (
    "timeline", "confirmed_facts", "unconfirmed_claims", "contradictions",
    "monitoring_points", "source_support",
)


@dataclass
class IntelligenceAnalysis:
    management_summary_zh: str
    why_it_matters_zh: str
    what_changed_zh: str
    timeline: list = field(default_factory=list)
    confirmed_facts: list = field(default_factory=list)
    unconfirmed_claims: list = field(default_factory=list)
    contradictions: list = field(default_factory=list)
    monitoring_points: list = field(default_factory=list)
    source_support: list = field(default_factory=list)
    analysis_confidence: str = "LOW"


def _collect_source_id_refs(raw: dict) -> set:
    refs = set()
    for item in raw.get("timeline", []) or []:
        if isinstance(item, dict):
            refs.update(item.get("source_ids", []) or [])
    for item in raw.get("confirmed_facts", []) or []:
        if isinstance(item, dict):
            refs.update(item.get("source_ids", []) or [])
    for item in raw.get("unconfirmed_claims", []) or []:
        if isinstance(item, dict):
            refs.update(item.get("source_ids", []) or [])
    for item in raw.get("contradictions", []) or []:
        if isinstance(item, dict):
            for key in ("source_a", "source_b"):
                if item.get(key):
                    refs.add(item[key])
    refs.update(raw.get("source_support", []) or [])
    return refs


def _text_fields_for_scan(raw: dict) -> list:
    texts = [
        raw.get("management_summary_zh", ""),
        raw.get("why_it_matters_zh", ""),
        raw.get("what_changed_zh", ""),
    ]
    texts.extend(str(mp) for mp in raw.get("monitoring_points", []) or [])
    return [t for t in texts if isinstance(t, str)]


def validate_analysis(raw: dict, valid_source_ids: set, own_fleet: bool,
                       llm_rules: dict) -> IntelligenceAnalysis:
    if not isinstance(raw, dict):
        raise AnalysisValidationError("top-level response is not a JSON object")

    # ── 必要欄位存在 + 型別 ──────────────────────────────────────
    for key in _REQUIRED_STRING_FIELDS:
        if key not in raw or not isinstance(raw[key], str):
            raise AnalysisValidationError(f"missing or invalid string field: {key}")
    for key in _REQUIRED_LIST_FIELDS:
        if key not in raw or not isinstance(raw[key], list):
            raise AnalysisValidationError(f"missing or invalid list field: {key}")

    if raw["analysis_confidence"] not in _VALID_CONFIDENCE:
        raise AnalysisValidationError(
            f"analysis_confidence must be one of {_VALID_CONFIDENCE}, got {raw['analysis_confidence']!r}"
        )

    limits = llm_rules.get("output_limits", {})

    def _clip(text: str, max_chars: int) -> str:
        return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"

    management_summary_zh = _clip(raw["management_summary_zh"],
                                   limits.get("management_summary_zh_max_chars", 200))
    why_it_matters_zh = _clip(raw["why_it_matters_zh"],
                               limits.get("why_it_matters_zh_max_chars", 200))
    what_changed_zh = _clip(raw["what_changed_zh"],
                             limits.get("what_changed_zh_max_chars", 200))

    # ── Source ID 引用驗證（§六十二：任何未知 ID → 整包 INVALID）────
    referenced_ids = _collect_source_id_refs(raw)
    unknown_ids = referenced_ids - set(valid_source_ids)
    if unknown_ids:
        raise AnalysisValidationError(
            f"response references unknown source_id(s): {sorted(unknown_ids)}"
        )

    # ── Own Fleet Guardrail（§三十二）────────────────────────────
    if own_fleet:
        forbidden = llm_rules.get("own_fleet_guardrail", {}).get(
            "forbidden_invented_action_phrases_zh", [])
        for text in _text_fields_for_scan(raw):
            for phrase in forbidden:
                if phrase and phrase in text:
                    raise AnalysisValidationError(
                        f"own-fleet guardrail violation: forbidden phrase {phrase!r} found in AI output"
                    )

    # ── Monitoring Points 不得是操作指令（§三十三〜三十四）──────────
    forbidden_commands = llm_rules.get("forbidden_operational_command_phrases_zh", [])
    for mp in raw.get("monitoring_points", []) or []:
        mp_text = str(mp)
        for phrase in forbidden_commands:
            if phrase and phrase in mp_text:
                raise AnalysisValidationError(
                    f"monitoring_points contains operational command phrase {phrase!r}"
                )

    # ── 清單裁切（不拒絕，只是不顯示超過上限的部分，§八十九）────────
    timeline = list(raw.get("timeline", []))[: limits.get("max_timeline_items", 5)]
    confirmed_facts = list(raw.get("confirmed_facts", []))[: limits.get("max_confirmed_facts", 8)]
    unconfirmed_claims = list(raw.get("unconfirmed_claims", []))[: limits.get("max_unconfirmed_claims", 8)]
    contradictions = list(raw.get("contradictions", []))[: limits.get("max_contradictions", 5)]
    monitoring_points = list(raw.get("monitoring_points", []))[: limits.get("max_monitoring_points", 5)]

    return IntelligenceAnalysis(
        management_summary_zh=management_summary_zh,
        why_it_matters_zh=why_it_matters_zh,
        what_changed_zh=what_changed_zh,
        timeline=timeline,
        confirmed_facts=confirmed_facts,
        unconfirmed_claims=unconfirmed_claims,
        contradictions=contradictions,
        monitoring_points=monitoring_points,
        source_support=list(raw.get("source_support", [])),
        analysis_confidence=raw["analysis_confidence"],
    )
