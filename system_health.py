#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
system_health.py
海事航運新聞監控系統 — Phase 7 §五十三〜五十四、九十六〜九十九 System Health

職責：
  彙整 Event Store / Email / Teams / Fleet-Schedule-Route Provider /
  LLM / Source Health 六個面向，產生一份 SystemHealthReport，供
  Dashboard `/health` 頁面與 `/api/health` 使用。

  ★ Management vs System 分離（§五十七〜五十九）：這裡產生的報告只
  給系統管理者 / Dashboard 看，絕不會被拿去產生 Maritime Intelligence
  Teams/Email 通知——那條路徑完全是 delivery_orchestrator.py 依
  Event/Operational 兩條軸決定的，跟這裡無關。

  ★ Overall System Status 判定原則（§九十七〜九十八）：
    - Event Store 健康檢查失敗 → CRITICAL（Persistent Memory 是
      production-critical 依賴，見 event_store.py EventStoreError）。
    - 全部新聞來源都失敗 → CRITICAL（沒有任何情報可用）。
    - Email 最終發送失敗 → CRITICAL（既有 production policy：Email
      失敗要 exit(1)，見 maritime_news.py __main__）。
    - Teams 失敗、單一/部分 Source DOWN、Fleet/Schedule/Route Provider
      不可用 → DEGRADED（功能性下降，但核心情報流程仍在運作）。
    - LLM 停用或失敗 → 絕不算 CRITICAL，也不特別列入 DEGRADED 判定
      （§九十八：LLM 是 Optional Enhancement，只在 llm_status 欄位
      顯示 ENABLED/DISABLED 供參考）。
    - 以上皆非 → HEALTHY。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


class SystemStatus:
    HEALTHY  = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"


@dataclass
class SystemHealthReport:
    overall_status: str
    generated_at: datetime

    last_run: Optional[dict] = None

    event_store_status: str = "UNKNOWN"
    email_status: str = "UNKNOWN"
    teams_status: str = "UNKNOWN"

    fleet_provider_status: str = "UNKNOWN"
    schedule_provider_status: str = "UNKNOWN"
    route_provider_status: str = "UNKNOWN"

    llm_status: str = "DISABLED"

    source_health_summary: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


class SystemHealthService:
    def __init__(self, event_store, delivery_history_store, source_health_store,
                 operational_provider_status: Optional[dict] = None,
                 llm_enabled: bool = False):
        self.event_store = event_store
        self.delivery_history = delivery_history_store
        self.source_health_store = source_health_store
        self.operational_provider_status = operational_provider_status or {}
        self.llm_enabled = llm_enabled

    def build_report(self, now: Optional[datetime] = None) -> SystemHealthReport:
        now = now or datetime.now(timezone.utc)
        notes: list = []

        event_store_status = self._event_store_status(notes)
        last_run = self._last_run()

        email_status = self._channel_status("EMAIL", notes)
        teams_status = self._channel_status("TEAMS", notes)

        fleet_status = self.operational_provider_status.get("fleet", "UNKNOWN")
        schedule_status = self.operational_provider_status.get("schedule", "UNKNOWN")
        route_status = self.operational_provider_status.get("route", "UNKNOWN")
        for label, status in (("Fleet", fleet_status), ("Schedule", schedule_status), ("Route", route_status)):
            if status == "UNAVAILABLE":
                notes.append(f"{label} Provider unavailable")

        llm_status = "ENABLED" if self.llm_enabled else "DISABLED"

        source_summary = self._source_summary(notes)

        overall = self._compute_overall(
            event_store_status, email_status, teams_status,
            fleet_status, schedule_status, route_status, source_summary,
        )

        return SystemHealthReport(
            overall_status=overall, generated_at=now, last_run=last_run,
            event_store_status=event_store_status, email_status=email_status, teams_status=teams_status,
            fleet_provider_status=fleet_status, schedule_provider_status=schedule_status,
            route_provider_status=route_status, llm_status=llm_status,
            source_health_summary=source_summary, notes=notes,
        )

    # ── 個別面向 ─────────────────────────────────────────────
    def _event_store_status(self, notes: list) -> str:
        try:
            hc = self.event_store.health_check()
            if hc.get("ok"):
                return SystemStatus.HEALTHY
            notes.append("Persistent Event Store health check failed")
            return SystemStatus.CRITICAL
        except Exception:
            notes.append("Persistent Event Store unavailable")
            return SystemStatus.CRITICAL

    def _last_run(self) -> Optional[dict]:
        try:
            return self.event_store.get_latest_run()
        except Exception:
            return None

    def _channel_status(self, channel: str, notes: list) -> str:
        try:
            recent = self.delivery_history.recent(limit=50)
        except Exception:
            return "UNKNOWN"
        channel_records = [r for r in recent if r.get("channel") == channel]
        if not channel_records:
            return "UNKNOWN"
        latest = channel_records[0]   # recent() 已依 id DESC 排序
        status = latest.get("status")
        if status == "SENT":
            return SystemStatus.HEALTHY
        if status == "FAILED":
            notes.append(f"{channel} delivery failed on most recent attempt")
            return SystemStatus.DEGRADED
        return "UNKNOWN"

    def _source_summary(self, notes: list) -> dict:
        try:
            summary = self.source_health_store.summary()
        except Exception:
            summary = {"total": 0, "HEALTHY": 0, "DEGRADED": 0, "DOWN": 0, "UNKNOWN": 0}
        total = summary.get("total", 0)
        if total > 0 and summary.get("HEALTHY", 0) == 0:
            notes.append("All collection sources failed")
        elif summary.get("DOWN", 0) > 0 or summary.get("DEGRADED", 0) > 0:
            notes.append(f"{summary.get('DOWN', 0)} source(s) DOWN, {summary.get('DEGRADED', 0)} DEGRADED")
        return summary

    def _compute_overall(self, event_store_status, email_status, teams_status,
                          fleet_status, schedule_status, route_status, source_summary) -> str:
        if event_store_status == SystemStatus.CRITICAL:
            return SystemStatus.CRITICAL
        if source_summary.get("total", 0) > 0 and source_summary.get("HEALTHY", 0) == 0:
            return SystemStatus.CRITICAL
        if email_status == SystemStatus.DEGRADED:
            return SystemStatus.CRITICAL

        degraded_signals = (
            teams_status == SystemStatus.DEGRADED,
            fleet_status == "UNAVAILABLE", schedule_status == "UNAVAILABLE", route_status == "UNAVAILABLE",
            source_summary.get("DOWN", 0) > 0, source_summary.get("DEGRADED", 0) > 0,
        )
        if any(degraded_signals):
            return SystemStatus.DEGRADED
        return SystemStatus.HEALTHY

    def diagnostics_report(self, report: Optional[SystemHealthReport] = None) -> str:
        r = report or self.build_report()
        return (
            "SYSTEM HEALTH\n"
            f"  Overall: {r.overall_status}\n"
            f"  Event Store: {r.event_store_status}   Email: {r.email_status}   Teams: {r.teams_status}\n"
            f"  Fleet Provider: {r.fleet_provider_status}   Schedule Provider: {r.schedule_provider_status}   "
            f"Route Provider: {r.route_provider_status}\n"
            f"  LLM: {r.llm_status}\n"
            f"  Sources: {r.source_health_summary}\n"
            + ("\n".join(f"  ⚠ {n}" for n in r.notes) if r.notes else "  (no issues)")
        )
