#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
event_extractor.py  v1.1
海事航運新聞監控系統 — Event Extraction（Phase 2 + Phase 2.1 alias hardening）

職責：
  1. normalize_title()  — 標題正規化（供 EventClusterer 使用）
  2. EventExtractor      — 用 regex / dictionary 從 title+summary 抽取：
       vessel_type / carrier / sea_area(+location 顯示名) / event_type /
       incident_subtype（Phase 2.1 新增，更細緻的事故子類型）

v1.1（Phase 2.1）修正重點：
  - ★ carrier / vessel_type / sea_area 一律改用「有邊界」的比對，不再是
    naive substring `in` 檢查。這修掉一個實測發現的真 bug：舊版邏輯下
    "PIL" 會誤判命中 "the pilot boarded the vessel" 裡的 "pilot"。
  - 英文/純 ASCII 別名 → 用 `\\b...\\b` word-boundary regex（不分大小寫）。
  - 中文別名 → 中文沒有空白分詞，word-boundary 對 CJK 字元幾乎恆為
    no-op（`\\w` 會把每個中文字都當成獨立 word char，導致「萬海」中
    「海」後面接著「貨」時抓不到邊界），所以中文別名仍用 substring 比對，
    但這對「萬海」這種語意已經很明確的中文詞组，誤判風險本來就低很多。
  - ★ "ONE"（Ocean Network Express 簡稱）是已知風險最高的別名：裸字
    "one" 是最常見的英文單字之一。改用專用規則：只有 (a) 出現完整名稱
    "Ocean Network Express"，或 (b) 原文（非小寫化）出現大小寫完全相符
    的 "ONE"（用 `\\bONE\\b`，區分大小寫），或 (c) "ONE" 緊鄰 shipping
    context word（line/container/vessel/shipping/news）才算命中。
    "One crew member was injured" 這種句首大寫、非全大寫的 "One"
    不會被誤判為航商。
  - EVERGREEN 同樣稽核過：裸字 "evergreen" 是常見英文詞（evergreen
    fund/strategy/tree），已從別名清單移除，只保留
    "evergreen marine"/"evergreen line" 這種無歧義片語與中文別名。
"""

from __future__ import annotations

import re
import html as _html_module
from typing import Optional

from risk_config import load_risk_rules
from models import NewsArticle, EventType

# ══════════════════════════════════════════════════════════════
# 標題 Normalization（§十八）
# ══════════════════════════════════════════════════════════════
# 只合併「同一個概念的不同寫法」，絕不能把不同事故類型混在一起
# （例如 collision 不可以 normalize 成 grounding）。
_SYNONYM_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\bcontainerships?\b'),        'container ship'),
    (re.compile(r'\bcontainer\s+vessels?\b'),   'container ship'),
    (re.compile(r'\bbox\s*ships?\b'),           'container ship'),
    (re.compile(r'\bboxships?\b'),              'container ship'),
    (re.compile(r'\bcollides?\b'),              'collision'),
    (re.compile(r'\bcolliding\b'),              'collision'),
    (re.compile(r'\bgrounds?\b'),               'grounding'),
    (re.compile(r'\brun\s+aground\b'),          'grounding'),
    (re.compile(r'\bhijacks?\b'),               'hijacking'),
    (re.compile(r'\bhijacked\b'),               'hijacking'),
    (re.compile(r'\bcatches?\s+fire\b'),        'fire'),
    (re.compile(r'\bon\s+fire\b'),              'fire'),
]

_CJK_RE = re.compile(r'[一-鿿]')


def _is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def normalize_title(title: str) -> str:
    """
    標題正規化：HTML entity → 小寫 → 同義詞合併 → 去標點 → 壓縮空白。
    僅用於相似度比對（clustering），不用於顯示。
    """
    if not title:
        return ""
    t = _html_module.unescape(title)
    t = t.lower()
    for pattern, repl in _SYNONYM_PATTERNS:
        t = pattern.sub(repl, t)
    # 保留英數字、CJK 字元與空白，其餘標點去除
    t = re.sub(r'[^\w\s一-鿿]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _compile_alias_pattern(alias: str) -> re.Pattern:
    """
    ★ Phase 2.1 alias 比對核心修正。
    英文別名 → \\b word-boundary（不分大小寫），避免 "PIL" 誤中 "pilot"。
    中文別名 → 維持 substring（CJK 沒有天然詞界，\\b 在連續中文字之間
    幾乎不會產生邊界，用了反而抓不到「萬海貨櫃船」這種常見寫法）。
    """
    alias = alias.strip()
    if _is_cjk(alias):
        return re.compile(re.escape(alias))
    return re.compile(r'\b' + re.escape(alias) + r'\b', re.IGNORECASE)


# ══════════════════════════════════════════════════════════════
# EventExtractor
# ══════════════════════════════════════════════════════════════
class EventExtractor:
    """
    Rule-based extraction（regex + dictionary），無 LLM 依賴。
    字典（vessel_types / major_carriers / major_shipping_areas /
    event_type_keywords / incident_subtype_keywords）都讀自 risk_rules.json。
    """

    # 抓具名船名的 best-effort heuristic（非必測項目，抓不到就是 None）
    _VESSEL_NAME_PATTERNS = [
        re.compile(r'\bM[VTS]\s+([A-Z][A-Za-z0-9\-\.]{2,25}'
                   r'(?:\s+[A-Z][A-Za-z0-9\-\.]{1,20}){0,2})'),
        re.compile(r'"([A-Z][A-Za-z0-9\-\.\s]{2,30})"'),
    ]

    _ONE_BARE_PATTERN       = re.compile(r'\bONE\b')                  # 區分大小寫，故意不加 re.IGNORECASE
    _ONE_BARE_LOWER_PATTERN = re.compile(r'\bone\b', re.IGNORECASE)   # 只用來偵測「有嘗試但被拒絕」

    def __init__(self, rules: Optional[dict] = None):
        self.rules = rules or load_risk_rules()
        self._build_lookup_tables()
        # Phase 2.1 §二十三 診斷計數（供 console diagnostics 用，每個 pipeline
        # run 建議建立新的 EventExtractor 實例，計數才會對應到那一次 run）
        self.diagnostics = {"carrier_detected": 0, "carrier_ambiguous_rejected": 0}

    # ── 建立查表 ─────────────────────────────────────────────
    def _build_lookup_tables(self) -> None:
        # vessel types：(compiled_pattern, key, display)，依 alias 長度長→短排序
        self._vessel_type_lookup: list[tuple[re.Pattern, str, str]] = []
        for vt in self.rules.get("vessel_types", []):
            for alias in vt.get("aliases", []):
                self._vessel_type_lookup.append(
                    (_compile_alias_pattern(alias), vt["key"], vt.get("display", vt["key"]))
                )
        self._vessel_type_lookup.sort(key=lambda x: -len(x[0].pattern))

        # carriers
        self._carrier_lookup: list[tuple[re.Pattern, str, str]] = []
        self._own_fleet_keys: set[str] = set()
        self._carrier_display: dict[str, str] = {}
        self._carrier_configs: dict[str, dict] = {}
        for c in self.rules.get("major_carriers", []):
            self._carrier_display[c["key"]] = c.get("display", c["key"])
            self._carrier_configs[c["key"]] = c
            if c.get("is_own_fleet"):
                self._own_fleet_keys.add(c["key"])
            if c.get("special_matching") == "ONE_CARRIER_RULE":
                # ONE 走專用規則（見 _match_one_carrier），不進一般 lookup，
                # 但完整名稱 "ocean network express" 仍然無歧義，照常收錄。
                for alias in c.get("aliases", []):
                    self._carrier_lookup.append(
                        (_compile_alias_pattern(alias), c["key"], c.get("display", c["key"]))
                    )
                continue
            for alias in c.get("aliases", []):
                self._carrier_lookup.append(
                    (_compile_alias_pattern(alias), c["key"], c.get("display", c["key"]))
                )
        self._carrier_lookup.sort(key=lambda x: -len(x[0].pattern))

        # sea areas
        self._area_lookup: list[tuple[re.Pattern, str, str]] = []
        self._area_display: dict[str, str] = {}
        self._area_region_group: dict[str, Optional[str]] = {}
        for area in self.rules.get("major_shipping_areas", []):
            self._area_display[area["key"]] = area.get("display", area["key"])
            self._area_region_group[area["key"]] = area.get("region_group")
            aliases = area.get("aliases_en", []) + area.get("aliases_zh", [])
            for alias in aliases:
                self._area_lookup.append(
                    (_compile_alias_pattern(alias), area["key"], area.get("display", area["key"]))
                )
        self._area_lookup.sort(key=lambda x: -len(x[0].pattern))

        # event_type（粗分類，維持 substring 比對；英文關鍵詞多為片語，
        # substring 風險遠低於短別名，Phase 2.1 稽核範圍聚焦在 carrier alias）
        self._event_type_lookup: list[tuple[str, str]] = []
        for et, langs in self.rules.get("event_type_keywords", {}).items():
            for kw in langs.get("en", []) + langs.get("zh", []):
                self._event_type_lookup.append((kw.lower(), et))
        self._event_type_lookup.sort(key=lambda x: -len(x[0]))
        self._event_type_priority = self.rules.get(
            "event_type_priority_order", list(EventType.ALL)
        )
        self._legacy_map = self.rules.get("legacy_category_to_event_type", {})

        # incident_subtype（Phase 2.1 新增，細分類，供 clustering 使用）
        self._subtype_lookup: list[tuple[str, str]] = []
        for subtype, langs in self.rules.get("incident_subtype_keywords", {}).items():
            if subtype.startswith("_") or not isinstance(langs, dict):
                continue
            for kw in langs.get("en", []) + langs.get("zh", []):
                self._subtype_lookup.append((kw.lower(), subtype))
        self._subtype_lookup.sort(key=lambda x: -len(x[0]))
        self._subtype_priority = self.rules.get(
            "incident_subtype_priority_order",
            list(self.rules.get("incident_subtype_keywords", {}).keys())
        )

    # ── ONE 專用規則 ─────────────────────────────────────────
    def _match_one_carrier(self, original_text: str, text_lower: str) -> bool:
        cfg = self._carrier_configs.get("ONE", {})
        # (a) 完整名稱已經在一般 lookup 處理，這裡只補「裸字 ONE」的情形
        # (b) 原文出現大小寫完全相符的全大寫 "ONE"
        if self._ONE_BARE_PATTERN.search(original_text):
            return True
        # (c) "ONE" 緊鄰 shipping context word（大小寫不拘）
        context_words = cfg.get("context_words", [])
        if context_words:
            ctx_pattern = re.compile(
                r'\bone\s+(' + '|'.join(re.escape(w) for w in context_words) + r')\b',
                re.IGNORECASE,
            )
            if ctx_pattern.search(text_lower):
                return True
        # 有出現裸字 "one"（大小寫不拘）但前面兩個條件都沒通過 → 記一筆「疑似但拒絕」
        if self._ONE_BARE_LOWER_PATTERN.search(text_lower):
            self.diagnostics["carrier_ambiguous_rejected"] += 1
        return False

    # ── 個別抽取器 ───────────────────────────────────────────
    def extract_vessel_type(self, text: str) -> Optional[str]:
        for pattern, key, _display in self._vessel_type_lookup:
            if pattern.search(text):
                return key
        return None

    def extract_carrier(self, text: str, original_text: Optional[str] = None) -> Optional[str]:
        """
        ★ text 建議傳「原始大小寫」文字（不要先 .lower()）——ONE 的判斷需要
        原始大小寫資訊。其餘航商比對本身已用 re.IGNORECASE，不受影響。
        """
        original_text = original_text if original_text is not None else text
        for pattern, key, _display in self._carrier_lookup:
            if pattern.search(text):
                self.diagnostics["carrier_detected"] += 1
                return key
        if self._match_one_carrier(original_text, original_text.lower()):
            self.diagnostics["carrier_detected"] += 1
            return "ONE"
        return None

    def extract_sea_area(self, text: str) -> tuple[Optional[str], Optional[str]]:
        """回傳 (key, display)，例如 ('RED_SEA', '紅海 / Red Sea')。"""
        for pattern, key, display in self._area_lookup:
            if pattern.search(text):
                return key, display
        return None, None

    def extract_vessel_name(self, original_text: str) -> Optional[str]:
        """Best-effort，抓不到回傳 None（不猜測）。非本階段必測項目。"""
        for pat in self._VESSEL_NAME_PATTERNS:
            m = pat.search(original_text)
            if m:
                name = m.group(1).strip(' "\'')
                if 2 <= len(name) <= 40:
                    return name
        return None

    def is_own_fleet_carrier(self, carrier_key: Optional[str]) -> bool:
        return carrier_key in self._own_fleet_keys if carrier_key else False

    def carrier_display(self, key: Optional[str]) -> Optional[str]:
        return self._carrier_display.get(key) if key else None

    def area_display(self, key: Optional[str]) -> Optional[str]:
        return self._area_display.get(key) if key else None

    def area_region_group(self, key: Optional[str]) -> Optional[str]:
        return self._area_region_group.get(key) if key else None

    # ── 事件分類（新版 event_type，與舊版 incident_category 分離）──
    def classify_event_type(self, title: str, summary: str,
                            legacy_category: Optional[str] = None) -> str:
        text_lower = f"{title} {summary}".lower()
        matched_types: set[str] = set()
        for kw, et in self._event_type_lookup:
            if kw in text_lower:
                matched_types.add(et)

        if matched_types:
            for et in self._event_type_priority:
                if et in matched_types:
                    return et

        # 沒有新字典命中 → 退回舊版 CAT mapping
        if legacy_category:
            return self._legacy_map.get(legacy_category, EventType.OTHER)
        return EventType.OTHER

    # ── 事件子類型（Phase 2.1，供 clustering 判斷「同類但不同事故」用）──
    def classify_incident_subtype(self, title: str, summary: str) -> Optional[str]:
        text_lower = f"{title} {summary}".lower()
        matched: set[str] = set()
        for kw, subtype in self._subtype_lookup:
            if kw in text_lower:
                matched.add(subtype)
        if not matched:
            return None
        for subtype in self._subtype_priority:
            if subtype in matched:
                return subtype
        return next(iter(matched))

    # ── 主入口：豐富化單篇 Article（就地修改並回傳）──────────
    def enrich(self, article: NewsArticle) -> NewsArticle:
        original_text = f"{article.title} {article.summary}"
        text_lower = original_text.lower()

        # 標題正規化（供 clustering 使用，非 dataclass 正式欄位）
        article._normalized_title = normalize_title(article.title)

        vessel_type_key = self.extract_vessel_type(original_text)
        if vessel_type_key:
            article.vessel_type = vessel_type_key

        vessel_name = self.extract_vessel_name(original_text)
        if vessel_name:
            article.vessel_name = vessel_name

        # ★ 傳原始大小寫文字給 extract_carrier，ONE 的規則需要判斷大小寫
        carrier_key = self.extract_carrier(original_text, original_text)
        if carrier_key:
            article.carrier = carrier_key

        area_key, area_display = self.extract_sea_area(original_text)
        if area_key:
            article.sea_area = area_key
            article.location = area_display

        article.event_type = self.classify_event_type(
            article.title, article.summary, article.incident_category
        )
        article.incident_subtype = self.classify_incident_subtype(
            article.title, article.summary
        )

        return article
