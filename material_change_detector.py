#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
material_change_detector.py
海事航運新聞監控系統 — Phase 3 §二十四〜二十八 Material Change Detection

這是 Phase 3 最重要的 module 之一：判斷「這次比對到的既有事件，跟資料庫
裡存的上一版比起來，是否有 management significance 的變化」。

★ 核心設計（呼應 §二十六 排除清單）：
  這個 module 只比較 Structured Fact Snapshot（§二十八），完全不看
  article_count / headline 原始文字 / summary 原始文字 / URL / published
  time 格式。這在架構上就排除了「多一篇轉載」「標題措辭改變」「摘要多幾個
  字但資訊相同」被誤判成 Material Update 的可能性——因為這些欄位根本
  不在比較範圍內，不需要另外寫規則排除。

Material Update 規則對應（§二十五 A〜I）：
  A. Priority Escalation           → PRIORITY_CHANGED
  B. Significant Score Increase    → SEVERITY_CHANGED / MANAGEMENT_SCORE_INCREASED
  C. Casualty Development          → CASUALTY_UPDATE
  D. Vessel Condition               → VESSEL_STATUS_UPDATE
  E. Security Escalation            → SECURITY_ESCALATION
  F. Port / Navigation Change       → PORT_STATUS_UPDATE
  G. Confidence Upgrade（限 P1/P2） → CONFIDENCE_CHANGED / INFORMATION_STATUS_CHANGED
  H. Fleet Relevance Change         → FLEET_RELEVANCE_CHANGED
  I. Vessel Identity Revealed       → VESSEL_STATUS_UPDATE
"""

from __future__ import annotations

from typing import Optional

from models import ManagementPriority, ConfidenceLevel, InformationStatus


def build_snapshot(event) -> dict:
    """
    §二十八 Structured Fact Snapshot。只取「事實」欄位，不取
    article_count / headline 原始文字 / summary。event 可以是
    MaritimeEvent dataclass（本次 run）或 DB dict row（既有事件）。
    """
    def g(key, default=None):
        if isinstance(event, dict):
            return event.get(key, default)
        return getattr(event, key, default)

    return {
        "event_type":            g("event_type"),
        "incident_subtype":       g("incident_subtype"),
        "vessel_name":             g("vessel_name"),
        "vessel_type":              g("vessel_type"),
        "carrier":                   g("carrier"),
        "sea_area":                   g("sea_area"),
        "management_priority":        g("management_priority"),
        "management_score":            g("management_score"),
        "severity_score":                g("severity_score"),
        "fleet_relevance_score":          g("fleet_relevance_score"),
        "confidence_level":                g("confidence_level"),
        "information_status":               g("information_status"),
        "vessel_status":                     g("vessel_status"),
        "casualty_status":                    g("casualty_status"),
        "crew_injured":                        g("crew_injured"),
        "crew_fatalities":                      g("crew_fatalities"),
        "crew_missing":                          g("crew_missing"),
        "fire_status":                             g("fire_status"),
        "pollution_status":                         g("pollution_status"),
        "port_status":                                g("port_status"),
        "navigation_status":                            g("navigation_status"),
        "operational_status":                             g("operational_status"),
    }


def _rank_priority(p: Optional[str]) -> int:
    return ManagementPriority.RANK.get(p, 99)


def _rank_confidence(c: Optional[str]) -> int:
    order = {ConfidenceLevel.HIGH: 0, ConfidenceLevel.MEDIUM: 1, ConfidenceLevel.LOW: 2}
    return order.get(c, 99)


def _rank_information_status(s: Optional[str]) -> int:
    order = {InformationStatus.CONFIRMED: 0, InformationStatus.CORROBORATED: 1,
             InformationStatus.UNCONFIRMED: 2, InformationStatus.EARLY_SIGNAL: 3}
    return order.get(s, 99)


def _escalation_index(order: list[str], value: Optional[str]) -> Optional[int]:
    if value is None or value not in order:
        return None
    return order.index(value)


class MaterialChangeDetector:

    def __init__(self, memory_rules: dict):
        self.rules = memory_rules
        mc = memory_rules.get("material_change", {})
        self.score_delta_threshold = mc.get("score_delta_threshold", 15)
        self.priority_escalation_enabled = mc.get("priority_escalation", True)
        self.confidence_upgrade_for_high_priority = mc.get(
            "confidence_upgrade_for_high_priority", True
        )
        self.high_priority_set = set(mc.get("high_priority_set", ["P1", "P2"]))
        self.fleet_relevance_delta_threshold = mc.get("fleet_relevance_delta_threshold", 10)
        self.escalation_orders = memory_rules.get("status_escalation_order", {})

    # ── 單一規則：Priority Escalation（A）──────────────────────
    def _check_priority(self, old: dict, new: dict) -> list[dict]:
        changes = []
        op, npv = old.get("management_priority"), new.get("management_priority")
        if op and npv and op != npv:
            escalated = _rank_priority(npv) < _rank_priority(op)
            changes.append({
                "change_type": "PRIORITY_CHANGED",
                "old_value": op, "new_value": npv,
                "material": bool(escalated and self.priority_escalation_enabled),
                "change_reason": f"Priority {'escalated' if escalated else 'downgraded'} {op} → {npv}",
            })
        return changes

    # ── B. Significant Score Increase ──────────────────────────
    def _check_score(self, old: dict, new: dict) -> list[dict]:
        changes = []
        os_, ns_ = old.get("severity_score"), new.get("severity_score")
        if os_ is not None and ns_ is not None and (ns_ - os_) >= self.score_delta_threshold:
            changes.append({
                "change_type": "SEVERITY_CHANGED",
                "old_value": os_, "new_value": ns_, "material": True,
                "change_reason": f"Severity score increased {os_:.0f} → {ns_:.0f}",
            })
        om, nm = old.get("management_score"), new.get("management_score")
        if om is not None and nm is not None and (nm - om) >= self.score_delta_threshold:
            changes.append({
                "change_type": "MANAGEMENT_SCORE_INCREASED",
                "old_value": om, "new_value": nm, "material": True,
                "change_reason": f"Management score increased {om:.0f} → {nm:.0f}",
            })
        return changes

    # ── C. Casualty Development ────────────────────────────────
    def _check_casualty(self, old: dict, new: dict) -> list[dict]:
        changes = []
        oc, nc = old.get("casualty_status"), new.get("casualty_status")
        if oc != nc and nc is not None:
            order1 = self.escalation_orders.get("casualty_status", [])
            order2 = self.escalation_orders.get("casualty_status_missing", [])
            i_old1, i_new1 = _escalation_index(order1, oc), _escalation_index(order1, nc)
            i_old2, i_new2 = _escalation_index(order2, oc), _escalation_index(order2, nc)
            escalated = (
                (i_old1 is not None and i_new1 is not None and i_new1 > i_old1)
                or (i_new1 is not None and i_old1 is None)
                or (i_old2 is not None and i_new2 is not None and i_new2 > i_old2)
                or (i_new2 is not None and i_old2 is None and nc == "MISSING")
            )
            changes.append({
                "change_type": "CASUALTY_UPDATE",
                "old_value": oc, "new_value": nc, "material": True,
                "change_reason": f"Casualty status changed {oc or 'unknown'} → {nc}"
                                  if escalated else f"Casualty status update: {oc or 'unknown'} → {nc}",
            })
        for field, label in (("crew_injured", "injured"), ("crew_fatalities", "fatalities"),
                             ("crew_missing", "missing")):
            ov, nv = old.get(field), new.get(field)
            if nv is not None and (ov is None or nv > ov):
                changes.append({
                    "change_type": "CASUALTY_UPDATE",
                    "old_value": ov, "new_value": nv, "material": True,
                    "change_reason": f"Crew {label} count updated: {ov if ov is not None else 'unknown'} → {nv}",
                })
        return changes

    # ── D. Vessel Condition ─────────────────────────────────────
    def _check_vessel_condition(self, old: dict, new: dict) -> list[dict]:
        changes = []
        ov, nv = old.get("vessel_status"), new.get("vessel_status")
        if ov != nv and nv is not None:
            changes.append({
                "change_type": "VESSEL_STATUS_UPDATE",
                "old_value": ov, "new_value": nv, "material": True,
                "change_reason": f"Vessel status changed {ov or 'unknown'} → {nv}",
            })
        of, nf = old.get("fire_status"), new.get("fire_status")
        if of != nf and nf is not None:
            changes.append({
                "change_type": "VESSEL_STATUS_UPDATE",
                "old_value": of, "new_value": nf, "material": True,
                "change_reason": f"Fire status changed {of or 'unknown'} → {nf}",
            })
        return changes

    # ── E. Security Escalation ──────────────────────────────────
    def _check_security(self, old: dict, new: dict) -> list[dict]:
        changes = []
        if new.get("event_type") == "SECURITY" or old.get("event_type") == "SECURITY":
            os_, ns_ = old.get("incident_subtype"), new.get("incident_subtype")
            if os_ and ns_ and os_ != ns_:
                changes.append({
                    "change_type": "SECURITY_ESCALATION",
                    "old_value": os_, "new_value": ns_, "material": True,
                    "change_reason": f"Security incident type changed {os_} → {ns_}",
                })
        return changes

    # ── F. Port / Navigation Change ─────────────────────────────
    def _check_port_navigation(self, old: dict, new: dict) -> list[dict]:
        changes = []
        for field in ("port_status", "navigation_status"):
            ov, nv = old.get(field), new.get(field)
            if ov != nv and nv is not None:
                changes.append({
                    "change_type": "PORT_STATUS_UPDATE",
                    "old_value": ov, "new_value": nv, "material": True,
                    "change_reason": f"{field.replace('_', ' ').title()} changed {ov or 'unknown'} → {nv}",
                })
        return changes

    # ── G. Confidence Upgrade（限 P1/P2）─────────────────────────
    def _check_confidence(self, old: dict, new: dict) -> list[dict]:
        changes = []
        priority = new.get("management_priority") or old.get("management_priority")
        is_high_priority = priority in self.high_priority_set

        oc, nc = old.get("confidence_level"), new.get("confidence_level")
        if oc != nc and nc is not None:
            upgraded = _rank_confidence(nc) < _rank_confidence(oc)
            material = bool(
                upgraded and is_high_priority and self.confidence_upgrade_for_high_priority
            )
            changes.append({
                "change_type": "CONFIDENCE_CHANGED",
                "old_value": oc, "new_value": nc, "material": material,
                "change_reason": (f"Confidence upgraded {oc} → {nc} on {priority} event"
                                  if material else f"Confidence changed {oc} → {nc}"),
            })

        oi, ni = old.get("information_status"), new.get("information_status")
        if oi != ni and ni is not None:
            upgraded = _rank_information_status(ni) < _rank_information_status(oi)
            material = bool(
                upgraded and is_high_priority and self.confidence_upgrade_for_high_priority
            )
            changes.append({
                "change_type": "INFORMATION_STATUS_CHANGED",
                "old_value": oi, "new_value": ni, "material": material,
                "change_reason": (f"Information upgraded from {oi} to {ni} on {priority} event"
                                  if material else f"Information status changed {oi} → {ni}"),
            })
        return changes

    # ── H. Fleet Relevance Change ────────────────────────────────
    def _check_fleet_relevance(self, old: dict, new: dict) -> list[dict]:
        changes = []
        ov, nv = old.get("fleet_relevance_score"), new.get("fleet_relevance_score")
        if ov is not None and nv is not None and (nv - ov) >= self.fleet_relevance_delta_threshold:
            changes.append({
                "change_type": "FLEET_RELEVANCE_CHANGED",
                "old_value": ov, "new_value": nv, "material": True,
                "change_reason": f"Fleet relevance increased {ov:.0f} → {nv:.0f}",
            })
        return changes

    # ── I. Vessel Identity Revealed ───────────────────────────────
    def _check_identity_revealed(self, old: dict, new: dict) -> list[dict]:
        changes = []
        ov, nv = old.get("vessel_name"), new.get("vessel_name")
        if not ov and nv:
            changes.append({
                "change_type": "VESSEL_STATUS_UPDATE",
                "old_value": ov, "new_value": nv, "material": True,
                "change_reason": f"Vessel identified as {nv}",
            })
        oc, nc = old.get("carrier"), new.get("carrier")
        if not oc and nc:
            changes.append({
                "change_type": "FLEET_RELEVANCE_CHANGED",
                "old_value": oc, "new_value": nc, "material": True,
                "change_reason": f"Operator identified as {nc}",
            })
        return changes

    # ── 主入口 ────────────────────────────────────────────────
    def compare(self, old_snapshot: dict, new_snapshot: dict) -> list[dict]:
        changes: list[dict] = []
        changes += self._check_priority(old_snapshot, new_snapshot)
        changes += self._check_score(old_snapshot, new_snapshot)
        changes += self._check_casualty(old_snapshot, new_snapshot)
        changes += self._check_vessel_condition(old_snapshot, new_snapshot)
        changes += self._check_security(old_snapshot, new_snapshot)
        changes += self._check_port_navigation(old_snapshot, new_snapshot)
        changes += self._check_confidence(old_snapshot, new_snapshot)
        changes += self._check_fleet_relevance(old_snapshot, new_snapshot)
        changes += self._check_identity_revealed(old_snapshot, new_snapshot)
        return changes

    @staticmethod
    def classify(changes: list[dict]) -> tuple[str, bool]:
        """
        回傳 (notification_state_component, material)。
        notification_state_component 是 'MATERIAL_UPDATE' / 'MINOR_UPDATE' / 'UNCHANGED'
        —— 呼叫端（pipeline）還要再疊加 NEW / RESOLVED_UPDATE 的判斷。
        """
        if any(c["material"] for c in changes):
            return "MATERIAL_UPDATE", True
        if changes:
            return "MINOR_UPDATE", False
        return "UNCHANGED", False
