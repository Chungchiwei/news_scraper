"""
test_pipeline.py
涵蓋：test_priority_sort，以及 CLAUDE.md §二十七 的 CASE 1-7 端對端情境測試。

全部使用 tests/fixtures/articles.json 或就地建構的 mock article，
不呼叫任何 live RSS / SMTP / LLM。
"""

from datetime import timedelta

from models import ManagementPriority, ConfidenceLevel
from risk_scorer import sort_events


def _run_pipeline(articles, extractor, carrier_filter, scorer, clusterer, now):
    kept, dropped = carrier_filter.filter_articles(list(articles))
    for a in kept:
        extractor.enrich(a)
        scorer.score_article(a, now=now)
    events = clusterer.cluster(kept)
    scorer.score_events(events, now=now)
    events = sort_events(events)
    return events, dropped


def test_priority_sort(scorer, make_article):
    """排序必須以 management_priority → management_score → last_updated 為準，
    不可再以 published time 當主要排序依據。"""
    from models import MaritimeEvent

    e1 = MaritimeEvent(event_id="e1", headline="low score P2",
                       management_priority=ManagementPriority.P2, management_score=61,
                       last_updated=None)
    e2 = MaritimeEvent(event_id="e2", headline="high score P1",
                       management_priority=ManagementPriority.P1, management_score=82,
                       last_updated=None)
    e3 = MaritimeEvent(event_id="e3", headline="higher score P1",
                       management_priority=ManagementPriority.P1, management_score=95,
                       last_updated=None)
    e4 = MaritimeEvent(event_id="e4", headline="P3 but very recent",
                       management_priority=ManagementPriority.P3, management_score=45,
                       last_updated=None)

    ordered = sort_events([e1, e4, e2, e3])
    assert [e.event_id for e in ordered] == ["e3", "e2", "e1", "e4"]


# ══════════════════════════════════════════════════════════════
# CASE 1-7（§二十七）
# ══════════════════════════════════════════════════════════════

def test_case1_red_sea_missile_attack(fixture_articles, extractor, carrier_filter, scorer, clusterer, now):
    events, _ = _run_pipeline([fixture_articles["fx04a"]], extractor, carrier_filter, scorer, clusterer, now)
    e = events[0]
    assert e.event_type == "SECURITY"
    assert e.severity_score >= 25
    assert e.operational_impact_score >= 12
    assert e.management_priority in (ManagementPriority.P1, ManagementPriority.P2)


def test_case2_reddit_rumor_low_confidence(fixture_articles, extractor, carrier_filter, scorer, clusterer, now):
    events, _ = _run_pipeline([fixture_articles["fx11"]], extractor, carrier_filter, scorer, clusterer, now)
    e = events[0]
    assert e.confidence_level == ConfidenceLevel.LOW
    assert e.management_priority != ManagementPriority.P1


def test_case3_carrier_new_service_is_p3_not_safety(fixture_articles, extractor, carrier_filter, scorer, clusterer, now):
    events, dropped = _run_pipeline([fixture_articles["fx07"]], extractor, carrier_filter, scorer, clusterer, now)
    assert len(dropped) == 0
    e = events[0]
    assert e.event_type == "COMPETITOR"
    assert e.management_priority == ManagementPriority.P3


def test_case4_carrier_award_filtered_out(fixture_articles, extractor, carrier_filter, scorer, clusterer, now):
    events, dropped = _run_pipeline([fixture_articles["fx08"]], extractor, carrier_filter, scorer, clusterer, now)
    assert len(events) == 0
    assert len(dropped) == 1


def test_case5_three_sources_one_event_high_confidence(fixture_articles, extractor, carrier_filter, scorer, clusterer, now):
    arts = [fixture_articles["fx04a"], fixture_articles["fx04b"], fixture_articles["fx04c"]]
    events, _ = _run_pipeline(arts, extractor, carrier_filter, scorer, clusterer, now)
    assert len(events) == 1
    assert events[0].source_count == 3
    assert events[0].confidence_level == ConfidenceLevel.HIGH


def test_case6_two_distinct_events_stay_separate(fixture_articles, extractor, carrier_filter, scorer, clusterer, now):
    arts = [fixture_articles["fx15a"], fixture_articles["fx15b"]]
    events, _ = _run_pipeline(arts, extractor, carrier_filter, scorer, clusterer, now)
    assert len(events) == 2


def test_case7_wan_hai_fire_forces_p1(fixture_articles, extractor, carrier_filter, scorer, clusterer, now):
    events, _ = _run_pipeline([fixture_articles["fx12"]], extractor, carrier_filter, scorer, clusterer, now)
    e = events[0]
    assert e.management_priority == ManagementPriority.P1


def test_full_fixture_set_runs_without_crash_and_produces_priorities(
    fixture_articles, extractor, carrier_filter, scorer, clusterer, now
):
    """Regression：整批 20 篇 fixture 一次跑過完整 pipeline，確認不會 crash，
    且 P1-P4 分佈合理（至少要有 P1，至少一則 PR 被濾除）。"""
    all_articles = list(fixture_articles.values())
    events, dropped = _run_pipeline(all_articles, extractor, carrier_filter, scorer, clusterer, now)

    assert len(dropped) >= 1
    counts = {p: 0 for p in ManagementPriority.ORDER}
    for e in events:
        counts[e.management_priority] += 1
    assert sum(counts.values()) == len(events)
    assert counts[ManagementPriority.P1] >= 1
    # 事件數必須少於文章數（代表 clustering 真的有作用，紅海攻擊 3 篇併成 1 個事件）
    assert len(events) < len(all_articles)
