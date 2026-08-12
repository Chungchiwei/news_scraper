#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
operational_history.py
海事航運新聞監控系統 — Phase 6 §四十七〜五十四 Operational Exposure History

職責：
  獨立的 SQLite 表（獨立資料庫檔案 data/operational_relevance.db，
  跟 Phase 3 的 event_history 完全分開，§五十一：兩者描述的是不同的
  東西——Phase 3 描述『事件本身怎麼變』，這裡描述『我們公司的曝險
  怎麼變』），保存每次 run 的 Operational Relevance 快照，並提供
  跨 run 比對，判斷 Exposure 是 NEW / ESCALATED / UNCHANGED / REDUCED
  / CLEARED（§五十三：獨立於 Phase 3 NotificationState 的第二條時間軸）。

★ Provider 失敗（relevance_status=UNAVAILABLE）的快照不會被存檔——
  避免用一次『不知道』污染下一次比對的基準，也避免誤判成 CLEARED
  （§六十四）。下一次成功評估時，仍會跟『上一次成功評估』比較，
  而不是跟這次失敗的結果比較。

★ 只保存 Event-specific 的曝險摘要（affected vessel/service/port/
  reasons），不保存完整公司船期資料庫（§九十五〜九十六 Data Privacy
  Boundary）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from operational_models import OperationalRelevance, RelevanceLevel, RelevanceStatus, OperationalNotificationState

logger = logging.getLogger(__name__)

DEFAULT_OPERATIONAL_HISTORY_DB_PATH = "data/operational_relevance.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS operational_relevance_history (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id              TEXT NOT NULL,
    run_id                TEXT,
    timestamp_utc         TEXT,
    relevance_score       REAL,
    relevance_level       TEXT,
    relevance_status      TEXT,
    affected_vessels_json TEXT,
    affected_services_json TEXT,
    affected_ports_json   TEXT,
    relevance_reasons_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_oprh_event_id ON operational_relevance_history(event_id);
"""


def _vessel_to_dict(v) -> dict:
    return {
        "vessel_name": v.vessel_name, "service_code": v.service_code,
        "next_port": v.next_port, "eta_display": v.eta_display,
        "exposure_type": v.exposure_type, "hours_to_exposure": v.hours_to_exposure,
    }


class OperationalHistoryStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_OPERATIONAL_HISTORY_DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA_SQL)

    def get_latest(self, event_id: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT * FROM operational_relevance_history WHERE event_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (event_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "event_id": row["event_id"], "run_id": row["run_id"],
            "timestamp_utc": row["timestamp_utc"],
            "relevance_score": row["relevance_score"],
            "relevance_level": row["relevance_level"],
            "relevance_status": row["relevance_status"],
            "affected_vessels": json.loads(row["affected_vessels_json"] or "[]"),
            "affected_services": json.loads(row["affected_services_json"] or "[]"),
            "affected_ports": json.loads(row["affected_ports_json"] or "[]"),
            "relevance_reasons": json.loads(row["relevance_reasons_json"] or "[]"),
        }

    def save_snapshot(self, relevance: OperationalRelevance) -> None:
        """Provider 失敗（UNAVAILABLE）的快照不存檔，見本檔案 docstring。"""
        if relevance.relevance_status == RelevanceStatus.UNAVAILABLE:
            return
        with self._conn:
            self._conn.execute(
                """INSERT INTO operational_relevance_history
                   (event_id, run_id, timestamp_utc, relevance_score, relevance_level,
                    relevance_status, affected_vessels_json, affected_services_json,
                    affected_ports_json, relevance_reasons_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    relevance.event_id, relevance.run_id,
                    (relevance.assessed_at or datetime.now(timezone.utc)).isoformat(),
                    relevance.relevance_score, relevance.relevance_level, relevance.relevance_status,
                    json.dumps([_vessel_to_dict(v) for v in relevance.affected_vessels], ensure_ascii=False),
                    json.dumps(relevance.affected_services, ensure_ascii=False),
                    json.dumps(relevance.affected_ports, ensure_ascii=False),
                    json.dumps(relevance.relevance_reasons, ensure_ascii=False),
                ),
            )

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class NullOperationalHistoryStore:
    """§八十四風格的安全退化：DB 開啟失敗時，永遠回傳『沒有歷史』且不存檔，不阻擋 Email。"""
    def __init__(self):
        self._warned = False

    def get_latest(self, event_id: str) -> Optional[dict]:
        return None

    def save_snapshot(self, relevance) -> None:
        if not self._warned:
            logger.warning("⚠️  OperationalHistoryStore 不可用，本次 run 的曝險快照不會被保存")
            self._warned = True

    def close(self):
        pass


def open_operational_history(db_path: Optional[str] = None):
    try:
        return OperationalHistoryStore(db_path)
    except Exception as e:
        logger.warning(f"⚠️  OperationalHistoryStore 開啟失敗，改用 NullOperationalHistoryStore: {e}")
        return NullOperationalHistoryStore()


def compute_operational_notification_state(previous: Optional[dict],
                                             current: OperationalRelevance) -> str:
    """
    §五十三：獨立於 Phase 3 NotificationState 的第二條時間軸。
    RelevanceLevel.RANK 數字越小代表曝險越高（DIRECT=0 ... NONE=4），
    所以「曝險升高」是 rank 數字變小，「曝險降低」是 rank 數字變大。
    """
    if current.relevance_status == RelevanceStatus.UNAVAILABLE:
        return OperationalNotificationState.EXPOSURE_UNAVAILABLE

    if previous is None:
        return OperationalNotificationState.EXPOSURE_NEW

    prev_level = previous.get("relevance_level") or RelevanceLevel.NONE
    curr_level = current.relevance_level or RelevanceLevel.NONE
    prev_rank = RelevanceLevel.RANK.get(prev_level, RelevanceLevel.RANK[RelevanceLevel.NONE])
    curr_rank = RelevanceLevel.RANK.get(curr_level, RelevanceLevel.RANK[RelevanceLevel.NONE])

    if curr_rank < prev_rank:
        return OperationalNotificationState.EXPOSURE_ESCALATED
    if curr_rank > prev_rank:
        if curr_level == RelevanceLevel.NONE:
            return OperationalNotificationState.EXPOSURE_CLEARED
        return OperationalNotificationState.EXPOSURE_REDUCED
    return OperationalNotificationState.EXPOSURE_UNCHANGED
