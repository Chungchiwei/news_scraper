#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
event_store.py
海事航運新聞監控系統 — Phase 3 §三〜十、四十三〜四十六 Persistent Event Store

用 Python 內建 sqlite3（不加 ORM）實作四張表：
  events / event_articles / event_history / system_runs

設計原則：
  - DB 開不起來、schema 建不起來 → 一律 EventStoreError（fatal），
    不可 silent fallback 成「全部當新事件」，否則主管會在某次 DB
    損毀時突然收到所有歷史事件重新寄送一次（§四十六）。
  - Event + Articles + History 的合併更新必須是同一個 transaction
    （§四十三），用 `with self._conn:` 讓 sqlite3 自動 commit/rollback。
  - WAL mode 是 best-effort：失敗只記 WARNING，不是 fatal（§四十四）。
  - schema_version 存在 schema_meta 表，供未來 migration 判斷（§四十五）。
  - URL 正規化（§四十一〜四十二）在這裡做，讓同一篇文章（不同
    tracking query string）不會被重複 insert。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src",
}

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS events (
    event_id                  TEXT PRIMARY KEY,
    canonical_key              TEXT,
    headline                   TEXT,
    event_type                 TEXT,
    legacy_category             TEXT,
    incident_subtype            TEXT,
    vessel_name                 TEXT,
    vessel_type                 TEXT,
    carrier                     TEXT,
    imo_number                  TEXT,
    location                    TEXT,
    country                     TEXT,
    region                      TEXT,
    port                        TEXT,
    sea_area                    TEXT,
    shipping_lane                TEXT,
    first_seen_utc               TEXT,
    last_seen_utc                TEXT,
    last_material_update_utc     TEXT,
    event_status                 TEXT,
    information_status           TEXT,
    confidence_level              TEXT,
    management_priority           TEXT,
    management_score               REAL,
    severity_score                  REAL,
    fleet_relevance_score             REAL,
    immediacy_score                    REAL,
    operational_impact_score             REAL,
    source_confidence_score                REAL,
    article_count                            INTEGER,
    independent_source_count                   INTEGER,
    primary_source                               TEXT,
    primary_url                                    TEXT,
    impact_tags_json                                 TEXT,
    vessel_status              TEXT,
    casualty_status             TEXT,
    crew_injured                 INTEGER,
    crew_fatalities                INTEGER,
    crew_missing                     INTEGER,
    fire_status                        TEXT,
    pollution_status                     TEXT,
    port_status                            TEXT,
    navigation_status                        TEXT,
    cargo_status                               TEXT,
    operational_status                           TEXT,
    content_fingerprint                            TEXT,
    version                                          INTEGER,
    notification_state                                 TEXT,
    change_reason                                        TEXT,
    last_run_id                                            TEXT,
    created_at_utc  TEXT,
    updated_at_utc  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_canonical_key ON events(canonical_key);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(event_status);
CREATE INDEX IF NOT EXISTS idx_events_last_seen ON events(last_seen_utc);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);

CREATE TABLE IF NOT EXISTS event_articles (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id           TEXT NOT NULL,
    article_id         TEXT NOT NULL,
    source_name        TEXT,
    source_domain       TEXT,
    source_family         TEXT,
    source_tier             TEXT,
    title                     TEXT,
    url                        TEXT,
    normalized_url               TEXT,
    published_at_utc               TEXT,
    collected_at_utc                 TEXT,
    first_seen_run_id                  TEXT,
    UNIQUE(event_id, article_id)
);
CREATE INDEX IF NOT EXISTS idx_event_articles_event ON event_articles(event_id);
CREATE INDEX IF NOT EXISTS idx_event_articles_url ON event_articles(normalized_url);

CREATE TABLE IF NOT EXISTS event_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL,
    timestamp_utc     TEXT,
    change_type         TEXT,
    old_value_json         TEXT,
    new_value_json            TEXT,
    change_reason                TEXT,
    material                       INTEGER,
    run_id                           TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_history_event ON event_history(event_id);

CREATE TABLE IF NOT EXISTS system_runs (
    run_id               TEXT PRIMARY KEY,
    started_at_utc         TEXT,
    completed_at_utc          TEXT,
    articles_collected           INTEGER,
    valid_articles                  INTEGER,
    events_detected                    INTEGER,
    new_events                            INTEGER,
    material_updates                         INTEGER,
    unchanged_events                            INTEGER,
    resolved_events                                INTEGER,
    status                                            TEXT,
    error_message                                       TEXT
);
"""


class EventStoreError(RuntimeError):
    """
    Phase 3 §四十六：Persistent Memory failure 屬於 production-critical
    failure。呼叫端看到這個例外必須：log ERROR、exit non-zero、
    絕對不能吞掉後 silent fallback 成「全部當新事件」。
    """


def normalize_url(url: Optional[str]) -> Optional[str]:
    """
    §四十一〜四十二：拿掉 tracking query string、統一大小寫/尾斜線，
    讓同一篇文章的兩個不同 tracking 版本被視為同一個 Article。
    解析失敗時回傳原字串（保守，不因正規化本身出錯而遺失資料）。
    """
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        scheme = (parts.scheme or "https").lower()
        netloc = parts.netloc.lower()
        path = parts.path.rstrip("/") or "/"
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in _TRACKING_PARAMS]
        kept.sort()
        query = urlencode(kept)
        return urlunsplit((scheme, netloc, path, query, ""))
    except Exception:
        return url


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_iso(dt) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None


class EventStore:

    def __init__(self, db_path: str, wal_mode: bool = True):
        self.db_path = db_path
        try:
            p = Path(db_path)
            if str(p.parent) not in ("", "."):
                p.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(db_path, timeout=15)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            if wal_mode:
                try:
                    self._conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.Error as e:
                    logger.warning(f"⚠️  無法啟用 SQLite WAL mode，退回預設 journal mode：{e}")
            with self._conn:
                self._conn.executescript(_SCHEMA_SQL)
            self._init_schema_version()
        except (sqlite3.Error, OSError) as e:
            raise EventStoreError(f"Persistent Event Store 初始化失敗（{db_path}）：{e}") from e

    # ── schema version（§四十五）─────────────────────────────
    def _init_schema_version(self) -> None:
        cur = self._conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        )
        row = cur.fetchone()
        if row is None:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
        else:
            stored = int(row["value"])
            if stored > SCHEMA_VERSION:
                raise EventStoreError(
                    f"資料庫 schema_version={stored} 比目前程式支援的版本"
                    f"（{SCHEMA_VERSION}）新，可能是被較新版本寫過，拒絕繼續執行。"
                )
            # stored < SCHEMA_VERSION：未來若有 migration 在此處理，目前恆等。

    def get_schema_version(self) -> int:
        cur = self._conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        )
        row = cur.fetchone()
        return int(row["value"]) if row else 0

    # ── health check（§七十二）───────────────────────────────
    def health_check(self) -> dict:
        try:
            tables = {
                r["name"] for r in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            required = {"events", "event_articles", "event_history", "system_runs", "schema_meta"}
            missing = required - tables
            return {
                "ok": not missing,
                "schema_version": self.get_schema_version(),
                "tables_present": sorted(tables & required),
                "tables_missing": sorted(missing),
                "db_path": self.db_path,
            }
        except sqlite3.Error as e:
            return {"ok": False, "error": str(e), "db_path": self.db_path}

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ── events ───────────────────────────────────────────────
    def count_events(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]

    def get_event(self, event_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_event_by_canonical_key(self, canonical_key: str,
                                   exclude_statuses: tuple = ("EXPIRED",)) -> Optional[dict]:
        placeholders = ",".join("?" * len(exclude_statuses))
        row = self._conn.execute(
            f"SELECT * FROM events WHERE canonical_key = ? "
            f"AND (event_status IS NULL OR event_status NOT IN ({placeholders})) "
            f"ORDER BY last_seen_utc DESC LIMIT 1",
            (canonical_key, *exclude_statuses),
        ).fetchone()
        return dict(row) if row else None

    def get_candidate_events(self, now: datetime, window_days: int,
                             statuses: tuple = ("ACTIVE", "MONITORING", "RESOLVED")) -> list[dict]:
        """跨 run matching 的候選集合：狀態符合 + last_seen_utc 落在搜尋窗口內。"""
        cutoff = _to_iso(now - timedelta(days=window_days))
        placeholders = ",".join("?" * len(statuses))
        rows = self._conn.execute(
            f"SELECT * FROM events WHERE event_status IN ({placeholders}) "
            f"AND last_seen_utc >= ?",
            (*statuses, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_active_or_monitoring(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE event_status IN ('ACTIVE', 'MONITORING')"
        ).fetchall()
        return [dict(r) for r in rows]

    def _execute_upsert_event(self, cur: sqlite3.Cursor, row: dict) -> None:
        row = dict(row)
        row.setdefault("created_at_utc", _utcnow_iso())
        row["updated_at_utc"] = _utcnow_iso()
        columns = list(row.keys())
        placeholders = ",".join("?" for _ in columns)
        update_clause = ",".join(
            f"{c}=excluded.{c}" for c in columns if c not in ("event_id", "created_at_utc")
        )
        sql = (
            f"INSERT INTO events ({','.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(event_id) DO UPDATE SET {update_clause}"
        )
        cur.execute(sql, [row[c] for c in columns])

    def upsert_event(self, row: dict) -> None:
        with self._conn:
            self._execute_upsert_event(self._conn.cursor(), row)

    # ── event_articles（§七、四十一〜四十二）───────────────────
    def _execute_add_article(self, cur: sqlite3.Cursor, event_id: str,
                             article: dict, run_id: str) -> bool:
        """回傳 True 代表這篇文章是第一次被記錄（新 insert），
        False 代表 (event_id, article_id) 已存在，靜默略過（不可重複 insert）。"""
        norm_url = normalize_url(article.get("url"))
        try:
            cur.execute(
                "INSERT INTO event_articles "
                "(event_id, article_id, source_name, source_domain, source_family, "
                " source_tier, title, url, normalized_url, published_at_utc, "
                " collected_at_utc, first_seen_run_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id, article.get("article_id"), article.get("source_name"),
                    article.get("source_domain"), article.get("source_family"),
                    article.get("source_tier"), article.get("title"), article.get("url"),
                    norm_url, _to_iso(article.get("published_at")),
                    _to_iso(article.get("collected_at")), run_id,
                ),
            )
            return True
        except sqlite3.IntegrityError:
            return False   # UNIQUE(event_id, article_id) 已存在

    def add_article(self, event_id: str, article: dict, run_id: str) -> bool:
        with self._conn:
            return self._execute_add_article(self._conn.cursor(), event_id, article, run_id)

    def find_article_by_url(self, normalized_url: Optional[str]) -> Optional[dict]:
        if not normalized_url:
            return None
        row = self._conn.execute(
            "SELECT * FROM event_articles WHERE normalized_url = ? LIMIT 1",
            (normalized_url,),
        ).fetchone()
        return dict(row) if row else None

    def get_articles_for_event(self, event_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM event_articles WHERE event_id = ?", (event_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── event_history（§八、七十七〜七十八）─────────────────────
    def _execute_insert_history(self, cur: sqlite3.Cursor, event_id: str,
                                change_type: str, old_value, new_value,
                                change_reason: Optional[str], material: bool,
                                run_id: str, timestamp_utc: Optional[str] = None) -> None:
        cur.execute(
            "INSERT INTO event_history "
            "(event_id, timestamp_utc, change_type, old_value_json, new_value_json, "
            " change_reason, material, run_id) VALUES (?,?,?,?,?,?,?,?)",
            (
                event_id, timestamp_utc or _utcnow_iso(), change_type,
                json.dumps(old_value, ensure_ascii=False, default=str) if old_value is not None else None,
                json.dumps(new_value, ensure_ascii=False, default=str) if new_value is not None else None,
                change_reason, 1 if material else 0, run_id,
            ),
        )

    def insert_history(self, event_id: str, change_type: str, old_value, new_value,
                       change_reason: Optional[str], material: bool, run_id: str) -> None:
        with self._conn:
            self._execute_insert_history(
                self._conn.cursor(), event_id, change_type, old_value, new_value,
                change_reason, material, run_id,
            )

    def get_history(self, event_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM event_history WHERE event_id = ? ORDER BY id ASC",
            (event_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 合併原子更新（§四十三：Event + Articles + History 同一個 transaction）──
    def persist_event_update(self, event_row: dict, articles: list[dict],
                             history_entries: list[dict], run_id: str) -> dict:
        """
        history_entries: list of dict，至少含 change_type / old_value / new_value /
        change_reason / material。全部與 event_row / articles 在同一個 SQLite
        transaction 內完成；任何一步例外，整包 rollback，不會出現
        「Event 更新成功但 History 沒寫入」的半套狀態（§四十三、七十一）。
        回傳 {"new_articles": [...article_id...]}（本次真正新 insert 的文章 id）。
        """
        new_article_ids: list[str] = []
        with self._conn:
            cur = self._conn.cursor()
            self._execute_upsert_event(cur, event_row)
            for art in articles:
                inserted = self._execute_add_article(cur, event_row["event_id"], art, run_id)
                if inserted:
                    new_article_ids.append(art.get("article_id"))
            for h in history_entries:
                self._execute_insert_history(
                    cur, event_row["event_id"], h["change_type"],
                    h.get("old_value"), h.get("new_value"),
                    h.get("change_reason"), h.get("material", False), run_id,
                    h.get("timestamp_utc"),
                )
        return {"new_articles": new_article_ids}

    # ── system_runs（§九）───────────────────────────────────────
    def start_run(self, run_id: str, started_at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO system_runs "
                "(run_id, started_at_utc, status) VALUES (?,?,?)",
                (run_id, _to_iso(started_at), "RUNNING"),
            )

    def finish_run(self, run_id: str, completed_at: datetime, *,
                   articles_collected: int, valid_articles: int, events_detected: int,
                   new_events: int, material_updates: int, unchanged_events: int,
                   resolved_events: int, status: str = "SUCCESS",
                   error_message: Optional[str] = None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE system_runs SET completed_at_utc=?, articles_collected=?, "
                "valid_articles=?, events_detected=?, new_events=?, material_updates=?, "
                "unchanged_events=?, resolved_events=?, status=?, error_message=? "
                "WHERE run_id=?",
                (
                    _to_iso(completed_at), articles_collected, valid_articles,
                    events_detected, new_events, material_updates, unchanged_events,
                    resolved_events, status, error_message, run_id,
                ),
            )

    def get_run(self, run_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM system_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── Phase 7 §五十四：System Health 需要「最近一次 run」 ─────────
    def get_latest_run(self) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM system_runs ORDER BY started_at_utc DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
