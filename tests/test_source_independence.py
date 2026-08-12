"""
test_source_independence.py
Phase 2.1 §十二〜十七：Article Count ≠ Independent Source Count。
"""

from models import ConfidenceLevel


def _prep_and_cluster(articles, extractor, scorer, clusterer, now):
    for a in articles:
        extractor.enrich(a)
        scorer.score_article(a, now=now)
    events = clusterer.cluster(articles)
    scorer.score_events(events, now=now)
    return events


def test_independent_source_count_reuters_reposts(
    extractor, scorer, clusterer, now, make_article
):
    """
    Reuters 原稿 + Yahoo 轉載（"according to Reuters"）+ MSN 轉載
    （"Reuters reported"）。article_count 應該是 3，但 independent_source_count
    必須收斂成 1，confidence 不可以因此變 HIGH。
    """
    r1 = make_article(
        article_id="src1", source_name="Reuters", source_tier="B",
        title="MSC vessel grounds in Singapore Strait",
        summary="MSC container vessel ran aground in the Singapore Strait.",
        minutes_ago=10,
    )
    r2 = make_article(
        article_id="src2", source_name="Yahoo News", source_tier="C",
        title="MSC vessel grounds in Singapore Strait",
        summary="According to Reuters, an MSC vessel ran aground in the Singapore Strait.",
        minutes_ago=15,
    )
    r3 = make_article(
        article_id="src3", source_name="MSN", source_tier="C",
        title="MSC vessel aground near Singapore",
        summary="Reuters reported that an MSC vessel ran aground near Singapore.",
        minutes_ago=20,
    )

    events = _prep_and_cluster([r1, r2, r3], extractor, scorer, clusterer, now)
    assert len(events) == 1
    e = events[0]

    assert e.article_count == 3
    assert e.independent_source_count == 1, (
        f"expected 1 independent source (all trace back to Reuters), "
        f"got {e.independent_source_count}"
    )
    assert e.confidence_level != ConfidenceLevel.HIGH


def test_independent_source_reuters_tradewinds(
    extractor, scorer, clusterer, now, make_article
):
    """
    Reuters + TradeWinds 各自獨立報導同一起事件（不是轉載關係）。
    article_count=2，independent_source_count 也必須是 2，
    confidence 應該是 HIGH（2 個獨立 Tier B 來源互相佐證）。
    """
    r1 = make_article(
        article_id="srcB1", source_name="Reuters", source_tier="B",
        title="MSC vessel grounds in Singapore Strait",
        summary="MSC container vessel ran aground in the Singapore Strait.",
        minutes_ago=10,
    )
    r2 = make_article(
        article_id="srcB2", source_name="TradeWinds", source_tier="B",
        title="MSC boxship grounding Singapore Strait",
        summary=("MSC container ship grounding reported in the Singapore Strait, "
                 "independently confirmed by the vessel's owner."),
        minutes_ago=15,
    )

    events = _prep_and_cluster([r1, r2], extractor, scorer, clusterer, now)
    assert len(events) == 1
    e = events[0]

    assert e.article_count == 2
    assert e.independent_source_count == 2
    assert e.confidence_level == ConfidenceLevel.HIGH
