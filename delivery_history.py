#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
delivery_history.py
海事航運新聞監控系統 — Phase 7 §十七〜二十 Delivery History

職責：
  獨立的 SQLite 表（獨立資料庫檔案 data/delivery_history.db，跟 Phase 3
  的 event_history、Phase 6 的 operational_relevance_history 都完全分開
  ——三者描述的是不同的東西：「事件本身怎麼變」「我們公司的曝險怎麼變」
  「這件事實際被送到哪裡去了」），保存每一次 channel 層級的實際發送
  結果，並提供：

    1. Per-Channel Dedup（§十八〜十九）：event_id + event_version +
       operational_state + channel + delivery_type 組成 dedup_key，
       同一個 dedup_key + channel 已經成功送過，就不再重送。
    2. Per-Channel Failure Isolation（§十八）：P1 Event 的 Email 已寄、
       Teams 失敗時，下次 run Email 不應再重寄，Teams 可以 retry ——
       兩個 channel 的成功/失敗狀態完全獨立記錄。
    3. Cooldown 查詢（§八十〜八十一）：依 channel 查詢某個 event 最近
       一次成功發送時間，供 delivery_orchestrator.py 判斷是否在
       cooldown 窗口內。

★ Provider/SMTP/Webhook 失敗時仍要記一筆 status=FAILED 的紀錄（不像
  Phase 6 UNAVAILABLE 快照那樣略過不存)——因為這裡要保留的正是
  「這個 channel 到底有沒有送成功」的事實，供下次 run 判斷要不要 retry。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DELIVERY_HISTORY_DB_PATH = "data/delivery_history.db"


class DeliveryStatus:
    SENT      = "SENT"
    FAILED    = "FAILED"
    SUPPRESSED = "SUPPRESSED"   # 被 cooldown / dedup 主動壓下，不是失敗


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS delivery_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          TEXT NOT NULL,
    run_id            TEXT,
    channel           TEXT NOT NULL,
    delivery_type     TEXT,
    delivery_reason   TEXT,
    dedup_key         TEXT,
    sent_at           TEXT,
    status            TEXT NOT NULL,
    error_message     TEXT
);
CREATE INDEX IF NOT EXISTS idx_delivery_event_channel ON delivery_history(event_id, channel);
CREATE INDEX IF NOT EXISTS idx_delivery_dedup_channel ON delivery_history(dedup_key, channel);
CREATE INDEX IF NOT EXISTS idx_delivery_sent_at ON delivery_history(sent_at);
"""


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class DeliveryHistoryStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DELIVERY_HISTORY_DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA_SQL)

    # ── 寫入 ─────────────────────────────────────────────────
    def record_delivery(self, *, event_id: str, run_id: Optional[str], channel: str,
                         delivery_type: Optional[str], delivery_reason: Optional[str],
                         dedup_key: Optional[str], status: str,
                         error_message: Optional[str] = None,
                         sent_at: Optional[datetime] = None) -> None:
        with self._conn:
            self._conn.execute(
                """INSERT INTO delivery_history
                   (event_id, run_id, channel, delivery_type, delivery_reason,
                    dedup_key, sent_at, status, error_message)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (event_id, run_id, channel, delivery_type, delivery_reason,
                 dedup_key, _iso(sent_at or datetime.now(timezone.utc)),
                 status, error_message),
            )

    # ── 查詢：Per-Channel Dedup（§十八〜十九）───────────────────
    def already_sent(self, dedup_key: Optional[str], channel: str) -> bool:
        """同一個 dedup_key 在這個 channel 上是否已經有一筆 SENT 紀錄。"""
        if not dedup_key:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM delivery_history WHERE dedup_key = ? AND channel = ? "
            "AND status = ? LIMIT 1",
            (dedup_key, channel, DeliveryStatus.SENT),
        ).fetchone()
        return row is not None

    # ── 查詢：Cooldown（§八十〜八十一）───────────────────────────
    def last_sent_at(self, event_id: str, channel: str) -> Optional[datetime]:
        row = self._conn.execute(
            "SELECT sent_at FROM delivery_history WHERE event_id = ? AND channel = ? "
            "AND status = ? ORDER BY id DESC LIMIT 1",
            (event_id, channel, DeliveryStatus.SENT),
        ).fetchone()
        return _parse_iso(row["sent_at"]) if row else None

    def last_delivery(self, event_id: str, channel: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM delivery_history WHERE event_id = ? AND channel = ? "
            "ORDER BY id DESC LIMIT 1",
            (event_id, channel),
        ).fetchone()
        return dict(row) if row else None

    def history_for_event(self, event_id: str) -> list:
        rows = self._conn.execute(
            "SELECT * FROM delivery_history WHERE event_id = ? ORDER BY id ASC",
            (event_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def recent(self, limit: int = 50) -> list:
        rows = self._conn.execute(
            "SELECT * FROM delivery_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


class NullDeliveryHistoryStore:
    """
    安全退化（同 Phase 6 NullOperationalHistoryStore 慣例）：DB 開啟失敗
    時，dedup/cooldown 查詢一律回傳「查無記錄」（不阻擋發送，寧可少數
    情況下重複發送，也不能因為 History DB 壞掉就讓所有通知永久停擺），
    寫入動作靜默略過並只警告一次。
    """
    def __init__(self):
        self._warned = False

    def _warn_once(self):
        if not self._warned:
            logger.warning("⚠️  DeliveryHistoryStore 不可用，本次 run 的發送紀錄不會被保存（dedup/cooldown 查詢一律視為無歷史）")
            self._warned = True

    def record_delivery(self, **kwargs) -> None:
        self._warn_once()

    def already_sent(self, dedup_key, channel) -> bool:
        return False

    def last_sent_at(self, event_id, channel) -> Optional[datetime]:
        return None

    def last_delivery(self, event_id, channel) -> Optional[dict]:
        return None

    def history_for_event(self, event_id) -> list:
        return []

    def recent(self, limit: int = 50) -> list:
        return []

    def close(self) -> None:
        pass


def open_delivery_history(db_path: Optional[str] = None):
    try:
        return DeliveryHistoryStore(db_path)
    except Exception as e:
        logger.warning(f"⚠️  DeliveryHistoryStore 開啟失敗，改用 NullDeliveryHistoryStore: {e}")
        return NullDeliveryHistoryStore()


def build_dedup_key(event_id: str, event_version, operational_notification_state: Optional[str]) -> str:
    """
    §十九：event_id + event_version + operational_state 組成 dedup_key
    （channel 本身不放進 key，因為 dedup 查詢時是「dedup_key + channel」
    一起比對——同一個 dedup_key 在 EMAIL 上發過，不代表 TEAMS 上也發過，
    見 already_sent()）。
    """
    return f"{event_id}:v{event_version}:{operational_notification_state or 'NONE'}"
