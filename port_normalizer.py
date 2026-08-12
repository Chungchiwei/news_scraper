#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
port_normalizer.py
海事航運新聞監控系統 — Phase 6 §十七〜十八、六十八〜六十九 Port Normalization

職責：
  把新聞/事件裡各種寫法的港口名稱（英文/繁中/簡中/常見別名/UN-LOCODE）
  正規化成單一 UN/LOCODE，讓 port_relevance.py 可以拿它跟船期資料的
  port_code 直接比對。

  ★ 保守原則（§六十八〜六十九）：
    - 找不到明確對應 → 回傳 None，絕不用 substring/模糊猜測。
    - 像 "Portland" 這種在多國都有同名港口、缺乏國家別資訊就無法唯一
      判定的地名，本表刻意不收錄，一律視為無法 normalize。
    - "Singapore Strait"（海峽/航道）不等於 "Singapore"（港口）——
      這裡只負責港口名稱正規化，海峽/航道相關的比對是
      route_relevance.py 的職責（sea_area/shipping_lane，不是 port_code）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

DEFAULT_PORTS_CONFIG = "config/ports_config.json"

_PUNCTUATION_RE = re.compile(r"[.,·•\-_/]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_key(text: str) -> str:
    """
    小寫化 + 去除標點 + 壓縮空白，供別名表查詢用。
    不做任何語意上的推論（不去除 "port"/"港" 這類字，因為別名表本身
    已經把「有沒有 port/港」的各種寫法都列成獨立 key，避免過度正規化
    導致誤判）。
    """
    if not text:
        return ""
    t = text.strip().lower()
    t = _PUNCTUATION_RE.sub(" ", t)
    t = _WHITESPACE_RE.sub(" ", t).strip()
    return t


def load_port_aliases(config_path: str = DEFAULT_PORTS_CONFIG) -> dict:
    p = Path(config_path)
    if not p.exists():
        p = Path(__file__).parent / config_path
    if not p.exists():
        raise FileNotFoundError(f"ports_config.json not found: {config_path}")
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("aliases", {})


class PortNormalizer:
    def __init__(self, alias_map: Optional[dict] = None):
        self._alias_map = alias_map if alias_map is not None else load_port_aliases()
        # alias_map 的 key 假設已經是「原始寫法」，這裡統一轉成
        # normalize_key() 之後的形式，避免大小寫/標點差異造成查不到。
        self._lookup = {normalize_key(k): v for k, v in self._alias_map.items()}

    def normalize(self, raw_text: Optional[str]) -> Optional[str]:
        """
        把單一字串正規化成 UN/LOCODE；找不到回傳 None。
        支援 Phase 2 常見的 "中文 / English" 組合格式，會分別嘗試整串
        與拆開後的兩段。
        """
        if not raw_text:
            return None

        candidates = [raw_text]
        if " / " in raw_text:
            candidates.extend(part.strip() for part in raw_text.split(" / "))

        for candidate in candidates:
            key = normalize_key(candidate)
            if not key:
                continue
            if key in self._lookup:
                return self._lookup[key]
        return None

    def normalize_many(self, *raw_texts: Optional[str]) -> Optional[str]:
        """依序嘗試多個欄位（例如 event.port、event.location），回傳第一個成功的結果。"""
        for text in raw_texts:
            code = self.normalize(text)
            if code:
                return code
        return None
