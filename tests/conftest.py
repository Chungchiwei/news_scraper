"""
tests/conftest.py
Phase 2 測試共用 fixture。

所有測試只使用 tests/fixtures/articles.json 的 mock 資料，
不依賴 live RSS / live website / SMTP / LLM。
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# 讓測試不論從哪個目錄執行 pytest 都能 import 到專案根目錄的模組
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models import NewsArticle                      # noqa: E402
from risk_config import load_risk_rules              # noqa: E402
from event_extractor import EventExtractor           # noqa: E402
from carrier_news_filter import CarrierNewsFilter     # noqa: E402
from risk_scorer import RiskScorer                    # noqa: E402
from event_clusterer import EventClusterer            # noqa: E402

# ── Phase 3 ──────────────────────────────────────────────────────
from memory_config import load_memory_rules           # noqa: E402
from event_store import EventStore                     # noqa: E402
from memory_pipeline import apply_persistent_memory, generate_run_id  # noqa: E402

FIXTURES_PATH = ROOT / "tests" / "fixtures" / "articles.json"


@pytest.fixture(scope="session")
def now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="session")
def rules():
    return load_risk_rules(str(ROOT / "risk_rules.json"))


@pytest.fixture
def extractor(rules):
    return EventExtractor(rules)


@pytest.fixture
def carrier_filter(rules):
    return CarrierNewsFilter(rules)


@pytest.fixture
def scorer(rules, extractor):
    return RiskScorer(rules, extractor)


@pytest.fixture
def clusterer(rules):
    return EventClusterer(rules)


def _raw_fixtures() -> list[dict]:
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _article_from_raw(d: dict, rules: dict, now: datetime,
                      tier_override: str | None = None) -> NewsArticle:
    tiers = rules.get("source_tiers", {})
    default_tier = tiers.get("_default", "C")
    tier = tier_override or tiers.get(d["source_name"], default_tier)
    minutes_ago = d.get("minutes_ago")
    published_at = (now - timedelta(minutes=minutes_ago)
                    if minutes_ago is not None else None)
    return NewsArticle(
        article_id="art_" + d["id"],
        source_name=d["source_name"],
        source_category=d.get("source_category"),
        source_lang=d.get("source_lang"),
        source_tier=tier,
        title=d["title"],
        summary=d["summary"],
        url=f"http://example.com/{d['id']}",
        published_at=published_at,
        collected_at=now,
        incident_category=d.get("incident_cat"),
        matched_keywords=[m[0] for m in d.get("matched", [])],
    )


@pytest.fixture
def fixture_articles(rules, now) -> dict[str, NewsArticle]:
    """回傳 {fixture_id: NewsArticle}，尚未跑過 extractor/scorer（乾淨狀態）。"""
    return {d["id"]: _article_from_raw(d, rules, now) for d in _raw_fixtures()}


@pytest.fixture
def make_article(rules, now):
    """工廠函式：測試可以自訂 title/summary/source_tier 等快速造一篇 Article。"""
    def _make(article_id="test_art", source_name="TradeWinds",
              source_category="航運專業", source_lang="en",
              title="", summary="", source_tier=None,
              minutes_ago=30, incident_cat=None):
        tiers = rules.get("source_tiers", {})
        tier = source_tier or tiers.get(source_name, tiers.get("_default", "C"))
        published_at = now - timedelta(minutes=minutes_ago) if minutes_ago is not None else None
        return NewsArticle(
            article_id=article_id, source_name=source_name,
            source_category=source_category, source_lang=source_lang,
            source_tier=tier, title=title, summary=summary,
            url=f"http://example.com/{article_id}",
            published_at=published_at, collected_at=now,
            incident_category=incident_cat,
        )
    return _make


def enrich_and_score(articles: list[NewsArticle], extractor: EventExtractor,
                     scorer: RiskScorer, now: datetime) -> list[NewsArticle]:
    for a in articles:
        extractor.enrich(a)
        scorer.score_article(a, now=now)
    return articles


# ══════════════════════════════════════════════════════════════
# Phase 3 — Persistent Event Memory 共用 fixture
# 全部使用 pytest tmp_path 的暫存 SQLite，絕不碰 production database
# （data/maritime_intelligence.db），也不依賴 live network。
# ══════════════════════════════════════════════════════════════
@pytest.fixture(scope="session")
def memory_rules():
    return load_memory_rules(str(ROOT / "memory_rules.json"))


@pytest.fixture
def event_store(tmp_path):
    store = EventStore(str(tmp_path / "test_memory.db"))
    yield store
    store.close()


@pytest.fixture
def make_dated_article(rules):
    """
    跟 make_article 類似，但 published_at 相對於呼叫端明確傳入的 run_time
    計算（不是綁死 session 的 now），這樣才能模擬『同一事件在不同 run_time
    被觀測』的情境，而不會讓 immediacy 計算出現時間錯亂。
    """
    def _make(article_id, run_time, source_name="Reuters", source_category="航運專業",
              source_lang="en", title="", summary="", source_tier=None, minutes_ago=10):
        tiers = rules.get("source_tiers", {})
        tier = source_tier or tiers.get(source_name, tiers.get("_default", "C"))
        published_at = run_time - timedelta(minutes=minutes_ago) if minutes_ago is not None else None
        return NewsArticle(
            article_id=article_id, source_name=source_name, source_category=source_category,
            source_lang=source_lang, source_tier=tier, title=title, summary=summary,
            url=f"http://example.com/{article_id}",
            published_at=published_at, collected_at=run_time,
        )
    return _make


@pytest.fixture
def run_memory_cycle(rules, memory_rules, extractor, scorer, clusterer):
    """
    工廠函式：_run(store, articles, run_time) 跑完整個 Phase 2/2.1 → Phase 3
    pipeline（enrich → score → cluster → score_events → apply_persistent_memory），
    回傳 apply_persistent_memory() 的結果 dict。
    """
    def _run(store: EventStore, articles: list[NewsArticle], run_time: datetime) -> dict:
        for a in articles:
            extractor.enrich(a)
            scorer.score_article(a, now=run_time)
        events = clusterer.cluster(articles)
        scorer.score_events(events, now=run_time)
        run_id = generate_run_id(run_time)
        return apply_persistent_memory(
            events, store, run_id, run_time, rules, memory_rules, scorer
        )
    return _run
