"""
test_clustering_hardening.py
Phase 2.1 §二〜四：Clustering Hardening。

刻意使用「沒有配合演算法」的困難情境（CLAUDE.md CASE 2.1-A/B/C 原文），
驗證 negative signals / hard reject 是否正確運作，而不是靠 fixture 討好演算法。
"""

from models import NewsArticle


def _prep(articles, extractor, scorer, now):
    articles = list(articles)
    for a in articles:
        extractor.enrich(a)
        scorer.score_article(a, now=now)
    return articles


def test_cluster_same_event_without_carrier(extractor, scorer, clusterer, now, make_article):
    """
    CASE 2.1-A：三篇報導同一起紅海事件，只有第一篇提到 carrier/vessel name，
    另外兩篇完全沒有提到 carrier、vessel name。應該仍能靠
    incident_subtype 一致 + 地理位置相關（含 region_group）+ 時間相近
    + summary 相似度，正確 cluster 成同一個 Event。
    """
    a = make_article(
        article_id="21a1", source_name="Reuters", source_tier="B",
        title="MSC ORION attacked in Red Sea",
        summary=("The containership MSC ORION reported an explosion after being "
                 "struck by a projectile while transiting southbound."),
        minutes_ago=10,
    )
    b = make_article(
        article_id="21a2", source_name="TradeWinds", source_tier="B",
        title="Container vessel hit by projectile near Bab el-Mandeb",
        summary=("A merchant vessel reported damage following an attack while "
                 "sailing through the southern Red Sea."),
        minutes_ago=25,
    )
    c = make_article(
        article_id="21a3", source_name="gCaptain", source_tier="B",
        title="Merchant ship reports explosion west of Hodeidah",
        summary=("Authorities received a report from a commercial vessel regarding "
                 "an explosion approximately 20 nautical miles west of Hodeidah."),
        minutes_ago=40,
    )

    articles = _prep([a, b, c], extractor, scorer, now)
    events = clusterer.cluster(articles)

    assert len(events) == 1, f"expected 1 event, got {len(events)}: {[e.headline for e in events]}"
    assert events[0].article_count == 3


def test_cluster_same_area_different_vessel(extractor, scorer, clusterer, now, make_article):
    """
    CASE 2.1-B：Container ship 與 Tanker 都在紅海「被攻擊」，用詞高度相似，
    但船型明確不同。不可因為同海域 + 同攻擊描述就自動合併成 1 個事件。
    """
    a = make_article(
        article_id="21b1", source_name="Reuters", source_tier="B",
        title="Container ship attacked in Red Sea",
        summary="A container ship was attacked while transiting the Red Sea, the operator confirmed.",
        minutes_ago=10,
    )
    b = make_article(
        article_id="21b2", source_name="TradeWinds", source_tier="B",
        title="Tanker attacked in Red Sea",
        summary="A tanker was attacked while transiting the Red Sea, according to shipping sources.",
        minutes_ago=20,
    )

    articles = _prep([a, b], extractor, scorer, now)
    assert articles[0].vessel_type == "CONTAINER_SHIP"
    assert articles[1].vessel_type == "TANKER"

    events = clusterer.cluster(articles)
    assert len(events) == 2, f"expected 2 events (different vessel type), got {len(events)}"


def test_cluster_same_carrier_different_event(extractor, scorer, clusterer, now, make_article):
    """
    CASE 2.1-C：同一家航商（MSC）+ 同一港口（新加坡），但一篇是 collision、
    一篇是 grounding —— 不同的具體事故類型。Event Type conflict 必須是
    strong negative signal，不可因 carrier+location 相同就合併。
    """
    a = make_article(
        article_id="21c1", source_name="Reuters", source_tier="B",
        title="MSC vessel collision near Singapore",
        summary="An MSC vessel was involved in a collision near Singapore.",
        minutes_ago=10,
    )
    b = make_article(
        article_id="21c2", source_name="TradeWinds", source_tier="B",
        title="MSC vessel grounding near Singapore",
        summary="An MSC vessel ran aground near Singapore.",
        minutes_ago=20,
    )

    articles = _prep([a, b], extractor, scorer, now)
    assert articles[0].carrier == articles[1].carrier == "MSC"
    assert articles[0].incident_subtype == "COLLISION"
    assert articles[1].incident_subtype == "GROUNDING"

    events = clusterer.cluster(articles)
    assert len(events) == 2, f"expected 2 events (subtype conflict), got {len(events)}"


def test_cluster_different_vessel_hard_reject(extractor, scorer, clusterer, now, make_article):
    """
    明確不同的 vessel name（MSC ORION vs MSC AURORA）必須是接近 hard reject，
    即使 carrier / event_type / location 全部相同，也絕不可合併。
    """
    a = make_article(
        article_id="hr1", source_name="Reuters", source_tier="B",
        title="MSC vessel collision near Singapore",
        summary="An MSC vessel was involved in a collision near Singapore.",
        minutes_ago=10,
    )
    b = make_article(
        article_id="hr2", source_name="TradeWinds", source_tier="B",
        title="MSC vessel collision near Singapore reported",
        summary="An MSC vessel collision was reported near Singapore.",
        minutes_ago=12,
    )
    articles = _prep([a, b], extractor, scorer, now)
    # 手動指定明確不同的船名（模擬已經有更精準的抽取結果）
    articles[0].vessel_name = "MSC ORION"
    articles[1].vessel_name = "MSC AURORA"

    score = clusterer.pair_score(articles[0], articles[1])
    assert clusterer._is_hard_reject(score)

    events = clusterer.cluster(articles)
    assert len(events) == 2, f"expected 2 events (hard reject on vessel name), got {len(events)}"
