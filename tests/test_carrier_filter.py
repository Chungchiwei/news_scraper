"""
test_carrier_filter.py
涵蓋：test_carrier_pr_filter
"""

from carrier_news_filter import CarrierNewsFilter


def test_carrier_pr_filter(carrier_filter, make_article):
    # 具 operational significance → 保留
    operational = make_article(
        source_name="Maersk News", source_category="航商動態",
        title="Maersk launches new container service Asia-Europe",
        summary="Maersk announced a new container service connecting Asia and Europe.",
    )
    result = carrier_filter.decide(operational)
    assert result.decision == CarrierNewsFilter.KEEP_OPERATIONAL

    # 純公關稿（得獎）→ 濾除
    pr_fluff = make_article(
        source_name="Maersk News", source_category="航商動態",
        title="Maersk wins sustainability award for green shipping initiative",
        summary="Maersk has been recognized with a sustainability award at an industry conference.",
    )
    result = carrier_filter.decide(pr_fluff)
    assert result.decision == CarrierNewsFilter.DROP

    # 不確定（兩邊都沒命中）→ 保留但降級，不可濾除也不可視為 operational
    ambiguous = make_article(
        source_name="CMA CGM News", source_category="航商動態",
        title="CMA CGM comments on quarterly outlook",
        summary="CMA CGM management shared thoughts on the quarterly business outlook.",
    )
    result = carrier_filter.decide(ambiguous)
    assert result.decision == CarrierNewsFilter.KEEP_LOW_VALUE

    # 非航商來源、非 COMPETITOR 分類 → 不套用此 filter
    non_carrier = make_article(
        source_name="Reuters", source_category="國際媒體",
        title="Some unrelated world news headline",
        summary="Nothing to do with shipping carriers.",
    )
    assert CarrierNewsFilter.applies_to(non_carrier) is False


def test_carrier_pr_filter_batch(carrier_filter, make_article):
    articles = [
        make_article(article_id="a1", source_name="Maersk News", source_category="航商動態",
                    title="Maersk launches new container service",
                    summary="Maersk announced a new container service."),
        make_article(article_id="a2", source_name="Maersk News", source_category="航商動態",
                    title="Maersk wins sustainability award",
                    summary="Maersk recognized with a sustainability award."),
        make_article(article_id="a3", source_name="Reuters", source_category="國際媒體",
                    title="Unrelated headline", summary="Nothing shipping related."),
    ]
    kept, dropped = carrier_filter.filter_articles(articles)
    kept_ids = {a.article_id for a in kept}
    assert "a1" in kept_ids
    assert "a3" in kept_ids
    assert "a2" not in kept_ids
    assert len(dropped) == 1
