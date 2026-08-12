#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard/view_models.py
海事航運新聞監控系統 — Phase 7 Dashboard 展示用格式化工具

職責：
  純格式化函式（UTC → TPE 顯示、Priority/Exposure 徽章顏色），供
  Jinja2 template 當 filter 用。不含任何查詢邏輯（那是 service.py 的
  職責）、不含任何風險判斷（Priority/Exposure 本身已經由 Phase 1-6
  決定好）。

  ★ 色彩系統沿用 Phase 4（§六十八）：
    Priority — P1 Red / P2 Orange / P3 Amber / Resolved Green
    Exposure — DIRECT Red / HIGH Orange / MODERATE Amber / LOW Blue-Gray /
               NONE Gray / UNAVAILABLE Neutral Gray
  ★ §六十九：Priority 徽章與 Exposure 徽章顏色故意選用不同色階
    （Exposure 用偏紫紅的 DIRECT 色號 vs Priority 用純紅），避免卡片上
    兩個紅色徽章讓人分不清楚哪個是哪個——渲染時務必都標上文字
    "EVENT PRIORITY" / "WHL EXPOSURE"，不能只靠顏色。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

_TZ = timezone(timedelta(hours=8))
_TZ_NAME = "TPE"

PRIORITY_COLOR = {
    "P1": "#dc2626", "P2": "#c2410c", "P3": "#b45309", "P4": "#64748b",
}
EXPOSURE_COLOR = {
    "DIRECT": "#7f1d1d", "HIGH": "#c2410c", "MODERATE": "#b45309",
    "LOW": "#64748b", "NONE": "#94a3b8", "UNAVAILABLE": "#475569",
    None: "#94a3b8", "NOT_ASSESSED": "#cbd5e1",
}
SITUATION_COLOR = {
    "HIGH": "#dc2626", "ELEVATED": "#c2410c", "WATCH": "#b45309", "NORMAL": "#15803d",
}
HEALTH_COLOR = {
    "HEALTHY": "#15803d", "DEGRADED": "#b45309", "CRITICAL": "#dc2626", "UNKNOWN": "#64748b",
}


def fmt_tpe(value) -> str:
    """UTC ISO 字串或 datetime → 'DD Mon YYYY HH:MM TPE' 顯示字串。"""
    if value is None:
        return "—"
    dt = value
    if isinstance(value, str):
        try:
            dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
    if not isinstance(dt, datetime):
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ).strftime(f"%d %b %Y %H:%M {_TZ_NAME}")


def fmt_hours(hours: Optional[float]) -> str:
    if hours is None:
        return "—"
    return f"{round(hours)}h"


def priority_color(priority: Optional[str]) -> str:
    return PRIORITY_COLOR.get(priority, "#64748b")


def exposure_color(level: Optional[str]) -> str:
    return EXPOSURE_COLOR.get(level, "#94a3b8")


def situation_color(situation: Optional[str]) -> str:
    return SITUATION_COLOR.get(situation, "#64748b")


def health_color(status: Optional[str]) -> str:
    return HEALTH_COLOR.get(status, "#64748b")


def exposure_label(level: Optional[str], status: Optional[str] = None) -> str:
    """§六十七：Provider Unavailable 顯示 'Unavailable'，不是 0 或空白。"""
    if status == "UNAVAILABLE":
        return "Unavailable"
    if status == "NOT_ASSESSED" or level is None:
        return "Not assessed"
    return level


def register_filters(env) -> None:
    """供 dashboard/app.py 註冊進 Jinja2 Environment。"""
    env.filters["fmt_tpe"] = fmt_tpe
    env.filters["fmt_hours"] = fmt_hours
    env.filters["priority_color"] = priority_color
    env.filters["exposure_color"] = exposure_color
    env.filters["situation_color"] = situation_color
    env.filters["health_color"] = health_color
    env.filters["exposure_label"] = exposure_label
