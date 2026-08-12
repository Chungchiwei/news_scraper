"""
test_confidence_priority.py
Phase 2.1 §五〜七：Priority 與 Confidence/information_status 必須完全解耦。
"""

from models import ManagementPriority, ConfidenceLevel, InformationStatus, MaritimeEvent, NewsArticle


def test_priority_confidence_independent():
    """
    直接在 MaritimeEvent 層級驗證：P1 + LOW confidence + EARLY_SIGNAL
    是合法組合，兩個欄位不會互相覆寫或耦合。
    """
    event = MaritimeEvent(
        event_id="e1",
        headline="Possible incident involving Wan Hai vessel",
        management_priority=ManagementPriority.P1,
        management_score=85.0,
        confidence_level=ConfidenceLevel.LOW,
        information_status=InformationStatus.EARLY_SIGNAL,
    )
    assert event.management_priority == ManagementPriority.P1
    assert event.confidence_level == ConfidenceLevel.LOW
    assert event.information_status == InformationStatus.EARLY_SIGNAL
    # 兩者互相獨立：改其中一個不會連動另一個
    event.management_priority = ManagementPriority.P4
    assert event.confidence_level == ConfidenceLevel.LOW
    assert event.information_status == InformationStatus.EARLY_SIGNAL


def test_wanhai_reddit_critical_override_low_confidence(
    extractor, carrier_filter, scorer, clusterer, now, make_article
):
    """
    §五 範例情境：Reddit 出現「萬海船疑似火災」。
    Fleet Relevance 可以讓它變成 P1 IMMEDIATE ATTENTION，
    但只有 Tier D（Reddit）來源，information_status 必須是 EARLY_SIGNAL，
    confidence 必須是 LOW —— 不可以因為 Priority 高就被寫成「已證實」。
    """
    a = make_article(
        article_id="wh_reddit", source_name="Reddit r/maritime", source_tier="D",
        source_category="航運專業",
        title="Possible fire onboard a Wan Hai vessel near Singapore",
        summary=("Unconfirmed post claims a possible fire onboard a Wan Hai vessel "
                 "near Singapore, no official confirmation yet."),
        minutes_ago=5,
    )
    kept, dropped = carrier_filter.filter_articles([a])
    assert len(dropped) == 0
    for art in kept:
        extractor.enrich(art)
        scorer.score_article(art, now=now)
    events = clusterer.cluster(kept)
    scorer.score_events(events, now=now)

    assert len(events) == 1
    e = events[0]
    assert e.management_priority == ManagementPriority.P1, \
        f"expected P1 via fleet relevance / critical override, got {e.management_priority}"
    assert e.confidence_level == ConfidenceLevel.LOW
    assert e.information_status == InformationStatus.EARLY_SIGNAL
