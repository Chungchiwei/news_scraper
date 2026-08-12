#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
models.py  v1.0
海事航運新聞監控系統 — Phase 2 統一資料模型

職責：
  定義 NewsArticle / MaritimeEvent 兩個核心 dataclass，
  取代目前各 scraper 直接互丟 dict 的作法。

設計原則（對應 CLAUDE.md Phase 2 §三/§四）：
  - 只用標準庫 dataclasses，不引入 pydantic / attrs 等大型 framework。
  - 未知欄位一律 None，不得用猜測值填充。
  - Article 與 Event 分開：一個 Event 可以包含 1..N 篇 Article。
  - 本檔案不含任何抓取 / 評分邏輯，純資料結構。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ══════════════════════════════════════════════════════════════
# 常數命名空間（避免 enum.Enum 造成 JSON 序列化麻煩，
# 用純字串常數 + 簡單類別做命名空間即可）
# ══════════════════════════════════════════════════════════════
class EventType:
    """新版事件分類（與舊版 CAT1-6 分開，見 event_extractor.py）"""
    SAFETY      = "SAFETY"
    SECURITY    = "SECURITY"
    CREW        = "CREW"
    OPERATIONS  = "OPERATIONS"
    REGULATORY  = "REGULATORY"
    ENVIRONMENT = "ENVIRONMENT"
    MARKET      = "MARKET"
    COMPETITOR  = "COMPETITOR"
    OTHER       = "OTHER"

    ALL = (SAFETY, SECURITY, CREW, OPERATIONS, REGULATORY,
           ENVIRONMENT, MARKET, COMPETITOR, OTHER)


class SourceTier:
    """來源可信度分級。A 最高，D 最低（未知/未分類一律視為 C）。"""
    A = "A"   # Official / Primary（IMO、政府、官方航商公告...）
    B = "B"   # Professional / Major Media（Reuters、Lloyd's List...）
    C = "C"   # 一般新聞／產業網站
    D = "D"   # Reddit / forum / community / 未驗證

    ORDER = (A, B, C, D)          # 由高至低
    SCORE = {A: 10, B: 8, C: 5, D: 2}


class ManagementPriority:
    """主管優先級（與 event_type/incident_category 完全獨立）。"""
    P1 = "P1"   # IMMEDIATE ATTENTION
    P2 = "P2"   # MANAGEMENT WATCH
    P3 = "P3"   # INDUSTRY WATCH
    P4 = "P4"   # REFERENCE

    ORDER = (P1, P2, P3, P4)      # 由高至低，供排序使用
    RANK  = {P1: 0, P2: 1, P3: 2, P4: 3}


class ConfidenceLevel:
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"

    ORDER = (HIGH, MEDIUM, LOW)


class EventStatus:
    """
    Phase 3 §十八〜十九：Event Lifecycle Status —— 回答「事件現在處於什麼生命週期」。
    這是第三個獨立於 management_priority / information_status 的欄位，
    不可把 CONFIRMED 之類的 information_status 值塞進這裡。

    ACTIVE     — 目前活躍，持續有新資訊或屬於近期事故
    MONITORING — 仍在追蹤，但已一段時間沒有 Material Update（見 memory_rules.json
                 的 monitoring_after_days）
    RESOLVED   — 已有明確文字證據顯示事件落幕（例如 fire extinguished / port reopened）
    EXPIRED    — 長期沒有更新且未明確 RESOLVED，依 event_type 設定的 expiry_days 過期
    """
    ACTIVE     = "ACTIVE"
    MONITORING = "MONITORING"
    RESOLVED   = "RESOLVED"
    EXPIRED    = "EXPIRED"

    ORDER = (ACTIVE, MONITORING, RESOLVED, EXPIRED)


class NotificationState:
    """
    Phase 3 §二十〜二十六：這一次 execution，這個事件是否「值得再次通知主管」。
    與 EventStatus / ManagementPriority / InformationStatus 完全獨立的第四個概念。

    NEW              — 資料庫中找不到匹配的既有事件，第一次看到
    MATERIAL_UPDATE  — 匹配到既有事件，且結構化事實有 management-significant 的變化
    MINOR_UPDATE      — 匹配到既有事件，有變化（例如新來源、措辭）但不影響主管決策
    UNCHANGED        — 匹配到既有事件，結構化事實完全沒有變化
    RESOLVED_UPDATE   — 這次比對確認事件已经 RESOLVED（落幕），是特殊的 Material Update
    """
    NEW              = "NEW"
    MATERIAL_UPDATE  = "MATERIAL_UPDATE"
    MINOR_UPDATE     = "MINOR_UPDATE"
    UNCHANGED        = "UNCHANGED"
    RESOLVED_UPDATE  = "RESOLVED_UPDATE"

    ORDER = (NEW, MATERIAL_UPDATE, MINOR_UPDATE, UNCHANGED, RESOLVED_UPDATE)


class InformationStatus:
    """
    Phase 2.1 §六：Priority 與 Confidence 必須完全解耦。
    information_status 回答的是「這件事有多確定是真的」，
    management_priority 回答的是「主管要多快知道」——兩者互不影響對方的計算。

    CONFIRMED    — 有 Tier A（官方/權威）來源
    CORROBORATED — 2+ 個獨立 Tier B 來源互相佐證
    UNCONFIRMED  — 單一 Tier B 或 Tier C 來源，內容具體但未交叉驗證
    EARLY_SIGNAL — 只有 Tier D（Reddit/社群/未驗證）來源
    """
    CONFIRMED    = "CONFIRMED"
    CORROBORATED = "CORROBORATED"
    UNCONFIRMED  = "UNCONFIRMED"
    EARLY_SIGNAL = "EARLY_SIGNAL"

    ORDER = (CONFIRMED, CORROBORATED, UNCONFIRMED, EARLY_SIGNAL)


# ══════════════════════════════════════════════════════════════
# NewsArticle — 單篇文章
# ══════════════════════════════════════════════════════════════
@dataclass
class NewsArticle:
    # ── 識別 / 來源 ──────────────────────────────────────────
    article_id:      str
    source_name:     str
    source_category: Optional[str] = None     # 中文媒體/航運專業/航商動態/國際媒體...
    source_lang:     Optional[str] = None      # zh-TW / zh-CN / en
    source_tier:     Optional[str] = None      # A/B/C/D（見 SourceTier）

    # ── Phase 2.1 §十三 Source Provenance（schema 預留，即使目前無法
    #    完全判斷 original_source，也要保留欄位供後續填入）──────────
    source_domain:   Optional[str] = None      # 例如 reuters.com
    original_source: Optional[str] = None      # 偵測到的原始出處（例如轉載自 Reuters 時填 "Reuters"）
    source_family:   Optional[str] = None      # 正規化後的來源家族代碼，例如 "REUTERS"；預設等於 source_name

    # ── 內容 ────────────────────────────────────────────────
    title:   str = ""
    summary: str = ""
    url:     Optional[str] = None

    # ── 時間 ────────────────────────────────────────────────
    published_at: Optional[datetime] = None    # UTC，來源未提供時為 None
    collected_at: Optional[datetime] = None    # UTC，pipeline 收集當下時間

    # ── 關鍵字 / 分類 ────────────────────────────────────────
    matched_keywords:  list[str] = field(default_factory=list)
    incident_category: Optional[str] = None    # 舊版 CAT1-6/OTHER（保留，見§五）
    event_type:        Optional[str] = None    # 新版 EventType（粗分類）
    incident_subtype:  Optional[str] = None    # Phase 2.1：更細緻的事件子類型，
                                                # 例如 COLLISION / GROUNDING / FIRE / VESSEL_ATTACK，
                                                # 用來讓 clustering 能區分「同類但不同事故」

    # ── Event Extraction 結果（可能是 None，不得猜測）────────
    vessel_name: Optional[str] = None
    vessel_type: Optional[str] = None
    imo_number:  Optional[str] = None          # Phase 2.1 §四：schema 預留，目前無來源可抽取，恆為 None

    carrier: Optional[str] = None

    location:       Optional[str] = None       # 顯示用地名（正規化前的原始命中詞）
    country:        Optional[str] = None
    region:         Optional[str] = None
    port:           Optional[str] = None
    sea_area:       Optional[str] = None        # 正規化後的區域代碼，如 RED_SEA
    shipping_lane:  Optional[str] = None

    # ── Risk Scoring（由 risk_scorer.py 填入，Article 層級先計算，
    #    Event 層級再彙整/取最大值）──────────────────────────
    severity_score:            Optional[float] = None
    relevance_score:            Optional[float] = None   # = fleet relevance
    immediacy_score:            Optional[float] = None
    operational_impact_score:   Optional[float] = None
    source_confidence_score:    Optional[float] = None

    management_score:    Optional[float] = None
    management_priority: Optional[str] = None   # P1-P4

    confidence: Optional[str] = None            # HIGH/MEDIUM/LOW（單篇文章層級）

    # ── Clustering 結果 ─────────────────────────────────────
    event_id:   Optional[str] = None
    cluster_id: Optional[str] = None

    # ── 內部相容欄位（非規格必要，僅供 Phase 2 相容層還原舊版
    #    dict 結構使用，不對外承諾穩定性）─────────────────────
    raw_legacy: Optional[dict] = field(default=None, repr=False, compare=False)

    # ── 便利方法 ─────────────────────────────────────────────
    def normalized_title(self) -> str:
        """回傳已正規化標題；尚未跑過 normalize_title() 時退回原始標題。"""
        return getattr(self, "_normalized_title", None) or self.title


# ══════════════════════════════════════════════════════════════
# MaritimeEvent — 聚類後的事件（1..N 篇 Article）
# ══════════════════════════════════════════════════════════════
@dataclass
class MaritimeEvent:
    event_id: str
    headline: str

    event_type:        Optional[str] = None
    incident_subtype:  Optional[str] = None     # Phase 2.1：事件層級代表子類型（取 primary article）
    incident_category: Optional[str] = None     # 舊版 CAT 的代表值（取 primary article）

    first_seen:   Optional[datetime] = None
    last_updated: Optional[datetime] = None

    primary_article: Optional[NewsArticle] = None
    articles: list[NewsArticle] = field(default_factory=list)

    vessel_name: Optional[str] = None
    vessel_type: Optional[str] = None
    carrier:     Optional[str] = None

    location:      Optional[str] = None
    country:       Optional[str] = None
    region:        Optional[str] = None
    port:          Optional[str] = None
    sea_area:      Optional[str] = None
    shipping_lane: Optional[str] = None

    severity_score:            Optional[float] = None
    fleet_relevance_score:     Optional[float] = None
    immediacy_score:           Optional[float] = None
    operational_impact_score:  Optional[float] = None
    source_confidence_score:   Optional[float] = None

    management_score:    Optional[float] = None
    management_priority: Optional[str] = None    # P1-P4 — 只回答「主管要多快知道」

    confidence_level:    Optional[str] = None     # HIGH/MEDIUM/LOW — 只回答「這個評分/彙整有多可信」
    information_status:  Optional[str] = None     # Phase 2.1：CONFIRMED/CORROBORATED/UNCONFIRMED/EARLY_SIGNAL
                                                   # — 只回答「這件事有多確定是真的」。
                                                   # ★ 三者完全獨立：P1 + EARLY_SIGNAL 是合法組合
                                                   #   （見 Phase 2.1 §五：Critical Override 可以讓一則
                                                   #   只有 Reddit 來源的萬海火災謠言變成 P1，
                                                   #   但 information_status 仍然是 EARLY_SIGNAL，
                                                   #   不得因為 Priority 高就被誤寫成「已證實」）。

    impact_tags: list[str] = field(default_factory=list)

    event_status: Optional[str] = None             # Phase 3：ACTIVE/MONITORING/RESOLVED/EXPIRED（見 EventStatus）

    # ── Phase 3 §二十九 Operational Fields（rule-based 抽取，無證據一律 None，
    #    不得猜測數字）──────────────────────────────────────────
    vessel_status:      Optional[str] = None        # UNDERWAY/DISABLED/GROUNDED/REFLOATED/ABANDONED/UNDER_TOW/SANK
    casualty_status:    Optional[str] = None        # NONE_REPORTED/INJURED/FATALITY/MISSING
    crew_injured:       Optional[int] = None
    crew_fatalities:    Optional[int] = None
    crew_missing:       Optional[int] = None
    fire_status:        Optional[str] = None        # ONGOING/EXTINGUISHED
    pollution_status:   Optional[str] = None        # REPORTED/CONTAINED
    port_status:        Optional[str] = None        # CONGESTED/CLOSED/REOPENED
    navigation_status:  Optional[str] = None        # RESTRICTED/CLOSED/REOPENED
    cargo_status:       Optional[str] = None        # 目前無專門字典，schema 預留
    operational_status: Optional[str] = None        # DISRUPTED/RESUMED

    # ── Phase 3 §十一〜十六 Stable Event Identity / Persistent Memory ──
    canonical_key:       Optional[str] = None        # EventIdentityBuilder 產生的正規化身份字串
    imo_number:          Optional[str] = None        # 目前多半為 None（尚無 IMO 抽取來源），schema 預留
    content_fingerprint: Optional[str] = None        # 供 change detection 參考用的雜湊，非 Material Update 唯一依據
    version:             int = 1                     # 只有 Material Update 才會遞增（§三十一）
    last_material_update: Optional[datetime] = None  # 與 last_updated（= last_seen）分開存放（§三十二）

    # ── Phase 3 §二十〜三十五 Notification / Lifecycle 決策結果 ──
    notification_state:  Optional[str] = None        # NEW/MATERIAL_UPDATE/MINOR_UPDATE/UNCHANGED/RESOLVED_UPDATE
    should_notify:        bool = False
    notification_reason: Optional[str] = None
    change_reason:        Optional[str] = None        # 最近一次 Material Update 的人類可讀原因（§七十八）
    run_id:               Optional[str] = None        # 最近一次更新此 Event 的 run_id

    # ── Phase 2.1 §十二 Article Count ≠ Independent Source Count ─────
    article_count: int = 0                         # 這個 Event 底下總共有幾篇「文章」
    source_count: int = 0                           # 有幾個不同的 source_name（可能包含轉載）
    independent_source_count: int = 0               # 去除轉載後，真正獨立的消息來源數
    source_tiers: dict[str, int] = field(default_factory=dict)              # 依「文章」計的 tier 分布（診斷用）
    independent_source_tiers: dict[str, int] = field(default_factory=dict)  # 依「獨立來源」計的 tier 分布
                                                                              # （confidence / information_status 用這個，不是 source_tiers）

    is_new:    bool = True     # Phase 2 無持久化，恆為 True；Phase 3 接 event_history 後才有意義
    is_update: bool = False

    # ── 便利方法 ─────────────────────────────────────────────
    def source_names(self) -> list[str]:
        return [a.source_name for a in self.articles]

    def critical_override_applied(self) -> bool:
        return bool(getattr(self, "_critical_override", False))
