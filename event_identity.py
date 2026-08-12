#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
event_identity.py
海事航運新聞監控系統 — Phase 3 §十一〜十六 Stable Event Identity

職責：
  把一個（可能只有部分欄位的）MaritimeEvent 轉成：
    1. identity signals（正規化後的 vessel_name / carrier / event_type /
       incident_subtype / location / date bucket / imo_number）
    2. canonical_key —— 目前「已知資訊能組出的最強身份字串」，
       只當作資料庫索引用的快速查找 key（fast path），
       *不是* Event 是否算同一事件的唯一依據。
    3. event_id —— SHA256(canonical_key) 前 16 碼，只在「確定要建立
       新事件」時才產生一次；一旦事件存進資料庫，之後即使
       canonical_key 因為得知船名而改變，event_id 也不會跟著變
       （由 persistent_matcher.py 負責決定「這是既有事件」還是
       「這是新事件」，event_identity.py 本身不做比對決策）。

Strong Identity Signal 優先順序（§十二）：
  Level 1  IMO Number（目前系統尚無 IMO 抽取來源，schema 預留，
           一旦有 imo_number 一定優先使用）
  Level 2  Vessel Name（正規化）+ Event Type + 時間 bucket
  Level 3  Carrier + Event Type + Location + 時間 bucket（未知船名時使用）
  Level 4  Event Type + Region + Title/Summary Fingerprint + 時間（最弱，
           交給 persistent_matcher.py 用較高 similarity threshold 把關）

★ 不要完全依賴 hash 字串相等（§十五）：第一次新聞可能只知道
  "unknown container ship"，之後才知道是 "MSC ORION"。這時候
  canonical_key 會從 Level 3/4 升級成 Level 2，字串整個改變。
  event_identity.py 只負責「算出目前這個 signal 組合最強能到哪個
  level、canonical_key 長什麼樣子」，「這個新算出來的身份要不要
  接到某個既有 event_id 上」的決策留給 persistent_matcher.py。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Union

from event_extractor import normalize_title

# 支援直接傳 MaritimeEvent dataclass，也支援傳從 DB 撈出來的 dict row
_EventLike = Union["object", dict]


def _get(obj: _EventLike, key: str):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]+")
_CJK_RE = re.compile(r'[一-鿿]')


def _is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def normalize_vessel_name(name: Optional[str]) -> Optional[str]:
    """
    "MSC ORION" / "MSC Orion" / "m.s.c. orion" 全部正規化成 "MSC_ORION"。
    中文船名（罕見但可能出現）保留原字元，只去除空白與標點。
    """
    if not name:
        return None
    name = name.strip()
    if not name:
        return None
    if _is_cjk(name):
        cleaned = re.sub(r"[\s\.\-_,，。、]+", "", name)
        return cleaned or None
    upper = name.upper()
    cleaned = _NON_ALNUM_RE.sub("_", upper).strip("_")
    return cleaned or None


def normalize_location_key(sea_area: Optional[str], region: Optional[str],
                           location: Optional[str]) -> Optional[str]:
    """
    優先使用已經正規化過的 sea_area（例如 RED_SEA，見 event_extractor.py），
    其次 region，最後才退回未正規化的原始 location 文字（弱訊號）。
    """
    if sea_area:
        return sea_area.strip().upper()
    if region:
        return region.strip().upper()
    if location:
        cleaned = normalize_title(location)
        cleaned = cleaned.strip().upper().replace(" ", "_")
        return cleaned or None
    return None


def date_bucket(dt: Optional[datetime]) -> str:
    """UTC 日期字串（YYYY-MM-DD），供 Level 2/3/4 identity 使用。
    注意：這只是 canonical_key 的一部分，不是比對的唯一依據——
    真正決定是否同一事件靠 persistent_matcher.py 的時間窗 + 訊號比對，
    所以事件橫跨午夜不會因為 date_bucket 不同就變成配對不到。"""
    if dt is None:
        return "UNKNOWN_DATE"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def text_fingerprint(title: Optional[str], summary: Optional[str], length: int = 10) -> str:
    text = normalize_title(f"{title or ''} {summary or ''}")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


@dataclass
class IdentitySignals:
    imo_number:        Optional[str]
    vessel_name_norm:  Optional[str]
    carrier:           Optional[str]
    event_type:        Optional[str]
    incident_subtype:  Optional[str]
    location_key:      Optional[str]
    date_bucket:        str
    fingerprint:        str
    level:              str    # "IMO" / "VESSEL" / "CARRIER_LOCATION" / "WEAK"
    canonical_key:      str


class EventIdentityBuilder:
    """
    Stateless（不吃 risk_rules.json，正規化邏輯全部是純字串運算），
    刻意設計成可以直接對 MaritimeEvent dataclass 或 DB dict row 使用，
    這樣 persistent_matcher.py 可以用同一組函式比較「這次 run 新算出來的
    Event」與「資料庫裡既有的 Event row」。
    """

    def build_signals(self, event: _EventLike) -> IdentitySignals:
        imo_number = _get(event, "imo_number")
        vessel_name_norm = normalize_vessel_name(_get(event, "vessel_name"))
        carrier = _get(event, "carrier")
        event_type = _get(event, "event_type")
        incident_subtype = _get(event, "incident_subtype")
        location_key = normalize_location_key(
            _get(event, "sea_area"), _get(event, "region"), _get(event, "location")
        )
        first_seen = _get(event, "first_seen") or _get(event, "first_seen_utc")
        if isinstance(first_seen, str):
            first_seen = _parse_iso(first_seen)
        bucket = date_bucket(first_seen)

        headline = _get(event, "headline")
        primary = _get(event, "primary_article")
        summary = _get(primary, "summary") if primary is not None else None
        fp = text_fingerprint(headline, summary)

        if imo_number:
            level = "IMO"
            key = f"IMO_{imo_number}"
        elif vessel_name_norm:
            level = "VESSEL"
            key = f"VESSEL_{vessel_name_norm}_{event_type or 'UNK'}_{bucket}"
        elif carrier and event_type and location_key:
            level = "CARRIER_LOCATION"
            key = f"CARRIER_{carrier}_{event_type}_{location_key}_{bucket}"
        else:
            level = "WEAK"
            key = f"WEAK_{event_type or 'UNK'}_{location_key or 'UNK'}_{fp}_{bucket}"

        return IdentitySignals(
            imo_number=imo_number,
            vessel_name_norm=vessel_name_norm,
            carrier=carrier,
            event_type=event_type,
            incident_subtype=incident_subtype,
            location_key=location_key,
            date_bucket=bucket,
            fingerprint=fp,
            level=level,
            canonical_key=key,
        )

    def canonical_key(self, event: _EventLike) -> str:
        return self.build_signals(event).canonical_key

    @staticmethod
    def generate_event_id(canonical_key: str) -> str:
        """
        只在確定要建立『新』Event 時呼叫一次。相同 canonical_key
        會產生相同 event_id（可重現、跨 run 穩定），但這不代表
        event_id 永遠等於 hash(目前的 canonical_key)——一旦事件已經
        存進資料庫，之後 canonical_key 即使升級（例如得知船名），
        既有 event_id 也不會改變，見 persistent_matcher.py。
        """
        digest = hashlib.sha256(canonical_key.encode("utf-8")).hexdigest()
        return "evt_" + digest[:16]


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
