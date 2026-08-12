#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
source_health.py
海事航運新聞監控系統 — Phase 7 §五十五〜五十七 Source Health（最小版）

職責：
  記錄每個新聞來源（RSS/HTML/Reddit/API）最近一次成功/失敗、連續失敗
  次數，分級成 HEALTHY/DEGRADED/DOWN/UNKNOWN。

  ★ 這是「最小版」（§五十五：若 Phase 1 還沒有正式 SourceHealthManager，
  Phase 7 新增最小版即可）——獨立 SQLite（獨立資料庫檔案，不碰 Phase 3
  event_history、Phase 6 operational_relevance_history、Phase 7 自己的
  delivery_history），只做「這個來源健不健康」這一件事。

  ★ Source Failure ≠ Maritime Alert（§五十七）：這個模組的輸出只會
  出現在 System Health / Dashboard，絕不會被拿去產生 Teams/Email 的
  Maritime Intelligence 通知——那條路徑完全由 delivery_orchestrator.py
  依 Event/Operational 兩條軸決定，跟 Source Health 無關。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_HEALTH_DB_PATH = "data/source_health.db"

# §原始 CLAUDE.md §十四：連續失敗 3 次 DEGRADED，5 次 DOWN。
DEFAULT_DEGRADED_THRESHOLD = 3
DEFAULT_DOWN_THRESHOLD = 5


class SourceHealthStatus:
    HEALTHY  = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN     = "DOWN"
    UNKNOWN  = "UNKNOWN"


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS source_health (
    source_name         TEXT PRIMARY KEY,
    last_success_utc     TEXT,
    last_failure_utc      TEXT,
    consecutive_failures     INTEGER NOT NULL DEFAULT 0,
    last_http_status           TEXT,
    latency_ms                   REAL,
    status                          TEXT NOT NULL DEFAULT 'UNKNOWN',
    updated_at_utc                    TEXT
);
"""


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SourceHealthStore:
    def __init__(self, db_path: Optional[str] = None,
                 degraded_threshold: int = DEFAULT_DEGRADED_THRESHOLD,
                 down_threshold: int = DEFAULT_DOWN_THRESHOLD):
        self.db_path = db_path or DEFAULT_SOURCE_HEALTH_DB_PATH
        self.degraded_threshold = degraded_threshold
        self.down_threshold = down_threshold
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA_SQL)

    def _status_for(self, consecutive_failures: int) -> str:
        if consecutive_failures >= self.down_threshold:
            return SourceHealthStatus.DOWN
        if consecutive_failures >= self.degraded_threshold:
            return SourceHealthStatus.DEGRADED
        return SourceHealthStatus.HEALTHY

    def record_success(self, source_name: str, http_status: Optional[int] = None,
                        latency_ms: Optional[float] = None, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        with self._conn:
            self._conn.execute(
                """INSERT INTO source_health
                   (source_name, last_success_utc, consecutive_failures, last_http_status,
                    latency_ms, status, updated_at_utc)
                   VALUES (?, ?, 0, ?, ?, ?, ?)
                   ON CONFLICT(source_name) DO UPDATE SET
                     last_success_utc = excluded.last_success_utc,
                     consecutive_failures = 0,
                     last_http_status = excluded.last_http_status,
                     latency_ms = excluded.latency_ms,
                     status = excluded.status,
                     updated_at_utc = excluded.updated_at_utc""",
                (source_name, _iso(now), str(http_status) if http_status is not None else None,
                 latency_ms, SourceHealthStatus.HEALTHY, _iso(now)),
            )

    def record_failure(self, source_name: str, http_status: Optional[int] = None,
                        now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        row = self._conn.execute(
            "SELECT consecutive_failures FROM source_health WHERE source_name = ?",
            (source_name,),
        ).fetchone()
        consecutive = (row["consecutive_failures"] if row else 0) + 1
        status = self._status_for(consecutive)
        with self._conn:
            self._conn.execute(
                """INSERT INTO source_health
                   (source_name, last_failure_utc, consecutive_failures, last_http_status,
                    status, updated_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_name) DO UPDATE SET
                     last_failure_utc = excluded.last_failure_utc,
                     consecutive_failures = excluded.consecutive_failures,
                     last_http_status = excluded.last_http_status,
                     status = excluded.status,
                     updated_at_utc = excluded.updated_at_utc""",
                (source_name, _iso(now), consecutive,
                 str(http_status) if http_status is not None else None, status, _iso(now)),
            )

    def get(self, source_name: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM source_health WHERE source_name = ?", (source_name,)
        ).fetchone()
        return dict(row) if row else None

    def all(self) -> list:
        rows = self._conn.execute("SELECT * FROM source_health ORDER BY source_name ASC").fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> dict:
        """給 System Health 用的彙總計數（不含每個來源的細節）。"""
        rows = self.all()
        counts = {"total": len(rows), "HEALTHY": 0, "DEGRADED": 0, "DOWN": 0, "UNKNOWN": 0}
        for r in rows:
            counts[r.get("status", "UNKNOWN")] = counts.get(r.get("status", "UNKNOWN"), 0) + 1
        return counts

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


class NullSourceHealthStore:
    """安全退化：DB 開啟失敗時，記錄動作靜默略過，查詢一律回傳空資料。"""
    def __init__(self):
        self._warned = False

    def _warn_once(self):
        if not self._warned:
            logger.warning("⚠️  SourceHealthStore 不可用，本次 run 的來源健康狀態不會被保存")
            self._warned = True

    def record_success(self, *args, **kwargs) -> None:
        self._warn_once()

    def record_failure(self, *args, **kwargs) -> None:
        self._warn_once()

    def get(self, source_name: str) -> Optional[dict]:
        return None

    def all(self) -> list:
        return []

    def summary(self) -> dict:
        return {"total": 0, "HEALTHY": 0, "DEGRADED": 0, "DOWN": 0, "UNKNOWN": 0}

    def close(self) -> None:
        pass


def open_source_health_store(db_path: Optional[str] = None):
    try:
        return SourceHealthStore(db_path)
    except Exception as e:
        logger.warning(f"⚠️  SourceHealthStore 開啟失敗，改用 NullSourceHealthStore: {e}")
        return NullSourceHealthStore()
