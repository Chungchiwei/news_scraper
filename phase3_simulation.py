#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phase3_simulation.py
Phase 3 Completion Report — Three-Run Simulation（§八十 15 / §八十一）。

不是 production 程式的一部分，是一次性驗證腳本：模擬同一套 Persistent
Event Store 連續跑 3 次（08:00 / 14:00 / 20:00），展示：
  Run 1 → 全部 NEW
  Run 2 → NEW / MATERIAL_UPDATE / UNCHANGED 同時出現
  Run 3 → RESOLVED_UPDATE（生命週期轉換）

使用暫存 SQLite（tempfile），不寫入 production database。
"""

import sys
import tempfile
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from models import NewsArticle
from risk_config import load_risk_rules
from memory_config import load_memory_rules
from event_extractor import EventExtractor
from risk_scorer import RiskScorer
from event_clusterer import EventClusterer
from event_store import EventStore
from memory_pipeline import apply_persistent_memory, generate_run_id, print_persistent_memory_report

import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger()


def make_article(article_id, run_time, source_name, source_tier, title, summary, minutes_ago):
    return NewsArticle(
        article_id=article_id, source_name=source_name, source_tier=source_tier,
        source_category="航運專業", title=title, summary=summary,
        url=f"http://example.com/{article_id}",
        published_at=run_time - timedelta(minutes=minutes_ago), collected_at=run_time,
    )


def run(store, extractor, scorer, clusterer, rules, memory_rules, articles, run_time, label):
    for a in articles:
        extractor.enrich(a)
        scorer.score_article(a, now=run_time)
    events = clusterer.cluster(articles)
    scorer.score_events(events, now=run_time)
    run_id = generate_run_id(run_time)
    result = apply_persistent_memory(events, store, run_id, run_time, rules, memory_rules, scorer)
    print(f"\n\n########## {label} (run_time={run_time.strftime('%H:%M')}) ##########")
    print_persistent_memory_report(run_id, len(articles), len(events), result)
    for e in result["all_current_events"]:
        print(
            f"  [{e.notification_state:16s}] {e.event_id}  P={e.management_priority}  "
            f"status={e.event_status:10s}  should_notify={e.should_notify}  "
            f"headline={e.headline!r}"
        )
        if e.notification_reason:
            print(f"      reason: {e.notification_reason}")
    return result


def main():
    rules = load_risk_rules()
    memory_rules = load_memory_rules()
    extractor = EventExtractor(rules)
    scorer = RiskScorer(rules, extractor)
    clusterer = EventClusterer(rules)

    d = tempfile.mkdtemp()
    db_path = os.path.join(d, "phase3_simulation.db")
    store = EventStore(db_path)
    print(f"Using temporary database: {db_path}\n")

    base = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)

    # ── Run 1 (08:00): 兩起獨立事件，全部 NEW ─────────────────────
    t1 = base
    run(store, extractor, scorer, clusterer, rules, memory_rules, [
        make_article(
            "r1_a", t1, "Reuters", "B",
            "MSC vessel grounding near Singapore",
            "An MSC vessel ran aground near the Singapore Strait.",
            10,
        ),
        make_article(
            "r1_b", t1, "TradeWinds", "B",
            "Port congestion reported at Los Angeles terminal",
            "Shipping sources report port congestion and berthing delays at a Los Angeles terminal.",
            15,
        ),
    ], t1, "RUN 1 — 08:00")

    # ── Run 2 (14:00): repost（Event B, UNCHANGED）
    #                    + casualty update（Event A, MATERIAL_UPDATE）
    #                    + 全新事件（Event C, NEW）───────────────
    t2 = base + timedelta(hours=6)
    run(store, extractor, scorer, clusterer, rules, memory_rules, [
        make_article(
            "r2_a", t2, "TradeWinds", "B",
            "MSC vessel grounding update",
            "The MSC vessel remains aground near the Singapore Strait. "
            "One crew member was seriously injured during the incident.",
            5,
        ),
        make_article(
            "r2_b", t2, "Splash247", "C",
            "Port congestion reported at Los Angeles terminal",
            "According to TradeWinds, shipping sources report port congestion at a Los Angeles terminal.",
            10,
        ),
        make_article(
            "r2_c", t2, "Reuters", "B",
            "Container ship attacked in Red Sea",
            "A container ship was attacked while transiting the Red Sea, the operator confirmed.",
            5,
        ),
    ], t2, "RUN 2 — 14:00")

    # ── Run 3 (20:00): Event A 解除（RESOLVED_UPDATE）
    #                    + Event B 再一次沒有變化（UNCHANGED）───────
    t3 = base + timedelta(hours=12)
    run(store, extractor, scorer, clusterer, rules, memory_rules, [
        make_article(
            "r3_a", t3, "TradeWinds", "B",
            "MSC vessel refloated near Singapore",
            "The MSC vessel has been successfully refloated near the Singapore Strait "
            "and port operations have resumed.",
            5,
        ),
        make_article(
            "r3_b", t3, "TradeWinds", "B",
            "Port congestion reported at Los Angeles terminal",
            "Shipping sources report port congestion and berthing delays at a Los Angeles terminal.",
            10,
        ),
    ], t3, "RUN 3 — 20:00")

    store.close()
    print("\n\nSimulation complete.")


if __name__ == "__main__":
    main()
