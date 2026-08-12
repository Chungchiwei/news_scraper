"""
tests/test_memory_lifecycle.py
Phase 3 §五十三〜六十七：Persistent Event Memory 生命週期測試。

全部使用 tests/conftest.py 的 event_store（tmp_path SQLite）+
run_memory_cycle（enrich → score → cluster → score_events →
apply_persistent_memory 完整流程），不碰 production database，
不依賴 live RSS / SMTP / Reddit / LLM。
"""

from datetime import timedelta

from models import NotificationState, EventStatus, ManagementPriority, ConfidenceLevel, InformationStatus


# ── CASE 3-A: First Seen / Unchanged ────────────────────────────
def test_new_event_first_seen(event_store, run_memory_cycle, make_dated_article, now):
    a = make_dated_article(
        "n1", now, source_name="Reuters", source_tier="B",
        title="MSC vessel grounding near Singapore",
        summary="An MSC vessel ran aground near the Singapore Strait.",
        minutes_ago=10,
    )
    result = run_memory_cycle(event_store, [a], now)
    events = result["all_current_events"]
    assert len(events) == 1
    e = events[0]
    assert e.notification_state == NotificationState.NEW
    assert e.version == 1
    assert e.event_status == EventStatus.ACTIVE

    row = event_store.get_event(e.event_id)
    assert row is not None
    assert row["first_seen_utc"] is not None


def test_existing_event_unchanged(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "u1", now, source_name="Reuters", source_tier="B",
        title="MSC vessel grounding near Singapore",
        summary="An MSC vessel ran aground near the Singapore Strait.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    event_id = r1["all_current_events"][0].event_id

    run2_time = now + timedelta(hours=3)
    a2 = make_dated_article(
        "u2", run2_time, source_name="Reuters", source_tier="B",
        title="MSC vessel grounding near Singapore",
        summary="An MSC vessel ran aground near the Singapore Strait.",
        minutes_ago=10,
    )
    r2 = run_memory_cycle(event_store, [a2], run2_time)
    e2 = r2["all_current_events"][0]

    assert e2.event_id == event_id
    assert e2.notification_state == NotificationState.UNCHANGED
    assert e2.should_notify is False
    assert e2.version == 1   # UNCHANGED 不遞增 version


# ── CASE 3-B: New Media, Same Facts → Confidence 可能 upgrade ──────
def test_same_event_new_source(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "s1", now, source_name="Reuters", source_tier="B",
        title="MSC vessel grounding near Singapore",
        summary="An MSC vessel ran aground near the Singapore Strait.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    e1 = r1["all_current_events"][0]
    event_id = e1.event_id
    assert e1.confidence_level == ConfidenceLevel.MEDIUM   # 單一 Tier B

    run2_time = now + timedelta(hours=2)
    a2 = make_dated_article(
        "s2", run2_time, source_name="TradeWinds", source_tier="B",
        title="MSC boxship grounding reported near Singapore Strait",
        summary=("MSC container ship grounding reported in the Singapore Strait, "
                 "independently confirmed by the vessel's owner."),
        minutes_ago=10,
    )
    r2 = run_memory_cycle(event_store, [a2], run2_time)
    e2 = r2["all_current_events"][0]

    assert e2.event_id == event_id
    assert e2.confidence_level == ConfidenceLevel.HIGH   # 2 個獨立 Tier B 來源
    history = event_store.get_history(event_id)
    change_types = [h["change_type"] for h in history]
    assert "SOURCE_ADDED" in change_types
    # 若原事件是 P1/P2，confidence 從 MEDIUM → HIGH 依規則視為 Material
    if e1.management_priority in (ManagementPriority.P1, ManagementPriority.P2):
        assert e2.notification_state == NotificationState.MATERIAL_UPDATE
        assert "CONFIDENCE_CHANGED" in change_types


# ── CASE 3-C: Casualty Update ───────────────────────────────────
def test_material_casualty_update(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "c1", now, source_name="Reuters", source_tier="B",
        title="Vessel fire onboard MSC containership",
        summary="A fire broke out onboard an MSC containership. No casualty information available yet.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    e1 = r1["all_current_events"][0]

    run2_time = now + timedelta(hours=2)
    a2 = make_dated_article(
        "c2", run2_time, source_name="Reuters", source_tier="B",
        title="Vessel fire onboard MSC containership update",
        summary="One crew member was seriously injured while fighting the fire onboard the MSC containership.",
        minutes_ago=5,
    )
    r2 = run_memory_cycle(event_store, [a2], run2_time)
    e2 = r2["all_current_events"][0]

    assert e2.event_id == e1.event_id
    assert e2.notification_state == NotificationState.MATERIAL_UPDATE
    assert e2.crew_injured == 1
    assert e2.casualty_status == "INJURED"
    history = event_store.get_history(e2.event_id)
    assert any(h["change_type"] == "CASUALTY_UPDATE" for h in history)


# ── CASE 3-D: Priority Escalation ───────────────────────────────
def test_priority_escalation_material(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "p1", now, source_name="TradeWinds", source_tier="B",
        title="Port congestion reported at Singapore terminal",
        summary="Authorities report port congestion and delays at a Singapore terminal.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    e1 = r1["all_current_events"][0]

    run2_time = now + timedelta(hours=4)
    a2 = make_dated_article(
        "p2", run2_time, source_name="TradeWinds", source_tier="B",
        title="Major port closure in Singapore Strait",
        summary=("Port authorities confirmed a major port closure in the Singapore Strait, "
                 "affecting shipping operations until further notice."),
        minutes_ago=5,
    )
    r2 = run_memory_cycle(event_store, [a2], run2_time)
    e2 = r2["all_current_events"][0]

    assert e2.event_id == e1.event_id
    rank1 = ManagementPriority.RANK.get(e1.management_priority, 99)
    rank2 = ManagementPriority.RANK.get(e2.management_priority, 99)
    assert rank2 < rank1, f"expected priority escalation, got {e1.management_priority} -> {e2.management_priority}"
    assert e2.notification_state == NotificationState.MATERIAL_UPDATE
    history = event_store.get_history(e2.event_id)
    assert any(h["change_type"] == "PRIORITY_CHANGED" for h in history)


# ── CASE 3-E: Wan Hai Identity Revealed ─────────────────────────
def test_wanhai_identity_revealed(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "w1", now, source_name="Splash247", source_tier="C",
        title="Unknown containership collision near Kaohsiung",
        summary="A container vessel of unknown operator was involved in a collision near Kaohsiung port.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    e1 = r1["all_current_events"][0]
    assert e1.carrier is None

    run2_time = now + timedelta(hours=3)
    a2 = make_dated_article(
        "w2", run2_time, source_name="Reuters", source_tier="B",
        title="Wan Hai containership collision near Kaohsiung",
        summary="The vessel involved in the collision near Kaohsiung has been identified as a Wan Hai containership.",
        minutes_ago=5,
    )
    r2 = run_memory_cycle(event_store, [a2], run2_time)
    e2 = r2["all_current_events"][0]

    assert e2.event_id == e1.event_id, "unknown-operator event must upgrade in place, not spawn a new event"
    assert e2.carrier == "WAN_HAI"
    assert e2.fleet_relevance_score > e1.fleet_relevance_score
    assert e2.notification_state == NotificationState.MATERIAL_UPDATE
    rank1 = ManagementPriority.RANK.get(e1.management_priority, 99)
    rank2 = ManagementPriority.RANK.get(e2.management_priority, 99)
    assert rank2 <= rank1


# ── CASE 3-F: Headline Rewrite, Facts Unchanged ─────────────────
def test_headline_change_not_material(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "h1", now, source_name="Reuters", source_tier="B",
        title="MSC vessel attacked in Red Sea",
        summary="An MSC containership was attacked while transiting the Red Sea, the operator confirmed.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    e1 = r1["all_current_events"][0]

    run2_time = now + timedelta(hours=1)
    a2 = make_dated_article(
        "h2", run2_time, source_name="Reuters", source_tier="B",
        title="MSC boxship damaged following projectile strike in Red Sea",
        summary="The MSC containership sustained damage following a projectile strike while transiting the Red Sea.",
        minutes_ago=5,
    )
    r2 = run_memory_cycle(event_store, [a2], run2_time)
    e2 = r2["all_current_events"][0]

    assert e2.event_id == e1.event_id, "headline rewrite of the same facts must match the existing event"
    assert e2.notification_state in (NotificationState.UNCHANGED, NotificationState.MINOR_UPDATE)
    assert e2.should_notify is False


# ── CASE 3-G: Repost Storm ───────────────────────────────────────
def test_repost_storm_suppressed(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "rs1", now, source_name="Reuters", source_tier="B",
        title="MSC vessel grounding near Singapore",
        summary="An MSC vessel ran aground near the Singapore Strait.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    e1 = r1["all_current_events"][0]
    assert e1.independent_source_count == 1

    run2_time = now + timedelta(hours=2)
    reposts = [
        make_dated_article(
            f"rs2_{i}", run2_time, source_name=f"Repost Site {i}", source_tier="C",
            title="MSC vessel grounding near Singapore",
            summary=f"According to Reuters, an MSC vessel ran aground near the Singapore Strait. (via outlet {i})",
            minutes_ago=5 + i,
        )
        for i in range(10)
    ]
    r2 = run_memory_cycle(event_store, reposts, run2_time)
    assert len(r2["all_current_events"]) == 1
    e2 = r2["all_current_events"][0]

    assert e2.event_id == e1.event_id
    assert e2.article_count >= 10
    assert e2.independent_source_count == 1, "10 篇都轉載自 Reuters，獨立來源數不應該增加"
    assert e2.notification_state != NotificationState.MATERIAL_UPDATE
    assert e2.should_notify is False


# ── CASE 3-H: EARLY_SIGNAL → CONFIRMED/CORROBORATED ─────────────
def test_early_signal_to_confirmed(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "es1", now, source_name="Reddit r/maritime", source_tier="D",
        source_category="航運專業",
        title="Possible fire onboard a Wan Hai vessel near Singapore",
        summary=("Unconfirmed post claims a possible fire onboard a Wan Hai vessel near Singapore, "
                 "no official confirmation yet."),
        minutes_ago=5,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    e1 = r1["all_current_events"][0]
    assert e1.management_priority == ManagementPriority.P1
    assert e1.confidence_level == ConfidenceLevel.LOW
    assert e1.information_status == InformationStatus.EARLY_SIGNAL

    run2_time = now + timedelta(hours=1)
    a2 = make_dated_article(
        "es2", run2_time, source_name="Reuters", source_tier="B",
        title="Wan Hai vessel fire confirmed near Singapore",
        summary="Reuters confirms a fire onboard a Wan Hai vessel near Singapore.",
        minutes_ago=5,
    )
    r2 = run_memory_cycle(event_store, [a2], run2_time)
    e2 = r2["all_current_events"][0]

    assert e2.event_id == e1.event_id
    assert e2.information_status in (InformationStatus.CORROBORATED, InformationStatus.CONFIRMED,
                                     InformationStatus.UNCONFIRMED)
    assert e2.notification_state == NotificationState.MATERIAL_UPDATE
    assert e2.should_notify is True


# ── CASE 3-I: Event Resolved ─────────────────────────────────────
def test_event_resolved(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "r1", now, source_name="TradeWinds", source_tier="B",
        title="Singapore Strait canal restriction after incident",
        summary="Port authorities confirmed a canal restriction in the Singapore Strait after an incident.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    e1 = r1["all_current_events"][0]
    assert e1.event_status == EventStatus.ACTIVE

    run2_time = now + timedelta(hours=6)
    a2 = make_dated_article(
        "r2", run2_time, source_name="TradeWinds", source_tier="B",
        title="Singapore Strait canal restriction lifted, channel reopened",
        summary=("The canal restriction in the Singapore Strait has been lifted; the channel has "
                 "reopened to traffic and operations have resumed."),
        minutes_ago=5,
    )
    r2 = run_memory_cycle(event_store, [a2], run2_time)
    e2 = r2["all_current_events"][0]

    assert e2.event_id == e1.event_id
    assert e2.event_status == EventStatus.RESOLVED
    assert e2.notification_state == NotificationState.RESOLVED_UPDATE
    history = event_store.get_history(e2.event_id)
    assert any(h["change_type"] == "RESOLVED" for h in history)


# ── CASE 3-J: Reopened ────────────────────────────────────────────
def test_event_reopened(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "ro1", now, source_name="TradeWinds", source_tier="B",
        title="Singapore Strait canal restriction after incident",
        summary="Port authorities confirmed a canal restriction in the Singapore Strait after an incident.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    event_id = r1["all_current_events"][0].event_id

    t2 = now + timedelta(hours=6)
    a2 = make_dated_article(
        "ro2", t2, source_name="TradeWinds", source_tier="B",
        title="Singapore Strait canal restriction lifted, channel reopened",
        summary=("The canal restriction in the Singapore Strait has been lifted; the channel has "
                 "reopened to traffic and operations have resumed."),
        minutes_ago=5,
    )
    r2 = run_memory_cycle(event_store, [a2], t2)
    assert r2["all_current_events"][0].event_status == EventStatus.RESOLVED

    t3 = now + timedelta(hours=12)
    a3 = make_dated_article(
        "ro3", t3, source_name="TradeWinds", source_tier="B",
        title="Singapore Strait canal restriction reinstated, channel closed again",
        summary=("The canal restriction in the Singapore Strait has been reinstated and the "
                 "channel closed again shortly after reopening."),
        minutes_ago=5,
    )
    r3 = run_memory_cycle(event_store, [a3], t3)
    e3 = r3["all_current_events"][0]

    assert e3.event_id == event_id
    assert e3.event_status == EventStatus.ACTIVE
    assert e3.notification_state == NotificationState.MATERIAL_UPDATE
    history = event_store.get_history(event_id)
    assert any(h["change_type"] == "REOPENED" for h in history)


# ── CASE 3-K: Different Vessel → New Event ──────────────────────
def test_different_vessel_new_event(event_store, run_memory_cycle, make_dated_article, now):
    a1 = make_dated_article(
        "dv1", now, source_name="Reuters", source_tier="B",
        title="MV MSC ORION grounds near Kaohsiung",
        summary="The vessel MV MSC ORION ran aground near Kaohsiung port.",
        minutes_ago=10,
    )
    r1 = run_memory_cycle(event_store, [a1], now)
    e1 = r1["all_current_events"][0]
    assert e1.vessel_name

    t2 = now + timedelta(hours=5)
    a2 = make_dated_article(
        "dv2", t2, source_name="Reuters", source_tier="B",
        title="MV MSC AURORA grounds near Kaohsiung",
        summary="The vessel MV MSC AURORA ran aground near Kaohsiung port.",
        minutes_ago=5,
    )
    r2 = run_memory_cycle(event_store, [a2], t2)
    e2 = r2["all_current_events"][0]

    assert e2.event_id != e1.event_id, "different named vessel must not attach to the existing event"
    assert e2.notification_state == NotificationState.NEW
