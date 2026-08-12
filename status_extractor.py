#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
status_extractor.py
海事航運新聞監控系統 — Phase 3 §二十九〜三十 Rule-Based Operational Status Extraction

無 LLM。只用 keyword / regex（讀自 memory_rules.json 的 status_keywords）。
沒有明確證據一律回傳 None，不得猜測數字或狀態（§三十）。
"""

from __future__ import annotations

import re
from typing import Optional

from event_extractor import normalize_title

# 英文數字詞（只涵蓋常見小數字，模糊或超出範圍一律不猜）
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "a": 1, "an": 1,
}

_CREW_INJURED_RE = re.compile(
    r'(\d+|one|two|three|four|five|six|seven|eight|nine|ten|a|an)\s+'
    r'(?:crew\s*(?:member)?s?|seafarers?|sailors?)\s+(?:were\s+|was\s+)?'
    r'(?:seriously\s+|badly\s+|critically\s+|slightly\s+)?injured',
    re.IGNORECASE,
)
_CREW_FATALITY_RE = re.compile(
    r'(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+'
    r'(?:crew\s*(?:member)?s?|seafarers?|sailors?|fatalit(?:y|ies))'
    r'(?:\s+(?:were\s+|was\s+)?(?:confirmed\s+)?(?:dead|killed))?',
    re.IGNORECASE,
)
_CREW_MISSING_RE = re.compile(
    r'(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+'
    r'(?:crew\s*(?:member)?s?|seafarers?|sailors?)\s+(?:(?:are|is|were|was)\s+)?missing',
    re.IGNORECASE,
)


def _to_int(token: str) -> Optional[int]:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return _WORD_NUMBERS.get(token)


class StatusExtractor:
    """
    輸入 title+summary，輸出一組 operational status 欄位（缺證據為 None）。
    字典結構：memory_rules["status_keywords"][category][state] = {"en": [...], "zh": [...]}
    """

    def __init__(self, memory_rules: dict):
        self.rules = memory_rules
        self._lookup: dict[str, list[tuple[str, str]]] = {}
        for category, states in memory_rules.get("status_keywords", {}).items():
            if category.startswith("_") or not isinstance(states, dict):
                continue
            entries: list[tuple[str, str]] = []
            for state, langs in states.items():
                if state.startswith("_") or not isinstance(langs, dict):
                    continue
                for kw in langs.get("en", []) + langs.get("zh", []):
                    entries.append((kw.lower(), state))
            entries.sort(key=lambda x: -len(x[0]))
            self._lookup[category] = entries

    def _match_category(self, text_lower: str, category: str) -> Optional[str]:
        for kw, state in self._lookup.get(category, []):
            if kw in text_lower:
                return state
        return None

    def extract(self, title: str, summary: str) -> dict:
        text_lower = f"{title or ''} {summary or ''}".lower()
        text_norm = normalize_title(f"{title or ''} {summary or ''}")

        result = {
            "vessel_status":      self._match_category(text_lower, "vessel_status"),
            "fire_status":        self._match_category(text_lower, "fire_status"),
            "casualty_status":    self._match_category(text_lower, "casualty_status"),
            "pollution_status":   self._match_category(text_lower, "pollution_status"),
            "port_status":        self._match_category(text_lower, "port_status"),
            "navigation_status":  self._match_category(text_lower, "navigation_status"),
            "operational_status": self._match_category(text_lower, "operational_status"),
            "crew_injured":       None,
            "crew_fatalities":    None,
            "crew_missing":       None,
        }

        m = _CREW_INJURED_RE.search(text_norm)
        if m:
            result["crew_injured"] = _to_int(m.group(1))
            if result["casualty_status"] is None:
                result["casualty_status"] = "INJURED"

        m = _CREW_FATALITY_RE.search(text_norm)
        if m and ("dead" in text_lower or "killed" in text_lower
                  or "fatalit" in text_lower or "死亡" in (title or "") + (summary or "")
                  or "罹難" in (title or "") + (summary or "") or "遇难" in (title or "") + (summary or "")):
            result["crew_fatalities"] = _to_int(m.group(1))
            result["casualty_status"] = "FATALITY"

        m = _CREW_MISSING_RE.search(text_norm)
        if m:
            result["crew_missing"] = _to_int(m.group(1))
            if result["casualty_status"] != "FATALITY":
                result["casualty_status"] = "MISSING"

        return result
