"""
test_risk_scoring.py
涵蓋：test_severity_scoring / test_fleet_relevance_scoring /
      test_immediacy_scoring / test_operational_impact /
      test_source_confidence / test_management_priority /
      test_critical_override
"""

from datetime import timedelta

from models import SourceTier, ManagementPriority, EventType


def test_severity_scoring(scorer):
    # 30 分級距
    assert scorer.score_severity(
        "Vessel sinking off coast", "The vessel is sinking, crew abandoning ship."
    ) == 30.0
    # 25 分級距
    assert scorer.score_severity(
        "Container ship fire reported", "A container ship fire was reported at sea."
    ) == 25.0
    # 20 分級距
    assert scorer.score_severity(
        "Vessel grounding near port", "The vessel grounding occurred near the port entrance."
    ) == 20.0
    # 15 分級距
    assert scorer.score_severity(
        "Port congestion worsens", "Port congestion has worsened this week."
    ) == 15.0
    # 沒有命中任何 tier，退回 event_type 預設值
    assert scorer.score_severity(
        "Generic shipping update", "Nothing severe here.", event_type=EventType.MARKET
    ) == 5.0


def test_fleet_relevance_scoring(scorer):
    # own fleet（萬海）固定 25 分，不因其他規則疊加超過上限
    assert scorer.score_fleet_relevance(
        "Wan Hai container ship incident", "WAN_HAI", "CONTAINER_SHIP", "RED_SEA"
    ) == 25.0

    # 主要航商 + 貨櫃船 + 主要航線 → 20 分
    assert scorer.score_fleet_relevance(
        "MSC container ship in Red Sea", "MSC", "CONTAINER_SHIP", "RED_SEA"
    ) == 20.0

    # 只有主要航商或主要海域（非貨櫃船）→ 15 分
    assert scorer.score_fleet_relevance(
        "MSC tanker incident", "MSC", "TANKER", None
    ) == 15.0

    # 一般商船關鍵字 → 10 分
    assert scorer.score_fleet_relevance(
        "A vessel had an incident", None, None, None
    ) == 10.0

    # 完全無關聯 → 0 分
    assert scorer.score_fleet_relevance(
        "Some unrelated business update", None, None, None
    ) == 0.0


def test_immediacy_scoring(scorer, now):
    # 注意：故意不用 "breaking" 之類的 ongoing 關鍵字，避免污染基礎門檻測試
    text = "Routine update on vessel status"
    assert scorer.score_immediacy(text, now - timedelta(hours=1), now) == 20.0
    assert scorer.score_immediacy(text, now - timedelta(hours=10), now) == 18.0
    assert scorer.score_immediacy(text, now - timedelta(hours=20), now) == 15.0
    assert scorer.score_immediacy(text, now - timedelta(hours=40), now) == 10.0
    assert scorer.score_immediacy(text, now - timedelta(hours=60), now) == 5.0
    assert scorer.score_immediacy(text, now - timedelta(hours=100), now) == 2.0
    # 時間未知
    assert scorer.score_immediacy(text, None, now) == 5.0
    # ongoing bonus，但不得超過上限 20
    capped = scorer.score_immediacy("rescue ongoing right now", now - timedelta(hours=1), now)
    assert capped == 20.0


def test_operational_impact(scorer):
    score, tags = scorer.score_operational_impact(
        "Major port closure disrupts shipping lane closure"
    )
    assert score == 15.0
    assert "NAVIGATION" in tags

    score, _ = scorer.score_operational_impact("Port congestion reported at terminal")
    assert score == 10.0

    score, _ = scorer.score_operational_impact("Carrier announces freight rate increase")
    assert score == 4.0

    score, tags = scorer.score_operational_impact("Nothing operationally relevant here")
    assert score == 0.0
    assert tags == []


def test_source_confidence(scorer):
    assert scorer.score_source_confidence_article(SourceTier.A) == 10.0
    assert scorer.score_source_confidence_article(SourceTier.B) == 8.0
    assert scorer.score_source_confidence_article(SourceTier.C) == 5.0
    assert scorer.score_source_confidence_article(SourceTier.D) == 2.0
    # 未知 tier 預設視為 C
    assert scorer.score_source_confidence_article(None) == 5.0

    # Event 層級：Tier A 存在 → 10；2+ 獨立 Tier B → 10；1 個 Tier B → 8；只有 Tier D → 2
    assert scorer.score_source_confidence_event({"A": 1}) == 10.0
    assert scorer.score_source_confidence_event({"B": 2}) == 10.0
    assert scorer.score_source_confidence_event({"B": 1}) == 8.0
    assert scorer.score_source_confidence_event({"D": 1}) == 2.0


def test_management_priority(scorer):
    p, override = scorer.determine_priority(85, EventType.SAFETY, 30, "", False)
    assert p == ManagementPriority.P1 and not override
    p, override = scorer.determine_priority(65, EventType.OPERATIONS, 15, "", False)
    assert p == ManagementPriority.P2 and not override
    p, override = scorer.determine_priority(45, EventType.MARKET, 5, "", False)
    assert p == ManagementPriority.P3 and not override
    p, override = scorer.determine_priority(20, EventType.OTHER, 5, "", False)
    assert p == ManagementPriority.P4 and not override


def test_critical_override(scorer):
    # own fleet + 高 severity + 分數已達門檻 → 強制升為 P1
    p, override = scorer.determine_priority(
        70, EventType.SAFETY, severity_score=25, text="wan hai container ship fire",
        is_own_fleet=True,
    )
    assert p == ManagementPriority.P1
    assert override is True

    # 分數太低（< min_score_to_consider）→ 即使 own fleet 也不觸發
    p, override = scorer.determine_priority(
        30, EventType.SAFETY, severity_score=25, text="wan hai minor issue",
        is_own_fleet=True,
    )
    assert p == ManagementPriority.P4
    assert override is False

    # SECURITY 攻擊關鍵字觸發 override（非 own fleet 也可以）
    p, override = scorer.determine_priority(
        70, EventType.SECURITY, severity_score=30,
        text="container vessel attacked by missile in red sea",
        is_own_fleet=False,
    )
    assert p == ManagementPriority.P1
    assert override is True
