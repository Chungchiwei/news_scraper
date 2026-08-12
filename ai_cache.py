#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai_cache.py
海事航運新聞監控系統 — Phase 5 §三十六〜三十八、八十四〜八十五 AI Analysis Cache

職責：
  獨立的 SQLite 表（獨立資料庫檔案，跟 Phase 3 的
  data/maritime_intelligence.db 完全分開），儲存每次 LLM 分析結果，
  提供 cache key 查詢，並保留完整歷史（不覆寫舊紀錄，供未來稽核
  「這段摘要當初是根據什麼」— §八十六 Auditability）。

★ 刻意用獨立資料庫檔案、獨立模組，不去動 event_store.py —— Phase 3
  Event Memory 核心本階段不得修改。

★ Cache 本身故障（無法開啟/寫入）不可以讓 Email 發送失敗（§八十四）：
  呼叫端應該把 AICache 初始化包在 try/except，失敗時退化成
  「每次都當 cache miss」，而不是讓整個 pipeline 中止。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_AI_CACHE_DB_PATH = "data/ai_analysis.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ai_analysis (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key           TEXT NOT NULL,
    event_id            TEXT NOT NULL,
    event_version       INTEGER,
    provider            TEXT,
    model               TEXT,
    prompt_version      TEXT,
    source_fingerprint  TEXT,
    analysis_json       TEXT,
    status              TEXT,
    created_at_utc      TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_analysis_cache_key ON ai_analysis(cache_key);
CREATE INDEX IF NOT EXISTS idx_ai_analysis_event_id ON ai_analysis(event_id);
"""


def make_cache_key(event_id: str, event_version: int, source_fingerprint: str,
                    prompt_version: str, model: str) -> str:
    """§三十七：event_id + version + source_fingerprint + prompt_version + model。"""
    return f"{event_id}:v{event_version}:{source_fingerprint}:{prompt_version}:{model}"


class AICache:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_AI_CACHE_DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA_SQL)

    def get(self, cache_key: str) -> Optional[dict]:
        """回傳最新一筆符合 cache_key 的紀錄（若有），不存在則回傳 None。"""
        cur = self._conn.execute(
            "SELECT * FROM ai_analysis WHERE cache_key = ? ORDER BY id DESC LIMIT 1",
            (cache_key,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        try:
            analysis_json = json.loads(row["analysis_json"]) if row["analysis_json"] else None
        except json.JSONDecodeError:
            analysis_json = None
        return {
            "cache_key": row["cache_key"],
            "event_id": row["event_id"],
            "event_version": row["event_version"],
            "provider": row["provider"],
            "model": row["model"],
            "prompt_version": row["prompt_version"],
            "source_fingerprint": row["source_fingerprint"],
            "analysis_json": analysis_json,
            "status": row["status"],
            "created_at_utc": row["created_at_utc"],
        }

    def put(self, cache_key: str, event_id: str, event_version: int, provider: str,
            model: str, prompt_version: str, source_fingerprint: str,
            analysis_json: Optional[dict], status: str) -> None:
        """
        ★ 永遠 INSERT，不 UPDATE/覆寫舊紀錄（§三十八：保留可追溯性）。
        同一個 cache_key 之後查詢一律取「最新一筆」（get() 用 ORDER BY id DESC）。
        """
        with self._conn:
            self._conn.execute(
                """INSERT INTO ai_analysis
                   (cache_key, event_id, event_version, provider, model, prompt_version,
                    source_fingerprint, analysis_json, status, created_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cache_key, event_id, event_version, provider, model, prompt_version,
                    source_fingerprint,
                    json.dumps(analysis_json, ensure_ascii=False) if analysis_json is not None else None,
                    status,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class NullAICache:
    """
    Cache 初始化失敗時的安全退化版本（§八十四：cache 故障不可阻擋 Email）。
    get() 永遠回傳 None（cache miss），put() 靜默略過並記一次 warning。
    """
    def __init__(self):
        self._warned = False

    def get(self, cache_key: str) -> Optional[dict]:
        return None

    def put(self, *args, **kwargs) -> None:
        if not self._warned:
            logger.warning("⚠️  AICache 不可用，本次 run 的 AI 分析結果不會被快取")
            self._warned = True

    def close(self):
        pass


def open_ai_cache(db_path: Optional[str] = None):
    """安全開啟 AICache；失敗時回退 NullAICache，不讓例外往上炸掉主流程。"""
    try:
        return AICache(db_path)
    except Exception as e:
        logger.warning(f"⚠️  AICache 開啟失敗，改用 NullAICache（本次 run 不使用快取）: {e}")
        return NullAICache()
