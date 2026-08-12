"""
test_extraction.py
涵蓋：test_event_classification / test_event_extraction_carrier /
      test_event_extraction_region
"""

from models import EventType


def test_event_classification(extractor):
    """新版 event_type 分類要能區分 SAFETY / SECURITY / MARKET / COMPETITOR。"""
    assert extractor.classify_event_type(
        "Container ship fire breaks out off Sri Lanka",
        "A container ship suffered a serious fire, crew evacuated safely."
    ) == EventType.SAFETY

    assert extractor.classify_event_type(
        "Tanker hijacked off Gulf of Guinea",
        "Pirates hijacked a tanker and are holding the crew hostage."
    ) == EventType.SECURITY

    assert extractor.classify_event_type(
        "Freight rate index climbs on strong demand",
        "The freight rate index climbed this week amid strong container demand."
    ) == EventType.MARKET

    assert extractor.classify_event_type(
        "Maersk launches new container service Asia-Europe",
        "Maersk announced a new container service connecting Asia and Europe.",
        legacy_category="CAT6",
    ) == EventType.COMPETITOR

    # 完全沒有命中新字典時，退回舊版 CAT mapping（§五）
    assert extractor.classify_event_type(
        "Some vague headline with no keywords", "", legacy_category="CAT1"
    ) == EventType.SAFETY


def test_event_extraction_carrier(extractor):
    """繁中/簡中/英文的航商名稱都要能 normalize 成同一個 key。"""
    assert extractor.extract_carrier("wan hai container ship fire") == "WAN_HAI"
    assert extractor.extract_carrier("萬海貨櫃船發生火災") == "WAN_HAI"
    assert extractor.extract_carrier("万海货柜船发生火灾") == "WAN_HAI"
    assert extractor.extract_carrier("msc vessel collision near singapore") == "MSC"
    assert extractor.extract_carrier("no carrier mentioned here at all") is None


def test_event_extraction_region(extractor):
    """繁中/簡中/英文的地名都要能 normalize 成同一個 sea_area key。"""
    key, display = extractor.extract_sea_area("attacked in the red sea")
    assert key == "RED_SEA"
    assert "Red Sea" in display

    key, _ = extractor.extract_sea_area("紅海發生攻擊事件")
    assert key == "RED_SEA"

    key, _ = extractor.extract_sea_area("红海发生攻击事件")
    assert key == "RED_SEA"

    key, _ = extractor.extract_sea_area("vessel transiting the strait of hormuz")
    assert key == "HORMUZ"

    key, _ = extractor.extract_sea_area("no location mentioned in this text")
    assert key is None


def test_enrich_sets_all_expected_fields(extractor, make_article):
    a = make_article(
        title="Wan Hai container ship fire in Red Sea",
        summary="A Wan Hai container ship suffered a fire while transiting the Red Sea.",
    )
    extractor.enrich(a)
    assert a.carrier == "WAN_HAI"
    assert a.vessel_type == "CONTAINER_SHIP"
    assert a.sea_area == "RED_SEA"
    assert a.event_type == EventType.SAFETY
    assert a.normalized_title()  # 有值且非空
