"""
test_carrier_alias_audit.py
Phase 2.1 §八〜十一：Carrier Alias False Positive Audit。

重點是 ONE（Ocean Network Express）—— 裸字 "one" 是最常見的英文單字之一，
不能用一般 substring 比對。同時附帶稽核過程中另外發現的真實 bug
（PIL 誤判 "pilot"）與 EVERGREEN 的同類風險，一併回歸測試。
"""


def test_one_false_positive(extractor):
    """"One crew member was injured" 這種句首大寫、非全大寫的 One，絕對不能被判成 ONE 航商。"""
    assert extractor.extract_carrier(
        "One crew member was injured onboard the vessel.",
        "One crew member was injured onboard the vessel.",
    ) is None


def test_one_valid_uppercase(extractor):
    """原文出現大小寫完全相符的全大寫 ONE，應正確判為 ONE 航商。"""
    text = "ONE launches new Asia service."
    assert extractor.extract_carrier(text, text) == "ONE"


def test_ocean_network_express_alias(extractor):
    """完整名稱 Ocean Network Express 無歧義，應正確判為 ONE 航商。"""
    text = "Ocean Network Express announced a new service."
    assert extractor.extract_carrier(text, text) == "ONE"


def test_one_context_word_lowercase_still_matches(extractor):
    """"one" 緊鄰 shipping context word（line/container/vessel/shipping/news）時，
    即使不是全大寫也應該算數（規則 c：ONE + context word）。"""
    text = "A one vessel service was announced for the Asia-Europe route."
    assert extractor.extract_carrier(text, text) == "ONE"


def test_pil_not_matched_inside_pilot(extractor):
    """
    Phase 2.1 稽核發現的真實 bug 回歸測試：
    舊版 naive substring 比對會讓 "PIL" 誤中 "pilot" 裡的 "pil"。
    改用 word-boundary regex 後必須修正。
    """
    text = "The harbor pilot boarded the vessel safely before departure."
    assert extractor.extract_carrier(text, text) is None


def test_pil_valid_uppercase(extractor):
    text = "PIL announced a new Asia-Europe service."
    assert extractor.extract_carrier(text, text) == "PIL"


def test_evergreen_bare_word_not_matched(extractor):
    """
    'evergreen' 單獨是常見英文詞（evergreen fund/strategy），
    已從別名清單移除裸字寫法，只留 'Evergreen Marine' 這類無歧義片語。
    """
    text = "The company adopted an evergreen strategy for long-term growth."
    assert extractor.extract_carrier(text, text) is None


def test_evergreen_marine_valid(extractor):
    text = "Evergreen Marine announced a new service."
    assert extractor.extract_carrier(text, text) == "EVERGREEN"
