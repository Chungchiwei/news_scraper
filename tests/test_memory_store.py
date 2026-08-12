"""
tests/test_memory_store.py
Phase 3 §四十一〜四十六、六十七〜七十一：Persistent Event Store 基礎設施測試。

全部使用 pytest tmp_path 的暫存 SQLite，不碰 production database
（data/maritime_intelligence.db），不依賴 live network。
"""

from datetime import timedelta
from unittest.mock import patch

import pytest

from event_store import EventStore, EventStoreError, normalize_url
from memory_pipeline import apply_persistent_memory, generate_run_id


# ── §六十四 Database Restart ─────────────────────────────────────
def test_memory_survives_restart(tmp_path, run_memory_cycle, make_dated_article, now):
    db_path = str(tmp_path / "restart.db")
    store_a = EventStore(db_path)
    a = make_dated_article(
        "rs1", now, source_name="Reuters", source_tier="B",
        title="MSC vessel grounding near Singapore",
        summary="An MSC vessel ran aground near the Singapore Strait.",
        minutes_ago=10,
    )
    result = run_memory_cycle(store_a, [a], now)
    event_id = result["all_current_events"][0].event_id
    store_a.close()

    # Process B：重新 instantiate，必須能從 SQLite 找回同一個事件
    store_b = EventStore(db_path)
    row = store_b.get_event(event_id)
    assert row is not None
    assert row["headline"] == "MSC vessel grounding near Singapore"
    assert store_b.count_events() == 1
    store_b.close()


# ── §六十五 URL Tracking Params ──────────────────────────────────
def test_tracking_url_dedup():
    url_a = "https://example.com/news/123?utm_source=google&utm_medium=social"
    url_b = "https://example.com/news/123?utm_source=facebook"
    url_c = "https://example.com/news/123"
    assert normalize_url(url_a) == normalize_url(url_b) == normalize_url(url_c)


def test_tracking_url_dedup_find_article(tmp_path):
    store = EventStore(str(tmp_path / "url.db"))
    event_row = {
        "event_id": "evt_url1", "canonical_key": "K1", "headline": "h",
        "event_type": "SAFETY", "event_status": "ACTIVE", "version": 1,
    }
    article = {
        "article_id": "a1", "source_name": "Reuters",
        "url": "https://example.com/news/123?utm_source=google",
        "title": "t",
    }
    store.persist_event_update(event_row, [article], [], run_id="run1")

    found = store.find_article_by_url(normalize_url("https://example.com/news/123?utm_source=facebook"))
    assert found is not None
    assert found["article_id"] == "a1"
    store.close()


# ── §三十一 Event Version ────────────────────────────────────────
def test_event_version_increment(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "v1", now, source_name="Reuters", source_tier="B",
        title="Vessel fire onboard MSC containership",
        summary="A fire broke out onboard an MSC containership. No casualty information available yet.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    e1 = r1["all_current_events"][0]
    assert e1.version == 1

    t2 = now + timedelta(hours=2)
    a2 = make_dated_article(
        "v2", t2, source_name="Reuters", source_tier="B",
        title="Vessel fire onboard MSC containership update",
        summary="One crew member was seriously injured while fighting the fire onboard the MSC containership.",
        minutes_ago=5,
    )
    r2 = run_memory_cycle(event_store, [a2], t2)
    e2 = r2["all_current_events"][0]
    assert e2.version == 2   # Material Update → version += 1

    t3 = now + timedelta(hours=4)
    a3 = make_dated_article(
        "v3", t3, source_name="Reuters", source_tier="B",
        title="Vessel fire onboard MSC containership update",
        summary="One crew member was seriously injured while fighting the fire onboard the MSC containership.",
        minutes_ago=5,
    )
    r3 = run_memory_cycle(event_store, [a3], t3)
    e3 = r3["all_current_events"][0]
    assert e3.version == 2   # UNCHANGED/MINOR 不遞增 version


# ── §三十二 last_seen vs last_material_update ────────────────────
def test_last_seen_update(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "ls1", now, source_name="Reuters", source_tier="B",
        title="MSC vessel grounding near Singapore",
        summary="An MSC vessel ran aground near the Singapore Strait.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    event_id = r1["all_current_events"][0].event_id
    row1 = event_store.get_event(event_id)
    first_last_seen = row1["last_seen_utc"]

    t2 = now + timedelta(hours=5)
    a2 = make_dated_article(
        "ls2", t2, source_name="Reuters", source_tier="B",
        title="MSC vessel grounding near Singapore",
        summary="An MSC vessel ran aground near the Singapore Strait.",
        minutes_ago=10,
    )
    run_memory_cycle(event_store, [a2], t2)
    row2 = event_store.get_event(event_id)

    assert row2["last_seen_utc"] != first_last_seen
    assert row2["last_seen_utc"] > first_last_seen


def test_last_material_update(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "lm1", now, source_name="Reuters", source_tier="B",
        title="MSC vessel grounding near Singapore",
        summary="An MSC vessel ran aground near the Singapore Strait.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    event_id = r1["all_current_events"][0].event_id
    row1 = event_store.get_event(event_id)
    first_material_update = row1["last_material_update_utc"]
    assert first_material_update is not None

    # run2：純粹重複（UNCHANGED）—— last_seen 前進，last_material_update 不應該跟著動
    t2 = now + timedelta(hours=5)
    a2 = make_dated_article(
        "lm2", t2, source_name="Reuters", source_tier="B",
        title="MSC vessel grounding near Singapore",
        summary="An MSC vessel ran aground near the Singapore Strait.",
        minutes_ago=10,
    )
    run_memory_cycle(event_store, [a2], t2)
    row2 = event_store.get_event(event_id)
    assert row2["last_material_update_utc"] == first_material_update
    assert row2["last_seen_utc"] > row1["last_seen_utc"]

    # run3：Casualty Update（Material）—— last_material_update 才會前進
    t3 = now + timedelta(hours=8)
    a3 = make_dated_article(
        "lm3", t3, source_name="Reuters", source_tier="B",
        title="MSC vessel grounding update",
        summary="The MSC vessel remains aground. One crew member was seriously injured during the incident.",
        minutes_ago=5,
    )
    run_memory_cycle(event_store, [a3], t3)
    row3 = event_store.get_event(event_id)
    assert row3["last_material_update_utc"] > first_material_update


# ── §四十三、七十一 Database Transaction / Atomicity ──────────────
def test_database_transaction(tmp_path):
    store = EventStore(str(tmp_path / "tx.db"))
    event_row = {
        "event_id": "evt_tx1", "canonical_key": "K1", "headline": "h",
        "event_type": "SAFETY", "event_status": "ACTIVE", "version": 1,
    }
    articles = [{"article_id": "a1", "source_name": "Reuters", "url": "http://x/1", "title": "t"}]
    history = [{"change_type": "EVENT_CREATED", "old_value": None, "new_value": {"x": 1},
               "change_reason": "first seen", "material": True}]

    # 故意讓 history insert 失敗，確認 event upsert 也一起 rollback
    # （不會出現『Event 更新成功但 History 沒寫入』的半套狀態）
    with patch.object(EventStore, "_execute_insert_history", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            store.persist_event_update(event_row, articles, history, run_id="run1")

    assert store.get_event("evt_tx1") is None
    assert store.get_articles_for_event("evt_tx1") == []
    store.close()


# ── §四十五 Schema Version ───────────────────────────────────────
def test_schema_version(tmp_path):
    db_path = str(tmp_path / "schema.db")
    store = EventStore(db_path)
    assert store.get_schema_version() == 1
    store.close()

    # 重新開啟不應該重置 schema_version 或清掉資料
    store2 = EventStore(db_path)
    assert store2.get_schema_version() == 1
    store2.close()

    # 模擬資料庫被更新版本的程式寫過（schema_version 比目前程式支援的還新）
    store3 = EventStore(db_path)
    store3._conn.execute(
        "UPDATE schema_meta SET value = '999' WHERE key = 'schema_version'"
    )
    store3._conn.commit()
    store3.close()

    with pytest.raises(EventStoreError):
        EventStore(db_path)


# ── §四十六 Database Failure Is Fatal ─────────────────────────────
def test_database_failure_is_fatal(tmp_path):
    bad_path = str(tmp_path / "unreachable" / "nested" / "test.db")
    with patch("event_store.sqlite3.connect", side_effect=OSError("disk full")):
        with pytest.raises(EventStoreError):
            EventStore(bad_path)


# ── §四十八 Baseline Silent Mode ──────────────────────────────────
def test_baseline_silent_mode(tmp_path, rules, memory_rules, extractor, scorer, clusterer,
                              make_dated_article, now):
    store = EventStore(str(tmp_path / "baseline.db"))
    assert store.count_events() == 0

    a = make_dated_article(
        "bl1", now, source_name="Reddit r/maritime", source_tier="D", source_category="航運專業",
        title="Possible fire onboard a Wan Hai vessel near Singapore",
        summary="Unconfirmed post claims a possible fire onboard a Wan Hai vessel near Singapore.",
        minutes_ago=5,
    )
    extractor.enrich(a)
    scorer.score_article(a, now=now)
    events = clusterer.cluster([a])
    scorer.score_events(events, now=now)
    run_id = generate_run_id(now)

    result = apply_persistent_memory(
        events, store, run_id, now, rules, memory_rules, scorer, baseline_mode="silent"
    )
    e = result["all_current_events"][0]
    assert e.management_priority == "P1"   # own-fleet critical override 仍然成立
    assert e.should_notify is False, "baseline silent 模式必須強制不通知，即使是 P1"
    assert result["is_baseline_run"] is True
    assert store.count_events() == 1   # 事件仍然正常存入，只是不通知
    store.close()
