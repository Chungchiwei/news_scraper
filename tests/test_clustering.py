"""
test_clustering.py
涵蓋：test_event_clustering_same_event / test_event_clustering_different_event /
      test_primary_source_selection / test_low_confidence_reddit
"""

from models import ConfidenceLevel, SourceTier


def _prep(articles, extractor, scorer, now):
    articles = list(articles)
    for a in articles:
        extractor.enrich(a)
        scorer.score_article(a, now=now)
    return articles


def test_event_clustering_same_event(fixture_articles, extractor, scorer, clusterer, now):
    """三家媒體報導同一起紅海飛彈攻擊事件，必須聚類成 1 個 Event，3 篇文章。"""
    arts = [fixture_articles["fx04a"], fixture_articles["fx04b"], fixture_articles["fx04c"]]
    arts = _prep(arts, extractor, scorer, now)
    events = clusterer.cluster(arts)
    assert len(events) == 1
    assert events[0].source_count == 3


def test_event_clustering_different_event(fixture_articles, extractor, scorer, clusterer, now):
    """MSC collision 與 Maersk grounding 即使地點相近、時間相近，仍須維持 2 個獨立 Event。"""
    arts = [fixture_articles["fx15a"], fixture_articles["fx15b"]]
    arts = _prep(arts, extractor, scorer, now)
    events = clusterer.cluster(arts)
    assert len(events) == 2


def test_primary_source_selection(fixture_articles, extractor, scorer, clusterer, now):
    """同一 Event 內，Primary Article 必須選 Tier 最高的來源（Reuters/TradeWinds 為 B，
    但若加入一則 Tier D 來源，Primary 絕不能選到它。"""
    arts = [fixture_articles["fx04a"], fixture_articles["fx04b"], fixture_articles["fx04c"]]
    arts = _prep(arts, extractor, scorer, now)

    # 手動插入一則同事件的 Reddit（Tier D）文章，且摘要故意寫得更長
    reddit_dup = fixture_articles["fx04a"]
    from models import NewsArticle
    reddit_article = NewsArticle(
        article_id="art_fx04_reddit", source_name="Reddit r/maritime",
        source_category="航運專業", source_lang="en", source_tier=SourceTier.D,
        title="Container vessel attacked by missile in Red Sea - discussion thread",
        summary="A" * 500,  # 刻意做超長摘要，確認 tier 仍然優先於摘要長度
        url="http://example.com/reddit_dup",
        published_at=reddit_dup.published_at, collected_at=now,
    )
    extractor.enrich(reddit_article)
    scorer.score_article(reddit_article, now=now)

    all_arts = arts + [reddit_article]
    events = clusterer.cluster(all_arts)
    assert len(events) == 1
    primary = events[0].primary_article
    assert primary.source_tier != SourceTier.D
    assert primary.source_name != "Reddit r/maritime"


def test_low_confidence_reddit(fixture_articles, extractor, scorer, clusterer, now):
    """只有 Reddit 單一來源、無法交叉驗證的事件，confidence 必須是 LOW。"""
    arts = _prep([fixture_articles["fx11"]], extractor, scorer, now)
    events = clusterer.cluster(arts)
    scorer.score_events(events, now=now)
    assert len(events) == 1
    assert events[0].confidence_level == ConfidenceLevel.LOW
    assert events[0].management_priority != "P1"
