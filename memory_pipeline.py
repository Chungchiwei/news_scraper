#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_pipeline.py
海事航運新聞監控系統 — Phase 3 主要整合層

把 Phase 2/2.1（fetch → extract → cluster → score，同一次 run 內完成）
產生的「這次 run 的事件列表」，接上 Persistent Event Memory：

    CURRENT-RUN EVENTS（Phase 2/2.1 clustering 結果）
      ↓
    PERSISTENT EVENT MATCHER（跨 run 比對既有事件，§十五〜十七）
      ↓
    欄位合併（新資訊優先，缺資料時 fall back 既有已知事實，§十五範例：
    unknown container ship → 後來確認是 MSC ORION，不能因為某篇轉載
    沒提到船名就把已知的船名蓋掉）
      ↓
    依合併後欄位重新評分（RiskScorer 再跑一次，讓「後來確認是萬海」
    這種資訊能反映在 fleet_relevance / management_priority 上，§七十五）
      ↓
    MATERIAL CHANGE DETECTION + EVENT LIFECYCLE（§二十四〜三十九）
      ↓
    NOTIFICATION POLICY（§三十三〜三十五）
      ↓
    PERSIST（Event + Articles + History 同一個 transaction，§四十三）

刻意獨立成一個檔案而不是直接寫進 maritime_news.py，是為了讓這一整層
可以在沒有任何 RSS / SMTP 依賴的情況下，用 tmp_path SQLite 直接單元測試
（見 tests/test_memory_*.py），也讓 maritime_news.py 的既有 production
entry point 改動降到最小。
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from models import MaritimeEvent, NewsArticle, EventStatus, NotificationState
from event_store import EventStore, EventStoreError, parse_iso  # noqa: F401  (EventStoreError re-export)
from event_identity import EventIdentityBuilder
from persistent_matcher import PersistentEventMatcher
from status_extractor import StatusExtractor
from material_change_detector import MaterialChangeDetector, build_snapshot
from event_lifecycle import EventLifecycleManager
from notification_policy import NotificationPolicy

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/maritime_intelligence.db"


def generate_run_id(run_time: Optional[datetime] = None) -> str:
    run_time = run_time or datetime.now(timezone.utc)
    return f"{run_time.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(3)}"


def resolve_db_path() -> str:
    return os.getenv("MARITIME_DB_PATH", DEFAULT_DB_PATH)


def resolve_baseline_mode(memory_rules: dict) -> str:
    default = memory_rules.get("baseline_mode", {}).get("default", "notify")
    return os.getenv("MEMORY_BASELINE_MODE", default).strip().lower() or default


def _merge(new_val, old_val):
    """新資訊優先；新資訊缺（None）才 fall back 到既有已知事實（§十五）。"""
    return new_val if new_val is not None else old_val


_TIER_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


def _cumulative_independent_tiers(store: EventStore, event_id: str,
                                   this_run_articles: list[NewsArticle]) -> tuple[dict, int]:
    """
    跨 run 累計版的 SourceProvenanceResolver.independent_source_tiers()。
    EventClusterer 的版本只看得到『這次 run 自己這批文章』，這裡把資料庫裡
    這個 event 既有的文章（event_articles 表，含 source_family/source_tier）
    跟這次 run 的新文章合併後，重新計算「每個獨立來源家族的最佳 tier」，
    confidence / information_status 才能正確反映『歷史上總共有幾家獨立
    媒體報導過這件事』，而不是每個 run 都從 1 個來源重新起算。
    """
    best: dict[str, str] = {}
    existing = store.get_articles_for_event(event_id)
    pairs = [(r.get("source_family") or r.get("source_name") or "UNKNOWN",
             r.get("source_tier") or "C") for r in existing]
    pairs += [(a.source_family or a.source_name or "UNKNOWN", a.source_tier or "C")
             for a in this_run_articles]
    for fam, tier in pairs:
        current = best.get(fam)
        if current is None or _TIER_RANK.get(tier, 2) < _TIER_RANK.get(current, 2):
            best[fam] = tier
    counts: dict[str, int] = {}
    for t in best.values():
        counts[t] = counts.get(t, 0) + 1
    return counts, len(best)


def _better_priority(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """回傳較急迫（RANK 數字較小）的 Priority；None 視為最不急迫。"""
    from models import ManagementPriority
    ra = ManagementPriority.RANK.get(a, 99)
    rb = ManagementPriority.RANK.get(b, 99)
    return a if ra <= rb else b


def _article_to_db_dict(a: NewsArticle) -> dict:
    return {
        "article_id": a.article_id, "source_name": a.source_name,
        "source_domain": a.source_domain, "source_family": a.source_family,
        "source_tier": a.source_tier, "title": a.title, "url": a.url,
        "published_at": a.published_at, "collected_at": a.collected_at,
    }


def _event_row_for_db(event: MaritimeEvent, run_id: str) -> dict:
    import json
    return {
        "event_id": event.event_id,
        "canonical_key": event.canonical_key,
        "headline": event.headline,
        "event_type": event.event_type,
        "legacy_category": event.incident_category,
        "incident_subtype": event.incident_subtype,
        "vessel_name": event.vessel_name,
        "vessel_type": event.vessel_type,
        "carrier": event.carrier,
        "imo_number": event.imo_number,
        "location": event.location,
        "country": event.country,
        "region": event.region,
        "port": event.port,
        "sea_area": event.sea_area,
        "shipping_lane": event.shipping_lane,
        "first_seen_utc": _to_iso(event.first_seen),
        "last_seen_utc": _to_iso(event.last_updated),
        "last_material_update_utc": _to_iso(event.last_material_update),
        "event_status": event.event_status,
        "information_status": event.information_status,
        "confidence_level": event.confidence_level,
        "management_priority": event.management_priority,
        "management_score": event.management_score,
        "severity_score": event.severity_score,
        "fleet_relevance_score": event.fleet_relevance_score,
        "immediacy_score": event.immediacy_score,
        "operational_impact_score": event.operational_impact_score,
        "source_confidence_score": event.source_confidence_score,
        "article_count": event.article_count,
        "independent_source_count": event.independent_source_count,
        "primary_source": event.primary_article.source_name if event.primary_article else None,
        "primary_url": event.primary_article.url if event.primary_article else None,
        "impact_tags_json": json.dumps(event.impact_tags, ensure_ascii=False),
        "vessel_status": event.vessel_status,
        "casualty_status": event.casualty_status,
        "crew_injured": event.crew_injured,
        "crew_fatalities": event.crew_fatalities,
        "crew_missing": event.crew_missing,
        "fire_status": event.fire_status,
        "pollution_status": event.pollution_status,
        "port_status": event.port_status,
        "navigation_status": event.navigation_status,
        "cargo_status": event.cargo_status,
        "operational_status": event.operational_status,
        "content_fingerprint": event.content_fingerprint,
        "version": event.version,
        "notification_state": event.notification_state,
        "change_reason": event.change_reason,
        "last_run_id": run_id,
    }


def _to_iso(dt) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def apply_persistent_memory(events: list[MaritimeEvent], store: EventStore,
                            run_id: str, run_time: datetime,
                            risk_rules: dict, memory_rules: dict, scorer,
                            baseline_mode: Optional[str] = None) -> dict:
    """
    主要進入點。回傳：
      {
        "run_id", "notification_events", "all_current_events",
        "stats": {new/material/minor/unchanged/resolved 計數},
        "is_baseline_run", "baseline_mode",
      }
    DB 開啟/寫入失敗一律讓 EventStoreError 往上拋（呼叫端 = production
    entry point 必須 log ERROR + exit non-zero，不可 silent fallback，
    見 §四十六）。
    """
    baseline_mode = baseline_mode or resolve_baseline_mode(memory_rules)
    is_baseline_run = store.count_events() == 0

    store.start_run(run_id, run_time)

    identity = EventIdentityBuilder()
    matcher = PersistentEventMatcher(memory_rules, risk_rules, identity)
    status_extractor = StatusExtractor(memory_rules)
    detector = MaterialChangeDetector(memory_rules)
    lifecycle = EventLifecycleManager(memory_rules)
    notifier = NotificationPolicy(memory_rules)

    touched_event_ids: set[str] = set()
    stats = {"new": 0, "material": 0, "minor": 0, "unchanged": 0, "resolved": 0}
    notification_events: list[MaritimeEvent] = []
    all_current_events: list[MaritimeEvent] = []

    for event in events:
        primary = event.primary_article
        text_title = primary.title if primary else event.headline
        text_summary = primary.summary if primary else ""
        incoming_text = f"{text_title} {text_summary}"

        # ── 1. 先用「這次 run 自己抽取到的欄位」做跨 run 比對 ──────
        #    （不能先合併舊資料再比對，否則 CASE 3-K「不同船名」的
        #    hard reject 會被舊船名蓋掉，變成永遠比對成功）
        matched_event_id, signals = matcher.find_match(event, store, now=run_time)
        old_row = store.get_event(matched_event_id) if matched_event_id else None

        if old_row is None:
            event.event_id = identity.generate_event_id(signals.canonical_key)
            event.version = 1
        else:
            event.event_id = old_row["event_id"]
            touched_event_ids.add(event.event_id)

        # ── 2. Rule-based operational status extraction（§二十九〜三十）──
        status_fields = status_extractor.extract(text_title, text_summary)

        # ── 3. 欄位合併：新資訊優先，缺資料 fall back 既有事實（§十五）──
        if old_row is not None:
            event.vessel_name    = _merge(event.vessel_name, old_row.get("vessel_name"))
            event.vessel_type    = _merge(event.vessel_type, old_row.get("vessel_type"))
            event.carrier         = _merge(event.carrier, old_row.get("carrier"))
            event.sea_area          = _merge(event.sea_area, old_row.get("sea_area"))
            event.location            = _merge(event.location, old_row.get("location"))
            event.region                = _merge(event.region, old_row.get("region"))
            event.port                    = _merge(event.port, old_row.get("port"))
            event.shipping_lane             = _merge(event.shipping_lane, old_row.get("shipping_lane"))
            event.incident_subtype            = _merge(event.incident_subtype, old_row.get("incident_subtype"))
            event.imo_number                    = _merge(event.imo_number, old_row.get("imo_number"))

        for field in ("vessel_status", "casualty_status", "fire_status", "pollution_status",
                     "port_status", "navigation_status", "operational_status"):
            new_val = status_fields.get(field)
            old_val = old_row.get(field) if old_row else None
            setattr(event, field, _merge(new_val, old_val))
        for field in ("crew_injured", "crew_fatalities", "crew_missing"):
            new_val = status_fields.get(field)
            old_val = old_row.get(field) if old_row else None
            setattr(event, field, _merge(new_val, old_val))

        # ── 3b. Source Independence 必須跨 run 累計（§十二〜十七 延伸）──
        # event.independent_source_tiers 這時候還只反映『這次 run 自己這批
        # 文章』（EventClusterer 只看得到當次 run），如果不跟資料庫裡既有
        # 文章的 family/tier 合併，confidence 會在每個 run 重新從 1 個來源
        # 起算，永遠不可能因為「又有一家獨立媒體報導」而真正升到 HIGH
        # （CASE 3-B）。用 event_articles 表裡累積的所有文章重新計算。
        if old_row is not None:
            cumulative_tiers, cumulative_count = _cumulative_independent_tiers(
                store, event.event_id, event.articles
            )
            event.independent_source_tiers = cumulative_tiers
            event.independent_source_count = cumulative_count
            event.article_count = (old_row.get("article_count") or 0) + len(event.articles)

        # ── 4. 合併後重新評分（讓「後來確認是萬海」反映在 priority 上，§七十五）──
        scorer.score_event(event, now=run_time)

        # ── 5. Lifecycle（RESOLVED / REOPENED / EXPIRED-recovery）────────
        old_status = old_row.get("event_status") if old_row else None
        new_status, lifecycle_change_type = lifecycle.apply_incoming(old_status, incoming_text)
        event.event_status = new_status

        # ── 6. Material Change Detection ─────────────────────────────
        new_snapshot = build_snapshot(event)
        if old_row is not None:
            old_snapshot = build_snapshot(old_row)
            changes = detector.compare(old_snapshot, new_snapshot)
            notification_component, material = detector.classify(changes)
        else:
            changes = []
            notification_component, material = "NEW", True

        history_entries: list[dict] = []
        if old_row is None:
            history_entries.append({
                "change_type": "EVENT_CREATED", "old_value": None,
                "new_value": new_snapshot, "material": True,
                "change_reason": "First seen",
            })
            notification_state = NotificationState.NEW
        else:
            for c in changes:
                history_entries.append(c)
            if lifecycle_change_type == "RESOLVED":
                notification_state = NotificationState.RESOLVED_UPDATE
                material = True
                history_entries.append({
                    "change_type": "RESOLVED", "old_value": old_status,
                    "new_value": new_status, "material": True,
                    "change_reason": "Resolution confirmed by source text",
                })
            elif lifecycle_change_type == "REOPENED":
                notification_state = NotificationState.MATERIAL_UPDATE
                material = True
                history_entries.append({
                    "change_type": "REOPENED", "old_value": old_status,
                    "new_value": new_status, "material": True,
                    "change_reason": "Event reopened after new development",
                })
            elif notification_component == "MATERIAL_UPDATE":
                notification_state = NotificationState.MATERIAL_UPDATE
            elif notification_component == "MINOR_UPDATE":
                notification_state = NotificationState.MINOR_UPDATE
            else:
                notification_state = NotificationState.UNCHANGED

        event.notification_state = notification_state
        event.canonical_key = identity.canonical_key(event)
        event.version = (old_row.get("version", 1) + 1) if (old_row and material) else \
                        (old_row.get("version", 1) if old_row else 1)
        event.last_material_update = run_time if (material or old_row is None) else \
                                     parse_iso(old_row.get("last_material_update_utc"))
        if event.first_seen is None and old_row is not None:
            event.first_seen = parse_iso(old_row.get("first_seen_utc"))
        elif old_row is not None:
            # first_seen 永遠沿用資料庫裡最早的一次，不因這次 run 而改變
            old_first_seen = parse_iso(old_row.get("first_seen_utc"))
            if old_first_seen is not None:
                event.first_seen = old_first_seen
        if event.last_updated is None:
            event.last_updated = run_time

        material_reasons = [c["change_reason"] for c in changes if c.get("material")]
        if lifecycle_change_type == "RESOLVED":
            material_reasons.append("Resolution confirmed by source text")
        elif lifecycle_change_type == "REOPENED":
            material_reasons.append("Event reopened after new development")
        event.change_reason = "; ".join(material_reasons) if material_reasons else \
                              (old_row.get("change_reason") if old_row else None)
        event.run_id = run_id

        # RESOLVED_UPDATE 的通知判斷用『事件解除前曾經達到的最高 Priority』，
        # 不是解除當下重新算出來的（通常會下降的）Priority——主管需要被
        # 告知「原本的 P1/P2 事件解除了」，不能因為解除當下的文字severity
        # 較低就被悄悄壓下通知（§三十四 RESOLVED_UPDATE 規則）。
        notify_priority = event.management_priority
        if notification_state == NotificationState.RESOLVED_UPDATE and old_row is not None:
            notify_priority = _better_priority(event.management_priority,
                                               old_row.get("management_priority"))

        should_notify, reason = notifier.decide(
            notification_state, notify_priority, event.confidence_level,
            event.information_status, event.event_status, material_reasons,
        )
        if is_baseline_run and baseline_mode == "silent":
            should_notify = False
            reason = reason + " [baseline import — notification suppressed]"
        event.should_notify = should_notify
        event.notification_reason = reason

        # ── 7. Persist（Event + Articles + History 同一個 transaction，§四十三）──
        event_row = _event_row_for_db(event, run_id)
        articles_db = [_article_to_db_dict(a) for a in event.articles]
        result = store.persist_event_update(event_row, articles_db, history_entries, run_id)

        if old_row is not None and result["new_articles"]:
            store.insert_history(
                event.event_id, "SOURCE_ADDED", None,
                {"new_article_ids": result["new_articles"]},
                f"{len(result['new_articles'])} additional source article(s) recorded",
                material=False, run_id=run_id,
            )

        all_current_events.append(event)
        if should_notify:
            notification_events.append(event)

        if notification_state == NotificationState.NEW:
            stats["new"] += 1
        elif notification_state == NotificationState.MATERIAL_UPDATE:
            stats["material"] += 1
        elif notification_state == NotificationState.MINOR_UPDATE:
            stats["minor"] += 1
        elif notification_state == NotificationState.UNCHANGED:
            stats["unchanged"] += 1
        elif notification_state == NotificationState.RESOLVED_UPDATE:
            stats["resolved"] += 1

    # ── 8. Lifecycle Sweep：這次沒被匹配到的既有事件也要檢查 MONITORING/EXPIRED ──
    sweep_results = lifecycle.sweep(store, now=run_time, touched_event_ids=touched_event_ids)
    for r in sweep_results:
        store.upsert_event({"event_id": r["event_id"], "event_status": r["new_status"]})
        store.insert_history(
            r["event_id"], r["change_type"] or "MONITORING",
            r["old_status"], r["new_status"],
            "Lifecycle sweep: no recent activity" if r["change_type"] == "EXPIRED"
            else "Lifecycle sweep: no recent material update",
            material=bool(r["change_type"]), run_id=run_id,
        )

    return {
        "run_id": run_id,
        "notification_events": notification_events,
        "all_current_events": all_current_events,
        "stats": stats,
        "is_baseline_run": is_baseline_run,
        "baseline_mode": baseline_mode,
        "matcher_diagnostics": matcher.diagnostics,
        "expired_this_run": [r for r in sweep_results if r["change_type"] == "EXPIRED"],
        "monitoring_this_run": [r for r in sweep_results if r["change_type"] is None],
    }


def print_persistent_memory_report(run_id: str, total_articles: int,
                                   current_run_events: int, memory_result: dict) -> None:
    """§五十二 Console Output。不印 SQL debug，只印彙整數字。"""
    stats = memory_result["stats"]
    notified = len(memory_result["notification_events"])
    all_events = len(memory_result["all_current_events"])
    existing_matched = sum(
        1 for e in memory_result["all_current_events"]
        if e.notification_state != NotificationState.NEW
    )
    suppressed = all_events - notified

    lines = [
        "",
        "=" * 60,
        "🧠 PERSISTENT INTELLIGENCE MEMORY",
        "=" * 60,
        f"Run ID: {run_id}",
        "",
        "Current Run:",
        f"  Articles collected: {total_articles}",
        f"  Current-run events: {current_run_events}",
        "",
        "Memory Match:",
        f"  Existing events matched: {existing_matched}",
        f"  New events: {stats['new']}",
        "",
        "Lifecycle:",
        f"  NEW: {stats['new']}",
        f"  MATERIAL UPDATE: {stats['material']}",
        f"  MINOR UPDATE: {stats['minor']}",
        f"  UNCHANGED: {stats['unchanged']}",
        f"  RESOLVED: {stats['resolved']}",
        f"  EXPIRED (swept, no new article this run): {len(memory_result['expired_this_run'])}",
        "",
        "Notifications:",
        f"  Eligible: {notified}",
        f"  Suppressed (no management-relevant change): {suppressed}",
    ]
    if memory_result["is_baseline_run"] and memory_result["baseline_mode"] == "silent":
        lines.append("")
        lines.append("⚠️  Baseline import run (MEMORY_BASELINE_MODE=silent) — "
                     "events stored but notifications suppressed.")
    lines.append("=" * 60)
    logger.info("\n".join(lines))
