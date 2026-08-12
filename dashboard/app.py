#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard/app.py
海事航運新聞監控系統 — Phase 7 §三十二〜三十四、七十〜七十七 Management Dashboard

FastAPI + Jinja2 + minimal local CSS（無 SPA framework、無外部 CDN／
Analytics，§一百〜一百零一）。Dashboard 純讀取 Phase 3/6/7 既有的
SQLite 資料庫，不需要即時連上 scraper（§六十一：Read-Only Data Service）。

啟動方式（§七十四）：
    uvicorn dashboard.app:app --host 127.0.0.1 --port 8000
或：
    python dashboard/app.py

安全性（§七十二〜七十三）：
  - 預設 bind 127.0.0.1（localhost），不是 0.0.0.0（見 __main__）。
  - 可選 Basic Auth：DASHBOARD_AUTH_ENABLED=true 時，
    DASHBOARD_USERNAME / DASHBOARD_PASSWORD 從環境變數讀取，
    絕不硬編碼。
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Depends, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from event_store import EventStore, EventStoreError
from operational_history import open_operational_history, DEFAULT_OPERATIONAL_HISTORY_DB_PATH
from delivery_history import open_delivery_history, DEFAULT_DELIVERY_HISTORY_DB_PATH
from source_health import open_source_health_store, DEFAULT_SOURCE_HEALTH_DB_PATH
from system_health import SystemHealthService
from version import __version__ as APP_VERSION, version_banner

from dashboard.service import DashboardService
from dashboard import view_models

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
view_models.register_filters(templates.env)
# ★ Phase 8：版本號透過 Jinja2 global 注入，所有頁面（base.html footer）
# 都能顯示，不需要每個 route 都手動傳一次（Single Source of Truth: version.py）。
templates.env.globals["app_version"] = APP_VERSION

app = FastAPI(title="Maritime Intelligence Dashboard", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

_security = HTTPBasic(auto_error=False)


def _bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() not in ("false", "0", "no", "")


def require_auth(credentials: Optional[HTTPBasicCredentials] = Depends(_security)):
    """
    §七十三：Optional Basic Internal Auth。DASHBOARD_AUTH_ENABLED=false
    （預設）時完全不檢查，行為與沒有這個依賴一樣。啟用時，帳密一律從
    環境變數讀取，絕不硬編碼；比對使用 secrets.compare_digest 避免
    timing attack。
    """
    if not _bool_env("DASHBOARD_AUTH_ENABLED", False):
        return True
    expected_user = os.environ.get("DASHBOARD_USERNAME", "")
    expected_pass = os.environ.get("DASHBOARD_PASSWORD", "")
    if not credentials or not (
        secrets.compare_digest(credentials.username, expected_user)
        and secrets.compare_digest(credentials.password, expected_pass)
    ):
        raise HTTPException(status_code=401, detail="Unauthorized",
                             headers={"WWW-Authenticate": "Basic"})
    return True


def _try_open_event_store() -> Optional[EventStore]:
    path = os.environ.get("MARITIME_DB_PATH", "data/maritime_intelligence.db")
    try:
        return EventStore(path)
    except EventStoreError as e:
        # §六十二：DB 打不開，Dashboard 顯示 unavailable，不是整個 App 掛掉。
        import logging
        logging.getLogger(__name__).warning(f"⚠️  Dashboard 無法開啟 Event Store：{e}")
        return None


def get_dashboard_service():
    event_store = _try_open_event_store()
    operational_history = open_operational_history(
        os.environ.get("MARITIME_OPERATIONAL_HISTORY_DB_PATH", DEFAULT_OPERATIONAL_HISTORY_DB_PATH))
    delivery_history = open_delivery_history(
        os.environ.get("MARITIME_DELIVERY_HISTORY_DB_PATH", DEFAULT_DELIVERY_HISTORY_DB_PATH))
    source_health_store = open_source_health_store(
        os.environ.get("MARITIME_SOURCE_HEALTH_DB_PATH", DEFAULT_SOURCE_HEALTH_DB_PATH))

    llm_enabled = _bool_env("LLM_ENABLED", False)

    health_service = SystemHealthService(
        event_store=event_store, delivery_history_store=delivery_history,
        source_health_store=source_health_store,
        operational_provider_status={}, llm_enabled=llm_enabled,
    )
    service = DashboardService(event_store, operational_history, delivery_history,
                                source_health_store, health_service)
    try:
        yield service
    finally:
        if event_store is not None:
            event_store.close()
        for store in (operational_history, delivery_history, source_health_store):
            try:
                store.close()
            except Exception:
                pass


DASHBOARD_BASE_URL = os.environ.get("DASHBOARD_BASE_URL", "")


# ══════════════════════════════════════════════════════════════
# HTML 頁面（§三十六〜五十四）
# ══════════════════════════════════════════════════════════════
@app.get("/")
def overview_page(request: Request, svc: DashboardService = Depends(get_dashboard_service),
                   _auth: bool = Depends(require_auth)):
    data = svc.overview()
    return templates.TemplateResponse(request, "overview.html", {
        "data": data, "page": "overview",
        "company_name": "WAN HAI LINES",
    })


@app.get("/events")
def events_page(request: Request, svc: DashboardService = Depends(get_dashboard_service),
                 _auth: bool = Depends(require_auth),
                 priority: Optional[str] = None, event_type: Optional[str] = None,
                 event_status: Optional[str] = None, confidence: Optional[str] = None,
                 relevance_level: Optional[str] = None, region: Optional[str] = None,
                 carrier: Optional[str] = None, own_fleet: Optional[str] = None,
                 q: Optional[str] = None):
    events = svc.list_events(
        priority=priority or None, event_type=event_type or None,
        event_status=event_status or None, confidence=confidence or None,
        relevance_level=relevance_level or None, region=region or None,
        carrier=carrier or None, own_fleet_only=bool(own_fleet), search=q or None,
    )
    return templates.TemplateResponse(request, "events.html", {
        "events": events, "page": "events",
        "filters": {"priority": priority, "event_type": event_type, "event_status": event_status,
                    "confidence": confidence, "relevance_level": relevance_level, "region": region,
                    "carrier": carrier, "own_fleet": own_fleet, "q": q},
    })


@app.get("/events/{event_id}")
def event_detail_page(event_id: str, request: Request,
                       svc: DashboardService = Depends(get_dashboard_service),
                       _auth: bool = Depends(require_auth)):
    detail = svc.get_event_detail(event_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return templates.TemplateResponse(request, "event_detail.html", {
        "e": detail, "page": "events",
    })


@app.get("/fleet")
def fleet_page(request: Request, svc: DashboardService = Depends(get_dashboard_service),
                _auth: bool = Depends(require_auth)):
    vessels = svc.fleet_exposure()
    return templates.TemplateResponse(request, "fleet.html", {
        "vessels": vessels, "page": "fleet",
    })


@app.get("/ports")
def ports_page(request: Request, svc: DashboardService = Depends(get_dashboard_service),
                _auth: bool = Depends(require_auth)):
    ports = svc.port_exposure()
    return templates.TemplateResponse(request, "ports.html", {
        "ports": ports, "page": "ports",
    })


@app.get("/resolved")
def resolved_page(request: Request, svc: DashboardService = Depends(get_dashboard_service),
                   _auth: bool = Depends(require_auth), days: int = 7):
    events = svc.resolved_events(days=days)
    return templates.TemplateResponse(request, "resolved.html", {
        "events": events, "page": "resolved", "days": days,
    })


@app.get("/health")
def health_page(request: Request, svc: DashboardService = Depends(get_dashboard_service),
                 _auth: bool = Depends(require_auth)):
    report = svc.system_health()
    return templates.TemplateResponse(request, "health.html", {
        "r": report, "page": "health",
    })


# ══════════════════════════════════════════════════════════════
# Internal Read-Only API（§七十五〜七十七）
# ══════════════════════════════════════════════════════════════
@app.get("/api/summary")
def api_summary(svc: DashboardService = Depends(get_dashboard_service),
                 _auth: bool = Depends(require_auth)):
    return JSONResponse(_json_safe(svc.overview()))


@app.get("/api/events")
def api_events(svc: DashboardService = Depends(get_dashboard_service),
                _auth: bool = Depends(require_auth),
                priority: Optional[str] = None, relevance_level: Optional[str] = None):
    events = svc.list_events(priority=priority or None, relevance_level=relevance_level or None)
    return JSONResponse(_json_safe(events))


@app.get("/api/events/{event_id}")
def api_event_detail(event_id: str, svc: DashboardService = Depends(get_dashboard_service),
                      _auth: bool = Depends(require_auth)):
    detail = svc.get_event_detail(event_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return JSONResponse(_json_safe(detail))


@app.get("/api/fleet-exposure")
def api_fleet_exposure(svc: DashboardService = Depends(get_dashboard_service),
                        _auth: bool = Depends(require_auth)):
    return JSONResponse(_json_safe(svc.fleet_exposure()))


@app.get("/api/health")
def api_health(svc: DashboardService = Depends(get_dashboard_service),
                _auth: bool = Depends(require_auth)):
    """
    §七十七：只回傳 sanitized 狀態，絕不輸出 exception stack、DB path、
    webhook URL、SMTP/LLM 憑證。
    """
    try:
        r = svc.system_health()
        return JSONResponse({
            "status": r.overall_status,
            "last_run": (r.last_run or {}).get("started_at_utc"),
            "event_store": r.event_store_status,
            "email": r.email_status,
            "teams": r.teams_status,
            "operational_data": {
                "fleet": r.fleet_provider_status,
                "schedule": r.schedule_provider_status,
                "route": r.route_provider_status,
            },
            "llm": r.llm_status,
        })
    except Exception:
        return JSONResponse({"status": "UNKNOWN"}, status_code=200)


def _json_safe(obj):
    """把 dict 裡的 datetime 轉成 ISO 字串，供 JSONResponse 使用。"""
    import datetime as _dt
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, _dt.datetime):
        return obj.isoformat()
    return obj


if __name__ == "__main__":
    import uvicorn
    # §七十二：Development default 只 bind localhost，不對外公開。
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))
    uvicorn.run("dashboard.app:app", host=host, port=port, reload=False)
