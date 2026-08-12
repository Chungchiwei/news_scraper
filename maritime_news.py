#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
maritime_news.py
WHL Maritime Intelligence System — 主程式（唯一 production entry point，
見 DEPRECATED.md／SYSTEM_ARCHITECTURE.md）。目前版本號見 VERSION／
version.py，不在此處硬編碼版本字串，避免與實際版本不同步。

職責：爬蟲 → Maritime Intelligence Pipeline（Article → Normalize →
Context Validation → Event Extraction → Event Classification →
Event Clustering → Risk Scoring → Management Priority → Persistent
Memory → Operational Relevance → Delivery Orchestration）→
Email / Teams / Dashboard。

新聞來源：AMZ123 / 信德海事網 HTML 爬蟲、Reddit 航運社群爬蟲
(r/Ships / r/maritime / r/shipping)、多個 RSS 來源。

Email 發送 → 委派給 email_sender.py（send_html() 為現行路徑）。
完整架構說明見 SYSTEM_ARCHITECTURE.md；資料流向見 DATA_FLOW.md。
"""

import os
import io
import re
import ssl
import json
import time
import hashlib
import html as _html_module
import logging
import logging.handlers
import traceback
import calendar
import warnings
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from bs4 import BeautifulSoup

from urllib3.exceptions import InsecureRequestWarning
warnings.filterwarnings('ignore', category=InsecureRequestWarning)

# ★ 可靠性修正：load_dotenv() 必須在 import email_sender 之前執行，
# 因為 email_sender.EmailConfig 是在模組匯入當下（class body 執行時）
# 就從 os.environ 讀取 MAIL_USER / MAIL_PASSWORD / TARGET_EMAIL。
# 若順序顛倒，本機用 .env 檔案的執行方式會讀到空值。
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from email_sender import NewsEmailSender, EmailConfigError, EmailSendError

# ── Phase 8：系統版本 Single Source of Truth（見 version.py）──────
from version import __version__, SYSTEM_NAME, version_banner

# ── Phase 2: Maritime Intelligence Core ─────────────────────────
from models import NewsArticle, MaritimeEvent, ManagementPriority, NotificationState
from risk_config import load_risk_rules
from event_extractor import EventExtractor
from carrier_news_filter import CarrierNewsFilter
from risk_scorer import RiskScorer, sort_events
from event_clusterer import EventClusterer

# ── Phase 3: Persistent Event Memory ─────────────────────────────
from memory_config import load_memory_rules
from event_store import EventStore, EventStoreError
from memory_pipeline import (
    apply_persistent_memory, generate_run_id, resolve_db_path,
    print_persistent_memory_report,
)

# ── Phase 4: Executive Maritime Intelligence Email ────────────────
# Selector 決定哪些事件進哪個 Email 區塊 → View Model 攤平成展示用資料
# （Management Summary 由 ManagementSummaryBuilder 產生）→ Renderer 只
# 負責排版，不做任何風險判斷。詳見各檔案 docstring。
from briefing_selector import BriefingSelector
from email_view_model import build_daily_brief_view_model
from executive_email_renderer import ExecutiveEmailRenderer

# ── Phase 5: LLM Maritime Intelligence Enhancement（Optional, 預設關閉）──
# LLM 是 Enhancement Layer，不是 Decision Authority：完全在 Phase 1-4
# deterministic pipeline 決定「主管要不要看到這個事件、Priority 多高」
# 之後才介入，只負責把已經選定的 Top Events 寫得更好，且任何一步失敗
# 都必須無聲 fallback 回 Phase 4 Rule-Based Summary（詳見 intelligence_
# analyzer.py docstring）。
from llm_config import load_llm_rules, load_llm_config
from llm_provider import build_provider
from ai_cache import open_ai_cache, DEFAULT_AI_CACHE_DB_PATH
from intelligence_analyzer import IntelligenceAnalyzer

# ── Phase 6: Fleet, Route & Port Operational Relevance ────────────
# EVENT RISK ≠ COMPANY EXPOSURE：這一層完全獨立於 Phase 1-5 的
# severity/priority/confidence，只回答「這件事跟本公司船隊/航線/港口
# 有多相關」。跟 Phase 5 LLM 一樣是 Optional Enhancement Layer —— 任何
# Provider（Fleet/Schedule/Route）失敗都只讓對應事件顯示 Unavailable，
# 引擎本身建置失敗則整段區塊不顯示，兩種情況都絕不能讓 Executive
# Email 發不出去（詳見 _run_operational_relevance() docstring）。
from operational_config import load_operational_rules
from fleet_provider import ConfigFleetProvider
from schedule_provider import ConfigScheduleProvider
from route_provider import ConfigRouteProvider
from operational_relevance import OperationalRelevanceEngine
from operational_history import (
    open_operational_history, DEFAULT_OPERATIONAL_HISTORY_DB_PATH,
    compute_operational_notification_state,
)
from operational_models import OperationalNotificationState

# ── Phase 7: Operational Delivery, Management Dashboard & Notification ──
# ─  Orchestration ────────────────────────────────────────────────
# Delivery ≠ Risk：這一層完全不改寫 Phase 1-5 的 priority/confidence，
# 也不改寫 Phase 6 的 relevance_level，只讀取這些既有欄位 + 兩條
# notification-state 軸（Phase 3 事件軸 / Phase 6 曝險軸），決定「這件事
# 要不要送、送去哪裡、多快送」。跟 Phase 5/6 一樣是 Optional Enhancement
# Layer——Teams/History 子系統本身故障，絕不能讓 Executive Email 因此
# 發不出去（詳見 _run_delivery_orchestration() docstring）。
from delivery_config import load_delivery_rules
from delivery_models import DeliveryChannel, DeliveryUrgency
from delivery_history import open_delivery_history, DeliveryStatus, DEFAULT_DELIVERY_HISTORY_DB_PATH
from delivery_orchestrator import DeliveryOrchestrator
from teams_config import load_teams_config
from teams_notifier import HttpTeamsNotifier
import teams_renderer
from source_health import open_source_health_store, DEFAULT_SOURCE_HEALTH_DB_PATH

# ★ 安全性修正：不再全域關閉 HTTPS 憑證驗證。
# feedparser.parse(url) 的第一次嘗試若因憑證問題失敗，
# 會自動 fallback 到以 requests（見下方 SSL_VERIFY）取得內容後再解析，
# 因此不需要、也不應該全域停用 SSL 驗證。
#
# ★ Phase 2.1 §十八〜二十一：SSL/TLS 驗證改為可設定，預設一律開啟。
# 只有在單一來源（RSS_SOURCES 內的 entry）明確加上 "verify_ssl": false 時，
# 才針對「那一個來源」關閉驗證，並會記錄 WARNING log；不可整批 search &
# replace 成永久關閉，也不可再用 ssl._create_unverified_context 全域停用。
SSL_VERIFY: bool = os.getenv("SSL_VERIFY", "true").strip().lower() not in (
    "false", "0", "no", ""
)
if not SSL_VERIFY:
    logging.getLogger(__name__).warning(
        "⚠️  SSL_VERIFY=false：已透過環境變數全域關閉 HTTPS 憑證驗證，"
        "僅建議在已知的公司代理/憑證問題排除前臨時使用。"
    )

# ★ Phase 8 §二十八〜三十一：Logging Finalization。
# Console = 給一般使用者看的精簡摘要（WARNING 以上，加上 __main__ 明確用
#   print() 輸出的版本橫幅／CLI Summary，兩者刻意分開，不透過 logger）。
# File   = logs/maritime_intelligence.log，完整 debug/operational detail
#   （INFO 以上，每天 rotate 一次，保留 14 天）。
# 不得把 300 行 scraper 細節印到 console 嚇到一般使用者，但也不能真的
# 遺失——所有 logger.info() 訊息仍然完整寫入檔案 log。
_INTELLIGENCE_DEBUG = os.getenv("INTELLIGENCE_DEBUG", "false").strip().lower() in (
    "true", "1", "yes"
)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO if _INTELLIGENCE_DEBUG else logging.WARNING)

_log_handlers = [_console_handler]

try:
    _log_dir = Path("logs")
    _log_dir.mkdir(parents=True, exist_ok=True)
    _file_handler = logging.handlers.TimedRotatingFileHandler(
        str(_log_dir / "maritime_intelligence.log"),
        when="midnight", backupCount=14, encoding="utf-8", utc=True,
    )
    _file_handler.setLevel(logging.DEBUG if _INTELLIGENCE_DEBUG else logging.INFO)
    _log_handlers.append(_file_handler)
except OSError:
    # 檔案系統無法寫入（例如唯讀環境）時，仍要能正常執行——只是沒有檔案
    # log，不能因此讓整個程式無法啟動（見 §三十三 Graceful Degradation）。
    pass

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)

# ★ Phase 2.1 §二十三：Validation Diagnostics 走 logger.debug()，預設不輸出。
# 想要在本機檢查時才看到，設 INTELLIGENCE_DEBUG=true。不影響 production 的 log 量。
if _INTELLIGENCE_DEBUG:
    logger.setLevel(logging.DEBUG)


# ══════════════════════════════════════════════════════════════
# 載入關鍵字設定檔
# ══════════════════════════════════════════════════════════════
def load_keywords_config(config_path: str = "keywords_config.json") -> dict:
    p = Path(config_path)
    if not p.exists():
        p = Path(__file__).parent / config_path
    if not p.exists():
        logger.error(f"❌ 找不到關鍵字設定檔：{config_path}")
        raise FileNotFoundError(f"keywords_config.json not found: {config_path}")
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    logger.info(f"✅ 已載入關鍵字設定檔：{p}")
    return cfg


_KW_CFG = load_keywords_config()

# ── 情境分類定義 ──────────────────────────────────────────────
INCIDENT_CATEGORIES: dict[str, dict] = {
    k: {
        "label":    v["label"],
        "icon":     v["icon"],
        "color":    v["color"],
        "bg":       v["bg"],
        "priority": v["priority"],
    }
    for k, v in _KW_CFG["categories"].items()
}

# ── 關鍵字對照表（priority 小的優先）────────────────────────
INCIDENT_KEYWORD_MAP: dict[str, str] = {}
_ALL_RAW: list[str] = []

for _cat_key, _cat_val in sorted(
    _KW_CFG["categories"].items(),
    key=lambda x: x[1]["priority"]
):
    for _kw in _cat_val["keywords"]:
        INCIDENT_KEYWORD_MAP.setdefault(_kw.lower(), _cat_key)
        _ALL_RAW.append(_kw)

# ── 去重全關鍵字清單 ──────────────────────────────────────────
_seen_kw: set = set()
ALL_KEYWORDS: list[str] = []
for _kw in _ALL_RAW:
    if _kw.lower() not in _seen_kw:
        ALL_KEYWORDS.append(_kw)
        _seen_kw.add(_kw.lower())

# ── 驗證詞集 ──────────────────────────────────────────────────
_VAL = _KW_CFG.get("validation", {})
TITLE_SHIPPING_TERMS:      set = set(_VAL.get("title_shipping_terms",      []))
BODY_SHIPPING_TERMS:       set = set(_VAL.get("body_shipping_terms",       []))
FINANCE_NOISE_TITLE_TERMS: set = set(_VAL.get("finance_noise_title_terms", []))
FINANCE_NOISE_BODY_TERMS:  set = set(_VAL.get("finance_noise_body_terms",  []))

# ── 航商名稱對照表 ────────────────────────────────────────────
CARRIER_NAMES: dict[str, list[str]] = _VAL.get("carrier_names", {})
_CARRIER_NAME_SET: set[str] = set()
for _names in CARRIER_NAMES.values():
    if isinstance(_names, list):
        for _n in _names:
            _CARRIER_NAME_SET.add(_n.lower())

logger.info(
    f"📚 關鍵字載入完成 | 分類 {len(INCIDENT_CATEGORIES)} 個 | "
    f"關鍵字 {len(ALL_KEYWORDS)} 個 | "
    f"航商 {len(CARRIER_NAMES)} 家 ({len(_CARRIER_NAME_SET)} 個名稱變體)"
)


# ══════════════════════════════════════════════════════════════
# RSS 來源設定
# ══════════════════════════════════════════════════════════════
RSS_SOURCES = [
    # ── 中文媒體（台灣）──────────────────────────────────────
    {"name": "自由時報",   "url": "https://news.ltn.com.tw/rss/world.xml",
     "backup_url": "https://news.ltn.com.tw/rss/all.xml",
     "extra_urls": [], "lang": "zh-TW", "icon": "🇹🇼", "category": "中文媒體"},
    {"name": "聯合新聞網", "url": "https://udn.com/rssfeed/news/2/6638?ch=news",
     "backup_url": "https://udn.com/rssfeed/news/2/6638",
     "extra_urls": [], "lang": "zh-TW", "icon": "📰", "category": "中文媒體"},
    {"name": "中央社",     "url": "https://www.cna.com.tw/rss/aall.aspx",
     "backup_url": "https://www.cna.com.tw/rss/aopl.aspx",
     "extra_urls": ["https://rsshub.app/cna/aall",
                    "https://rsshub.rssforever.com/cna/aall"],
     "lang": "zh-TW", "icon": "🏛️", "category": "中文媒體"},
    {"name": "Yahoo新聞",  "url": "https://tw.news.yahoo.com/rss/world",
     "backup_url": "https://tw.news.yahoo.com/rss/",
     "extra_urls": [], "lang": "zh-TW", "icon": "🟣", "category": "中文媒體"},
    {"name": "風傳媒",     "url": "https://www.storm.mg/feeds",
     "backup_url": "https://rsshub.app/storm/latest",
     "extra_urls": ["https://rsshub.rssforever.com/storm/latest"],
     "lang": "zh-TW", "icon": "🌪️", "category": "中文媒體"},

    # ── 中文媒體（大陸）──────────────────────────────────────
    {"name": "海事服務網 CNSS", "url": "https://www.cnss.com.cn/rss.xml",
     "backup_url": "https://rsshub.app/cnss/news",
     "extra_urls": ["https://rsshub.rssforever.com/cnss/news",
                    "https://rsshub2.rssforever.com/cnss/news"],
     "lang": "zh-CN", "icon": "⚓", "category": "中文媒體", "need_clean": True},
    {"name": "壹航運",     "url": "__oneshipping_html__",
     "backup_url": None, "extra_urls": [],
     "lang": "zh-CN", "icon": "🚢", "category": "中文媒體", "_html_scraper": True},
    {"name": "AMZ123",     "url": "https://www.amz123.com/author-23325",
     "backup_url": None, "extra_urls": [],
     "lang": "zh-CN", "icon": "📦", "category": "中文媒體", "_html_scraper": True},
    {"name": "信德海事網", "url": "https://www.xindemarinenews.com/plus/top.php",
     "backup_url": None, "extra_urls": [],
     "lang": "zh-CN", "icon": "⚓", "category": "中文媒體", "_html_scraper": True},
    {"name": "人民網 國際","url": "http://www.people.com.cn/rss/world.xml",
     "backup_url": "https://rsshub.app/people/world",
     "extra_urls": ["https://rsshub.rssforever.com/people/world"],
     "lang": "zh-CN", "icon": "🏮", "category": "中文媒體", "need_clean": True},
    {"name": "環球時報",   "url": "https://www.globaltimes.cn/rss/outbrain.xml",
     "backup_url": "https://rsshub.app/huanqiu/world",
     "extra_urls": ["https://rsshub.rssforever.com/huanqiu/world",
                    "https://rsshub.app/huanqiu/mil"],
     "lang": "zh-CN", "icon": "🌏", "category": "中文媒體", "need_clean": True},
    {"name": "新華社 國際","url": "http://www.xinhuanet.com/world/news_world.xml",
     "backup_url": "https://rsshub.app/xinhua/world",
     "extra_urls": ["https://rsshub.rssforever.com/xinhua/world",
                    "https://rss.fatpandadev.com/xinhua/world"],
     "lang": "zh-CN", "icon": "📻", "category": "中文媒體", "need_clean": True},
    {"name": "澎湃新聞 國際","url": "https://rsshub.app/thepaper/channel/25950",
     "backup_url": "https://rsshub.rssforever.com/thepaper/channel/25950",
     "extra_urls": ["https://rsshub.app/thepaper/channel/121811",
                    "https://rsshub2.rssforever.com/thepaper/channel/25950"],
     "lang": "zh-CN", "icon": "🗞️", "category": "中文媒體", "need_clean": True},
    {"name": "財新網 國際","url": "https://rsshub.app/caixin/international",
     "backup_url": "https://rsshub.rssforever.com/caixin/international",
     "extra_urls": ["https://rsshub.app/caixin/economy",
                    "https://rsshub2.rssforever.com/caixin/international"],
     "lang": "zh-CN", "icon": "💹", "category": "中文媒體", "need_clean": True},

    # ── 航運專業媒體 ──────────────────────────────────────────
    {"name": "TradeWinds", "url": "https://www.tradewindsnews.com/rss",
     "backup_url": "https://rsshub.app/tradewindsnews/latest",
     "extra_urls": ["https://rsshub.rssforever.com/tradewindsnews/latest"],
     "lang": "en", "icon": "🚢", "category": "航運專業"},
    {"name": "Splash247",  "url": "https://splash247.com/feed/",
     "backup_url": None, "extra_urls": [],
     "lang": "en", "icon": "⚓", "category": "航運專業"},
    {"name": "gCaptain",   "url": "https://gcaptain.com/feed/",
     "backup_url": "https://gcaptain.com/feed/rss/", "extra_urls": [],
     "lang": "en", "icon": "🧭", "category": "航運專業"},
    {"name": "Maritime Exec","url": "https://www.maritime-executive.com/rss/articles",
     "backup_url": "https://maritime-executive.com/feed",
     "extra_urls": ["https://rsshub.app/maritime-executive/article",
                    "https://rsshub.rssforever.com/maritime-executive/article"],
     "lang": "en", "icon": "⛴️", "category": "航運專業"},
    {"name": "Hellenic Ship","url": "https://www.hellenicshippingnews.com/feed/",
     "backup_url": "https://www.hellenicshippingnews.com/feed/rss/",
     "extra_urls": [], "lang": "en", "icon": "🏛️",
     "category": "航運專業", "need_clean": True},
    {"name": "Hellenic — Piracy & Security", "icon": "🏴‍☠️",
     "url": "https://www.hellenicshippingnews.com/category/shipping-news/piracy-and-security-news/feed/",
     "backup_url": None, "extra_urls": [],
     "lang": "en", "category": "航運專業", "need_clean": True},
    {"name": "Hellenic — International", "icon": "🌐",
     "url": "https://www.hellenicshippingnews.com/category/shipping-news/international-shipping-news/feed/",
     "backup_url": None, "extra_urls": [],
     "lang": "en", "category": "航運專業", "need_clean": True},
    {"name": "Hellenic — Port News", "icon": "⚓",
     "url": "https://www.hellenicshippingnews.com/category/shipping-news/port-news/feed/",
     "backup_url": None, "extra_urls": [],
     "lang": "en", "category": "航運專業", "need_clean": True},
    {"name": "Safety4Sea", "url": "https://safety4sea.com/feed/",
     "backup_url": "https://safety4sea.com/feed/rss/",
     "extra_urls": [], "lang": "en", "icon": "🛡️",
     "category": "航運專業", "need_clean": True},
    {"name": "Container News","url": "https://container-news.com/feed/",
     "backup_url": None, "extra_urls": [],
     "lang": "en", "icon": "📦", "category": "航運專業"},
    {"name": "Freightwaves","url": "https://www.freightwaves.com/news/feed",
     "backup_url": "https://www.freightwaves.com/feed", "extra_urls": [],
     "lang": "en", "icon": "📊", "category": "航運專業"},
    {"name": "Offshore Energy","url": "https://www.offshore-energy.biz/feed/",
     "backup_url": None, "extra_urls": [],
     "lang": "en", "icon": "⚡", "category": "航運專業"},
    {"name": "NewsBase",   "url": "https://newsbase.com/rss",
     "backup_url": "https://newsbase.com/feed", "extra_urls": [],
     "lang": "en", "icon": "🛢️", "category": "航運專業", "need_clean": True},
    {"name": "Marine Insight","url": "https://www.marineinsight.com/feed/",
     "backup_url": "https://www.marineinsight.com/feed/rss/",
     "extra_urls": [], "lang": "en", "icon": "⚓",
     "category": "航運專業", "need_clean": True},
    {"name": "Lloyd's List","url": "https://www.lloydslist.com/search#?topic=maritime+casualty",
     "lang": "en", "icon": "⚓", "category": "航運專業", "_html_scraper": True},
    {"name": "MarineLink", "icon": "⚓",
     "url": "https://www.marinelink.com/news/rss",
     "backup_url": "https://www.marinelink.com/news/rss?take=20",
     "extra_urls": [], "lang": "en", "category": "航運專業", "need_clean": True},

    # ── Reddit 航運社群（標記為 _reddit_scraper）────────────
    {"name": "Reddit r/Ships",    "url": "__reddit_ships__",
     "backup_url": None, "extra_urls": [],
     "lang": "en", "icon": "🤖", "category": "航運專業", "_reddit_scraper": True,
     "_reddit_sub": "Ships"},
    {"name": "Reddit r/maritime", "url": "__reddit_maritime__",
     "backup_url": None, "extra_urls": [],
     "lang": "en", "icon": "🤖", "category": "航運專業", "_reddit_scraper": True,
     "_reddit_sub": "maritime"},
    {"name": "Reddit r/shipping", "url": "__reddit_shipping__",
     "backup_url": None, "extra_urls": [],
     "lang": "en", "icon": "🤖", "category": "航運專業", "_reddit_scraper": True,
     "_reddit_sub": "shipping"},

    # ── 11 大航商官方新聞 RSS ─────────────────────────────────
    {"name": "Maersk News",      "icon": "🔵",
     "url": "https://www.maersk.com/news/rss",
     "backup_url": "https://rsshub.app/maersk/news",
     "extra_urls": ["https://rsshub.rssforever.com/maersk/news"],
     "lang": "en", "category": "航商動態", "need_clean": True},
    {"name": "CMA CGM News",     "icon": "🔴",
     "url": "https://www.cma-cgm.com/news/rss",
     "backup_url": "https://rsshub.app/cmacgm/news",
     "extra_urls": [], "lang": "en", "category": "航商動態", "need_clean": True},
    {"name": "Hapag-Lloyd News", "icon": "🟠",
     "url": "https://www.hapag-lloyd.com/en/news-insights/rss.xml",
     "backup_url": "https://rsshub.app/hapag-lloyd/news",
     "extra_urls": [], "lang": "en", "category": "航商動態", "need_clean": True},
    {"name": "長榮海運新聞",     "icon": "🟢",
     "url": "https://www.evergreen-marine.com/rss/news_zh.xml",
     "backup_url": "https://www.evergreen-marine.com/rss/news_en.xml",
     "extra_urls": ["https://rsshub.app/evergreen/news"],
     "lang": "zh-TW", "category": "航商動態", "need_clean": True},
    {"name": "陽明海運新聞",     "icon": "🟡",
     "url": "https://www.yangming.com/rss/news.xml",
     "backup_url": "https://rsshub.app/yangming/news",
     "extra_urls": [], "lang": "zh-TW", "category": "航商動態", "need_clean": True},
    {"name": "萬海航運新聞",     "icon": "🔷",
     "url": "https://www.wanhai.com/views/RSSFeed.xhtml",
     "backup_url": "https://rsshub.app/wanhai/news",
     "extra_urls": [], "lang": "zh-TW", "category": "航商動態", "need_clean": True},
    {"name": "ONE News",         "icon": "🟣",
     "url": "https://www.one-line.com/en/rss/news",
     "backup_url": "https://rsshub.app/one-line/news",
     "extra_urls": [], "lang": "en", "category": "航商動態", "need_clean": True},
    {"name": "HMM News",         "icon": "🔶",
     "url": "https://www.hmm21.com/cms/business/rss/news_en.xml",
     "backup_url": "https://rsshub.app/hmm/news",
     "extra_urls": [], "lang": "en", "category": "航商動態", "need_clean": True},
    {"name": "PIL News",         "icon": "⬛",
     "url": "https://www.pilship.com/en/rss/news.xml",
     "backup_url": "https://rsshub.app/pil/news",
     "extra_urls": [], "lang": "en", "category": "航商動態", "need_clean": True},
    {"name": "COSCO Shipping News","icon": "🔴",
     "url": "https://rsshub.app/cosco/news",
     "backup_url": "https://rsshub.rssforever.com/cosco/news",
     "extra_urls": [], "lang": "zh-CN", "category": "航商動態", "need_clean": True},
    {"name": "OOCL News",        "icon": "🟤",
     "url": "https://www.oocl.com/eng/rss/news.xml",
     "backup_url": "https://rsshub.app/oocl/news",
     "extra_urls": [], "lang": "en", "category": "航商動態", "need_clean": True},
    {"name": "MSC News (via Splash247)","icon": "⬜",
     "url": "https://splash247.com/tag/msc/feed/",
     "backup_url": "https://splash247.com/feed/",
     "extra_urls": [], "lang": "en", "category": "航商動態"},

    # ── 國際媒體 ──────────────────────────────────────────────
    {"name": "Reuters",    "url": "https://feeds.reuters.com/reuters/worldNews",
     "backup_url": "https://news.yahoo.com/rss/world",
     "extra_urls": ["https://rsshub.app/reuters/world",
                    "https://rsshub.rssforever.com/reuters/world"],
     "lang": "en", "icon": "🌐", "category": "國際媒體"},
    {"name": "BBC News",   "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
     "backup_url": "https://feeds.bbci.co.uk/news/rss.xml",
     "extra_urls": [], "lang": "en", "icon": "🇬🇧", "category": "國際媒體"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml",
     "backup_url": None, "extra_urls": [],
     "lang": "en", "icon": "🌍", "category": "國際媒體"},
    {"name": "The Guardian","url": "https://www.theguardian.com/world/rss",
     "backup_url": None, "extra_urls": [],
     "lang": "en", "icon": "🗞️", "category": "國際媒體"},
    {"name": "AP News",    "url": "https://rsshub.app/apnews/topics/world-news",
     "backup_url": "https://rsshub.rssforever.com/apnews/topics/world-news",
     "extra_urls": [], "lang": "en", "icon": "📡",
     "category": "國際媒體", "need_clean": True},
]

CNYES_SOURCES = [
    {"name": "鉅亨網 頭條",
     "api_url": "https://news.cnyes.com/api/v3/news/category/headline?limit=30",
     "icon": "💹", "category": "中文媒體", "lang": "zh-TW"},
    {"name": "鉅亨網 國際政經",
     "api_url": "https://news.cnyes.com/api/v3/news/category/wd_macro?limit=30",
     "icon": "💹", "category": "中文媒體", "lang": "zh-TW"},
    {"name": "鉅亨網 能源",
     "api_url": "https://news.cnyes.com/api/v3/news/category/energy?limit=30",
     "icon": "💹", "category": "中文媒體", "lang": "zh-TW"},
]

# ══════════════════════════════════════════════════════════════
# ★ Reddit 爬蟲設定（內建，無需 .env）★
# ══════════════════════════════════════════════════════════════
REDDIT_CONFIG = {
    # False = 免 API Key 的 requests JSON 模式（預設）
    # True  = PRAW 模式（需填入下方 client_id / client_secret）
    "use_praw":      False,

    # PRAW 模式才需要填寫
    "client_id":     "",                         # ← 填入你的 Reddit Client ID
    "client_secret": "",                         # ← 填入你的 Reddit Client Secret
    "user_agent":    "MaritimeNewsScraper/1.0",

    # 每個 Subreddit 抓取篇數
    "posts_per_sub": 15,

    # 排序方式：hot / new / top
    "category":      "hot",

    # Flair 篩選：只抓有 "News!" 標籤的貼文
    # 若要全抓請改為空字串 ""
    "flair_filter":  "News!",
}


# ══════════════════════════════════════════════════════════════
# XML 清洗工具
# ══════════════════════════════════════════════════════════════
def clean_xml_content(raw) -> str:
    import gzip as _gzip
    if isinstance(raw, bytes):
        if raw[:2] == b'\x1f\x8b':
            try:
                raw = _gzip.decompress(raw)
            except Exception:
                pass
        if raw[:3] == b'\xef\xbb\xbf':
            raw = raw[3:]
        encoding  = 'utf-8'
        enc_match = re.search(rb'encoding=["\']([^"\']+)["\']', raw[:200])
        if enc_match:
            try:
                encoding = enc_match.group(1).decode('ascii')
            except Exception:
                pass
        try:
            text = raw.decode(encoding, errors='replace')
        except (LookupError, Exception):
            text = raw.decode('utf-8', errors='replace')
    else:
        text = raw
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    text = re.sub(r'&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)',
                  '&amp;', text)
    return text


# ══════════════════════════════════════════════════════════════
# Reddit 航運社群爬蟲（★ v2：改用 RSS 方式，繞過 403）
# ══════════════════════════════════════════════════════════════
class RedditShippingScraper:
    """
    改用 Reddit RSS feed 抓取，無需 API Key，
    且不受 JSON API 的 IP 封鎖影響。
    RSS URL 格式：https://www.reddit.com/r/{sub}/{category}.rss
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer":         "https://www.reddit.com/",
    }

    SHIPPING_SUBREDDITS = ["Ships", "maritime", "shipping"]

    # RSS Bridge 備援清單（當 reddit.com 被封時依序嘗試）
    RSS_TEMPLATES = [
        "https://www.reddit.com/r/{sub}/{cat}.rss?limit={limit}",
        "https://old.reddit.com/r/{sub}/{cat}.rss?limit={limit}",
        "https://www.reddittopofalltime.com/r/{sub}.rss",
    ]

    def __init__(self, keywords: list, hours_back: int = 2,
                 config: dict | None = None):
        self.keywords   = keywords
        self.hours_back = hours_back
        self.config     = config or REDDIT_CONFIG
        self.seen_urls: set = set()

    def _parse_timestamp(self, ts) -> datetime | None:
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None

    def _parse_rss_time(self, entry) -> datetime | None:
        """解析 RSS entry 的時間"""
        try:
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                import calendar as _cal
                ts = _cal.timegm(entry.published_parsed)
                if ts > 0:
                    return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass
        raw = getattr(entry, 'published', '') or getattr(entry, 'updated', '') or ''
        if not raw:
            return None
        for fmt in (
            '%a, %d %b %Y %H:%M:%S %z',
            '%Y-%m-%dT%H:%M:%S%z',
            '%Y-%m-%dT%H:%M:%SZ',
        ):
            try:
                dt = datetime.strptime(raw.strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                continue
        return None

    def _build_item_from_rss(self, entry, source_name: str,
                              scraper_ref) -> dict | None:
        """將 RSS entry 轉為系統統一格式"""
        import html as _h
        title    = _h.unescape(getattr(entry, 'title',   '') or '').strip()
        link     = getattr(entry, 'link',    '') or ''
        summary  = getattr(entry, 'summary', '') or ''
        author   = getattr(entry, 'author',  '') or ''

        if not title or not link:
            return None

        # 清理 summary（移除 HTML 標籤）
        summary_clean = re.sub(r'<[^>]+>', '', summary).strip()
        summary_clean = re.sub(r'\s+', ' ', summary_clean)
        if len(summary_clean) > 300:
            summary_clean = summary_clean[:300] + "..."

        # 補充 meta
        if author:
            summary_clean = f"[u/{author}]  {summary_clean}" if summary_clean \
                            else f"[u/{author}]"

        if link in self.seen_urls:
            return None

        pub_time = self._parse_rss_time(entry)
        cutoff   = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back)
        if pub_time is not None and pub_time < cutoff:
            return None

        # Flair 篩選
        flair_filter = self.config.get("flair_filter", "")
        if flair_filter:
            tags = getattr(entry, 'tags', []) or []
            tag_terms = [t.get('term', '') for t in tags]
            if not any(flair_filter.lower() in t.lower() for t in tag_terms):
                return None

        matched = scraper_ref._match_keywords(title, summary_clean)
        if not matched:
            if not any(t.lower() in title.lower()
                       for t in TITLE_SHIPPING_TERMS):
                return None
            cfg_other = INCIDENT_CATEGORIES.get(
                "OTHER", {"label": "其他", "color": "#888888"}
            )
            matched = [("shipping community",
                        cfg_other["label"], cfg_other["color"])]

        self.seen_urls.add(link)
        return {
            'source_name':     source_name,
            'source_icon':     "🤖",
            'source_lang':     "en",
            'source_category': "航運專業",
            'title':           title,
            'summary':         summary_clean,
            'link':            link,
            'published':       (pub_time.strftime('%Y-%m-%d %H:%M UTC')
                                if pub_time else '時間未知'),
            'matched':         matched,
            'incident_cat':    scraper_ref._classify_incident(title, summary_clean),
        }

    def _fetch_subreddit_rss(self, subreddit: str,
                              scraper_ref) -> list[dict]:
        """用 RSS 方式抓取單一 subreddit"""
        results  = []
        category = self.config.get("category", "hot")
        limit    = self.config.get("posts_per_sub", 25)

        for template in self.RSS_TEMPLATES:
            rss_url = template.format(
                sub=subreddit, cat=category, limit=limit
            )
            logger.info(f"    🔗 {rss_url}")
            try:
                resp = requests.get(
                    rss_url, headers=self.HEADERS,
                    timeout=20, verify=SSL_VERIFY, allow_redirects=True
                )
                if resp.status_code == 403:
                    logger.warning(f"    ⚠️  403，嘗試下一個備援...")
                    continue
                if resp.status_code == 429:
                    logger.warning("    ⚠️  限流 429，等待 5 秒...")
                    time.sleep(5)
                    resp = requests.get(
                        rss_url, headers=self.HEADERS,
                        timeout=20, verify=SSL_VERIFY
                    )
                resp.raise_for_status()

                # 用 feedparser 解析 RSS
                parsed = feedparser.parse(io.BytesIO(resp.content))
                if not parsed or not parsed.entries:
                    parsed = feedparser.parse(resp.text)

                if parsed and parsed.entries:
                    logger.info(f"    📊 取得 {len(parsed.entries)} 篇貼文")
                    for entry in parsed.entries:
                        item = self._build_item_from_rss(
                            entry, f"Reddit r/{subreddit}", scraper_ref
                        )
                        if item:
                            results.append(item)
                    return results  # 成功則直接回傳，不繼續嘗試備援

                logger.warning("    ⚠️  RSS 無資料，嘗試下一個備援...")

            except requests.exceptions.HTTPError as e:
                logger.warning(f"    ⚠️  HTTP {e.response.status_code}，嘗試下一個備援...")
            except Exception as e:
                logger.warning(f"    ⚠️  {e}，嘗試下一個備援...")

        logger.warning(f"    ❌ r/{subreddit} 所有備援均失敗")
        return results

    def fetch(self, scraper_ref,
              subreddits: list[str] | None = None) -> list[dict]:
        target_subs = subreddits or self.SHIPPING_SUBREDDITS
        all_results = []
        logger.info(
            f"\n  📡 [航運專業][en] Reddit 社群爬蟲（模式：RSS Feed）"
        )
        logger.info(
            f"    📋 目標：{', '.join(f'r/{s}' for s in target_subs)}"
        )
        for sub in target_subs:
            logger.info(f"\n    🔍 爬取 r/{sub} ...")
            try:
                posts = self._fetch_subreddit_rss(sub, scraper_ref)
                logger.info(f"    ✅ r/{sub} 命中 {len(posts)} 篇")
                all_results.extend(posts)
                time.sleep(2)
            except Exception as e:
                logger.warning(f"    ❌ r/{sub} 爬取失敗: {e}")
        logger.info(
            f"\n  📋 Reddit 總計 | "
            f"Subreddit {len(target_subs)} 個 | 命中 {len(all_results)} 篇"
        )
        return all_results



# ══════════════════════════════════════════════════════════════
# 壹航運 HTML 爬蟲
# ══════════════════════════════════════════════════════════════
class OneShippingScraper:
    BASE_URL = "https://www.oneshipping.info"
    LIST_URL = "https://www.oneshipping.info/hyrd"
    HEADERS  = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer":         "https://www.oneshipping.info/hyrd",
    }
    SOURCE_META = {"name": "壹航運", "icon": "🚢",
                   "lang": "zh-CN", "category": "中文媒體"}

    def __init__(self, keywords: list, hours_back: int = 6):
        self.keywords   = keywords
        self.hours_back = hours_back
        self.seen_urls: set = set()

    def _parse_list_items(self, html: str) -> list[dict]:
        results    = []
        li_pattern = re.compile(
            r'<li[^>]+class="w-list-item"[^>]+'
            r'data-list-title="([^"]*)"[^>]+'
            r'data-list-id="(\d+)"[^>]*>.*?'
            r'<p class="w-list-date w-hide">([^<]*)</p>', re.DOTALL
        )
        for m in li_pattern.finditer(html):
            title    = _html_module.unescape(m.group(1).strip())
            news_id  = m.group(2).strip()
            date_str = m.group(3).strip()
            results.append({
                "title": title, "news_id": news_id,
                "url":   f"{self.BASE_URL}/newsinfo/{news_id}.html",
                "date_str": date_str,
            })
        return results

    def _parse_date(self, date_str: str) -> datetime | None:
        if not date_str:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                return datetime.strptime(date_str.strip(), fmt).replace(
                    tzinfo=timezone(timedelta(hours=8))
                ).astimezone(timezone.utc)
            except ValueError:
                continue
        return None

    def _fetch_article_summary(self, url: str) -> str:
        try:
            resp = requests.get(url, headers=self.HEADERS,
                                timeout=15, verify=SSL_VERIFY, allow_redirects=True)
            resp.raise_for_status()
            html = resp.text
            for pat in [
                r'<div[^>]+class="[^"]*w-detail-content[^"]*"[^>]*>(.*?)</div>',
                r'<div[^>]+class="[^"]*detail-content[^"]*"[^>]*>(.*?)</div>',
                r'<div[^>]+class="[^"]*article-content[^"]*"[^>]*>(.*?)</div>',
                r'<div[^>]+id="[^"]*content[^"]*"[^>]*>(.*?)</div>',
            ]:
                m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
                if m:
                    raw = m.group(1)
                    break
            else:
                raw = " ".join(re.findall(r'<p[^>]*>(.*?)</p>',
                                          html, re.IGNORECASE | re.DOTALL))
            summary = re.sub(r'\s+', ' ',
                             _html_module.unescape(
                                 re.sub(r'<[^>]+>', '', raw)
                             )).strip()
            return summary[:300] + ("..." if len(summary) > 300 else "")
        except Exception as e:
            logger.debug(f"      壹航運內文抓取失敗: {url} → {e}")
            return ""

    def fetch(self, scraper_ref) -> list[dict]:
        results = []
        cutoff  = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back)
        matched_count = skipped_kw = skipped_time = skipped_dup = 0
        logger.info("\n  📡 [中文媒體][zh-CN] 壹航運（列表頁直接解析）")
        try:
            resp = requests.get(self.LIST_URL, headers=self.HEADERS,
                                timeout=20, verify=SSL_VERIFY)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logger.warning(f"    ⚠️  壹航運列表頁失敗: {e}")
            return results

        candidates = self._parse_list_items(html)
        logger.info(f"    📊 第 1 頁共發現 {len(candidates)} 篇文章")

        if candidates:
            last_date = self._parse_date(candidates[-1]["date_str"])
            if last_date and last_date >= cutoff:
                try:
                    r2 = requests.get(self.LIST_URL, headers=self.HEADERS,
                                      timeout=20, verify=SSL_VERIFY,
                                      params={"page": 2})
                    if r2.status_code == 200:
                        extra = self._parse_list_items(r2.text)
                        if extra:
                            candidates.extend(extra)
                            logger.info(f"    📊 第 2 頁追加 {len(extra)} 篇")
                except Exception:
                    pass

        for cand in candidates:
            url, title, date_str = cand["url"], cand["title"], cand["date_str"]
            if url in self.seen_urls:
                skipped_dup += 1
                continue
            pub_time = self._parse_date(date_str)
            if pub_time is not None and pub_time < cutoff:
                skipped_time += 1
                continue
            title_matched = scraper_ref._match_keywords(title, "")
            summary       = self._fetch_article_summary(url)
            matched       = scraper_ref._match_keywords(title, summary) or title_matched
            if not matched:
                skipped_kw += 1
                continue
            self.seen_urls.add(url)
            results.append({
                'source_name':     self.SOURCE_META['name'],
                'source_icon':     self.SOURCE_META['icon'],
                'source_lang':     self.SOURCE_META['lang'],
                'source_category': self.SOURCE_META['category'],
                'title': title, 'summary': summary, 'link': url,
                'published': (pub_time.strftime('%Y-%m-%d %H:%M UTC')
                              if pub_time else '時間未知'),
                'matched':      matched,
                'incident_cat': scraper_ref._classify_incident(title, summary),
            })
            matched_count += 1

        logger.info(
            f"  📋 壹航運 | 候選 {len(candidates)} | 命中 {matched_count} | "
            f"無關鍵字 {skipped_kw} | 時間 {skipped_time} | 重複 {skipped_dup}"
        )
        return results


# ══════════════════════════════════════════════════════════════
# Lloyd's List 搜尋頁爬蟲
# ══════════════════════════════════════════════════════════════
class LloydsListScraper:
    BASE_URL = "https://www.lloydslist.com"
    HEADERS  = {
        "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "Accept":           "application/json, text/html, */*",
        "Accept-Language":  "en-US,en;q=0.9",
        "Referer":          "https://www.lloydslist.com/search",
        "X-Requested-With": "XMLHttpRequest",
    }
    SOURCE_META = {"name": "Lloyd's List", "icon": "⚓",
                   "lang": "en", "category": "航運專業"}
    SEARCH_TOPICS = [
        "maritime+casualty",
        "container+shipping",
        "liner+shipping",
    ]

    def __init__(self, keywords: list, hours_back: int = 6):
        self.keywords   = keywords
        self.hours_back = hours_back
        self.seen_urls: set = set()

    def _fetch_via_api(self) -> list[dict]:
        candidates = []
        for topic in self.SEARCH_TOPICS:
            for endpoint in [
                f"https://www.lloydslist.com/api/v1/search?topic={topic}"
                f"&sortBy=date&sortOrder=desc&perPage=20",
                f"https://www.lloydslist.com/api/search?topic={topic}"
                f"&sortBy=date&sortOrder=desc&perPage=20",
            ]:
                try:
                    resp = requests.get(endpoint, headers=self.HEADERS,
                                        timeout=20, verify=SSL_VERIFY)
                    if resp.status_code == 200:
                        data  = resp.json()
                        items = (data.get("results") or data.get("items") or
                                 data.get("data") or [])
                        if items:
                            logger.info(
                                f"    ✅ Lloyd's List API [{topic}]: {len(items)} 筆"
                            )
                            for item in items:
                                candidates.append({
                                    "title":    item.get("title", ""),
                                    "url":      (item.get("url") or
                                                 item.get("link") or ""),
                                    "summary":  (item.get("summary") or
                                                 item.get("description") or ""),
                                    "date_str": (item.get("publishedDate") or
                                                 item.get("date") or ""),
                                    "byline":   (item.get("byline") or
                                                 item.get("author") or ""),
                                })
                            break
                except (ValueError, KeyError):
                    continue
                except Exception as e:
                    logger.debug(
                        f"    Lloyd's List API 嘗試失敗: {endpoint[:50]} → {e}"
                    )
        return candidates

    def _fetch_via_html(self) -> list[dict]:
        candidates = []
        html = ""
        for url in [
            "https://www.lloydslist.com/search?topic=maritime+casualty"
            "&sortBy=date&sortOrder=desc&perPage=20",
            "https://www.lloydslist.com/search?topic=container+shipping"
            "&sortBy=date&sortOrder=desc&perPage=20",
            "https://www.lloydslist.com/search?q=ship+fire+collision"
            "+grounding+Maersk+MSC+Evergreen&sortBy=date&perPage=20",
        ]:
            try:
                resp = requests.get(
                    url, headers={**self.HEADERS, "Accept": "text/html"},
                    timeout=20, verify=SSL_VERIFY
                )
                if resp.status_code == 200 and len(resp.text) > 500:
                    html = resp.text
                    logger.info(
                        f"    ✅ Lloyd's List HTML 取得: {len(html)} chars"
                    )
                    break
            except Exception as e:
                logger.debug(f"    HTML 爬取失敗: {url[:50]} → {e}")

        if not html:
            return candidates

        block_pat   = re.compile(
            r'<div class="search-result__body[^"]*"[^>]*>(.*?)'
            r'(?=<div class="search-result__body|$)', re.DOTALL
        )
        date_pat    = re.compile(
            r'<time\s+datetime="(\d{4}-\d{2}-\d{2})"[^>]*>([^<]+)</time>'
        )
        summary_pat = re.compile(r'ng-bind-html="doc\.summary">([^<]+)</p>')

        for bm in block_pat.finditer(html):
            block  = bm.group(1)
            link_m = re.search(
                r'href="(https://www\.lloydslist\.com/LL\d+/[^"]+)"'
                r'[^>]*>([^<]+)</a>', block
            )
            if not link_m:
                continue
            url_found = link_m.group(1).strip()
            title     = _html_module.unescape(link_m.group(2).strip())
            date_m    = date_pat.search(block)
            sum_m     = summary_pat.search(block)
            candidates.append({
                "title":    title,
                "url":      url_found,
                "summary":  (_html_module.unescape(sum_m.group(1).strip())
                             if sum_m else ""),
                "date_str": date_m.group(1) if date_m else "",
                "byline":   "",
            })

        logger.info(f"    📊 HTML 解析到 {len(candidates)} 篇文章")
        return candidates

    def _parse_date(self, date_str: str) -> datetime | None:
        if not date_str:
            return None
        for fmt in (
            '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d %b %Y', '%B %d, %Y',
        ):
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                continue
        return None

    def fetch(self, scraper_ref) -> list[dict]:
        results = []
        cutoff  = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back)
        matched_count = skipped_kw = skipped_time = skipped_dup = 0
        logger.info("\n  📡 [航運專業][en] Lloyd's List（海事事故 + 航商動態）")

        candidates = self._fetch_via_api()
        if not candidates:
            logger.info("    ⚠️  API 無資料，改用 HTML 解析")
            candidates = self._fetch_via_html()
        if not candidates:
            logger.warning("    ⛔ Lloyd's List 所有方式均無資料")
            return results

        seen_in_batch: set = set()
        deduped = []
        for c in candidates:
            if c.get("url") and c["url"] not in seen_in_batch:
                seen_in_batch.add(c["url"])
                deduped.append(c)
        candidates = deduped
        logger.info(f"    📊 去重後共 {len(candidates)} 篇候選文章")

        for cand in candidates:
            url      = cand.get("url", "")
            title    = cand.get("title", "")
            summary  = cand.get("summary", "")
            date_str = cand.get("date_str", "")
            byline   = cand.get("byline", "")

            if not title or not url:
                continue
            if url in self.seen_urls:
                skipped_dup += 1
                continue

            pub_time = self._parse_date(date_str)
            if pub_time is not None and pub_time < cutoff:
                skipped_time += 1
                continue

            matched = scraper_ref._match_keywords(title, summary)
            if not matched:
                if not any(t.lower() in title.lower()
                           for t in TITLE_SHIPPING_TERMS):
                    skipped_kw += 1
                    continue
                matched = [("maritime news",
                            INCIDENT_CATEGORIES["OTHER"]["label"],
                            INCIDENT_CATEGORIES["OTHER"]["color"])]

            self.seen_urls.add(url)
            if byline and byline not in summary:
                summary = f"By {byline} — {summary}" if summary else f"By {byline}"

            results.append({
                'source_name':     self.SOURCE_META['name'],
                'source_icon':     self.SOURCE_META['icon'],
                'source_lang':     self.SOURCE_META['lang'],
                'source_category': self.SOURCE_META['category'],
                'title':   title,
                'summary': summary[:300] + ("..." if len(summary) > 300 else ""),
                'link':    url,
                'published': (pub_time.strftime('%Y-%m-%d %H:%M UTC')
                              if pub_time else '時間未知'),
                'matched':      matched,
                'incident_cat': scraper_ref._classify_incident(title, summary),
            })
            matched_count += 1

        logger.info(
            f"  📋 Lloyd's List | 候選 {len(candidates)} | 命中 {matched_count} | "
            f"無關鍵字 {skipped_kw} | 時間 {skipped_time} | 重複 {skipped_dup}"
        )
        return results


# ══════════════════════════════════════════════════════════════
# AMZ123 作者頁 HTML 爬蟲  ← ★ class 宣告完整，不再截斷 ★
# ══════════════════════════════════════════════════════════════
class Amz123Scraper:
    LIST_URL = "https://www.amz123.com/author-23325"
    BASE_URL = "https://www.amz123.com"
    HEADERS  = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.9",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Referer":         "https://www.amz123.com/",
    }
    SOURCE_META = {"name": "AMZ123", "icon": "📦",
                   "lang": "zh-CN", "category": "中文媒體"}

    def __init__(self, keywords: list, hours_back: int = 6):
        self.keywords   = keywords
        self.hours_back = hours_back
        self.seen_urls: set = set()

    def _parse_date(self, date_str: str) -> datetime | None:
        if not date_str:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                return datetime.strptime(date_str.strip(), fmt).replace(
                    tzinfo=timezone(timedelta(hours=8))
                ).astimezone(timezone.utc)
            except ValueError:
                continue
        return None

    def fetch(self, scraper_ref) -> list[dict]:
        results = []
        cutoff  = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back)
        matched_count = skipped_kw = skipped_time = skipped_dup = 0
        logger.info("\n  📡 [中文媒體][zh-CN] AMZ123（作者頁直接解析）")

        try:
            session = requests.Session()
            session.headers.update(self.HEADERS)
            resp = session.get(self.LIST_URL, verify=SSL_VERIFY, timeout=15)
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except Exception as e:
            logger.warning(f"    ⚠️  AMZ123 列表頁失敗: {e}")
            return results

        soup  = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("div.article-item-container")
        logger.info(f"    📊 共發現 {len(items)} 篇文章")

        for item in items:
            title_tag = item.select_one("a.article-title")
            date_tag  = item.select_one("div.article-bottom span:first-child")
            reads_tag = item.select_one("div.article-bottom span:last-child")
            desc_tag  = item.select_one("p.article-description")

            if not title_tag:
                continue

            title    = title_tag.get_text(strip=True)
            url      = title_tag.get("href", "")
            if url and not url.startswith("http"):
                url = self.BASE_URL + url
            date_str = date_tag.get_text(strip=True) if date_tag else ""
            summary  = desc_tag.get_text(strip=True)[:300] if desc_tag else ""
            reads    = reads_tag.get_text(strip=True) if reads_tag else ""

            if url in self.seen_urls:
                skipped_dup += 1
                continue

            pub_time = self._parse_date(date_str)
            if pub_time is not None and pub_time < cutoff:
                skipped_time += 1
                continue

            matched = scraper_ref._match_keywords(title, summary)
            if not matched:
                skipped_kw += 1
                continue

            self.seen_urls.add(url)
            results.append({                'source_name':     self.SOURCE_META['name'],
                'source_icon':     self.SOURCE_META['icon'],
                'source_lang':     self.SOURCE_META['lang'],
                'source_category': self.SOURCE_META['category'],
                'title':   title,
                'summary': summary + (f"  （閱讀 {reads}）" if reads else ""),
                'link':    url,
                'published': (pub_time.strftime('%Y-%m-%d %H:%M UTC')
                              if pub_time else '時間未知'),
                'matched':      matched,
                'incident_cat': scraper_ref._classify_incident(title, summary),
            })
            matched_count += 1

        logger.info(
            f"  📋 AMZ123 | 候選 {len(items)} | 命中 {matched_count} | "
            f"無關鍵字 {skipped_kw} | 時間 {skipped_time} | 重複 {skipped_dup}"
        )
        return results


# ══════════════════════════════════════════════════════════════
# 信德海事網 HTML 爬蟲
# ══════════════════════════════════════════════════════════════
class XindeScraper:
    LIST_URL = "https://www.xindemarinenews.com/plus/top.php"
    BASE_URL = "https://www.xindemarinenews.com"
    HEADERS  = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.9",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Referer":         "https://www.xindemarinenews.com/",
    }
    SOURCE_META = {"name": "信德海事網", "icon": "⚓",
                   "lang": "zh-CN", "category": "中文媒體"}

    def __init__(self, keywords: list, hours_back: int = 6):
        self.keywords   = keywords
        self.hours_back = hours_back
        self.seen_urls: set = set()

    def _parse_date(self, date_str: str) -> datetime | None:
        if not date_str:
            return None
        match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', date_str)
        if not match:
            match = re.search(r'(\d{4}-\d{2}-\d{2})', date_str)
        if not match:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                return datetime.strptime(match.group(1).strip(), fmt).replace(
                    tzinfo=timezone(timedelta(hours=8))
                ).astimezone(timezone.utc)
            except ValueError:
                continue
        return None

    def fetch(self, scraper_ref) -> list[dict]:
        results = []
        cutoff  = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back)
        matched_count = skipped_kw = skipped_time = skipped_dup = 0
        logger.info("\n  📡 [中文媒體][zh-CN] 信德海事網（列表頁直接解析）")

        try:
            session = requests.Session()
            session.headers.update(self.HEADERS)
            resp = session.get(self.LIST_URL, verify=SSL_VERIFY, timeout=15)
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except Exception as e:
            logger.warning(f"    ⚠️  信德海事網列表頁失敗: {e}")
            return results

        soup  = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("li div.box")
        logger.info(f"    📊 共發現 {len(items)} 篇文章")

        for item in items:
            title_tag   = item.select_one("p.text_title a")
            summary_tag = item.select_one("p.text_con")
            date_tag    = item.select_one("p[style*='color: grey']")

            if not title_tag:
                continue

            title    = title_tag.get_text(strip=True)
            href     = title_tag.get("href", "")
            url      = href if href.startswith("http") else self.BASE_URL + href
            date_str = date_tag.get_text(strip=True) if date_tag else ""

            summary = ""
            if summary_tag:
                for a in summary_tag.find_all("a"):
                    a.decompose()
                summary = summary_tag.get_text(strip=True)[:300]

            if url in self.seen_urls:
                skipped_dup += 1
                continue

            pub_time = self._parse_date(date_str)
            if pub_time is not None and pub_time < cutoff:
                skipped_time += 1
                continue

            matched = scraper_ref._match_keywords(title, summary)
            if not matched:
                skipped_kw += 1
                continue

            self.seen_urls.add(url)
            results.append({
                'source_name':     self.SOURCE_META['name'],
                'source_icon':     self.SOURCE_META['icon'],
                'source_lang':     self.SOURCE_META['lang'],
                'source_category': self.SOURCE_META['category'],
                'title':   title,
                'summary': summary,
                'link':    url,
                'published': (pub_time.strftime('%Y-%m-%d %H:%M UTC')
                              if pub_time else '時間未知'),
                'matched':      matched,
                'incident_cat': scraper_ref._classify_incident(title, summary),
            })
            matched_count += 1

        logger.info(
            f"  📋 信德海事網 | 候選 {len(items)} | 命中 {matched_count} | "
            f"無關鍵字 {skipped_kw} | 時間 {skipped_time} | 重複 {skipped_dup}"
        )
        return results


# ══════════════════════════════════════════════════════════════
# 新聞爬取器（核心邏輯）
# ══════════════════════════════════════════════════════════════
class NewsRssScraper:
    HEADERS_DEFAULT = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "Accept":          "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }
    HEADERS_CN = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "Accept":          "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }
    HEADERS_CNYES = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "Accept":          "application/json",
        "Accept-Language": "zh-TW,zh;q=0.9",
        "Referer":         "https://news.cnyes.com/",
    }

    SKIP_PATTERNS = [
        r'為何', r'為什麼', r'焦點股', r'熱門股', r'漲停', r'跌停',
        r'外資', r'法人', r'ETF', r'基金', r'股息', r'財報',
        r'油價.*美元', r'美元.*油價', r'石油危機', r'能源危機',
        r'大洗牌', r'資金輪動', r'恐慌指數', r'VIX', r'台股', r'股市',
    ]

    HIGH_CONFIDENCE_TERMS = {
        "houthi", "irgc", "ansarallah",
        "strait of hormuz", "persian gulf", "gulf of oman",
        "red sea attack", "red sea incident",
        "bab el-mandeb", "gulf of aden attack",
        "ukmto", "ctf-151",
        "ship fire", "vessel fire", "tanker fire",
        "ship collision", "vessel collision",
        "ship grounding", "vessel grounding",
        "ship sinking", "vessel sinking", "ship capsized",
        "man overboard", "mayday", "abandon ship",
        "search and rescue", "coast guard rescue",
        "oil spill", "marine pollution",
        "maersk", "msc", "cma cgm", "cosco", "hapag-lloyd",
        "evergreen", "yang ming", "hmm", "one line",
        "ocean network express", "pil", "wan hai", "oocl",
        "gemini cooperation", "ocean alliance", "the alliance",
        "premier alliance",
        "blank sailing", "void sailing", "port omission",
        "gri", "baf surcharge", "pss surcharge",
        "scfi", "ccfi", "wci", "fbx",
        "荷姆茲", "荷莫茲", "霍爾木茲", "霍尔木兹",
        "波斯灣", "波斯湾", "阿曼灣", "阿曼湾",
        "胡塞", "革命衛隊", "革命卫队",
        "油輪遭攻擊", "商船遇襲", "油轮遭攻击", "商船遇袭",
        "水雷封鎖", "水雷封锁",
        "船舶火災", "船舶碰撞", "船舶擱淺", "船舶沉沒",
        "船舶火灾", "船舶搁浅", "船舶沉没",
        "船員落海", "海上搜救", "棄船",
        "船员落海", "弃船",
        "馬士基", "马士基", "達飛輪船", "达飞轮船",
        "長榮海運", "长荣海运", "長榮", "长荣",
        "陽明海運", "阳明海运", "陽明", "阳明",
        "萬海航運", "万海航运", "萬海", "万海",
        "中遠海運", "中远海运", "中遠集運", "中远集运",
        "東方海外", "东方海外",
        "赫伯羅特", "赫伯罗特",
        "海洋網聯", "海洋网联",
        "現代商船", "现代商船",
        "太平船務", "太平船务",
        "空班", "略港", "附加費", "附加费",
        "運費上漲", "运费上涨", "運價指數", "运价指数",
    }

    def __init__(self, keywords: list, sources: list,
                 cnyes_sources: list, hours_back: int = 6,
                 source_health_store=None):
        self.keywords      = keywords
        self.sources       = sources
        self.cnyes_sources = cnyes_sources
        self.hours_back    = hours_back
        self.seen_urls: set = set()
        # ★ Phase 7 §五十五〜五十七：最小版 Source Health 記錄。刻意只
        # 掛在 fetch_from_source() 既有的兩個乾淨分支點（成功/所有 URL
        # 均失敗），不侵入每一個 entry 解析邏輯——source_health_store
        # 預設 None 時完全不記錄（呼叫端未提供就是選擇不啟用），且所有
        # 呼叫都包在 try/except 內，絕不能因為 Source Health 記錄本身
        # 出錯而讓正常的爬蟲流程中斷（§不要破壞目前可以正常使用的 crawler）。
        self.source_health_store = source_health_store

    # ── 語境驗證 ──────────────────────────────────────────────
    def _validate_shipping_context(self, title: str, summary: str,
                                   source_category: str = "") -> bool:
        title_clean   = _html_module.unescape(title)
        summary_clean = _html_module.unescape(summary)
        title_lower   = title_clean.lower()
        full_lower    = (title_clean + " " + summary_clean).lower()

        if source_category == "航商動態":
            if any(t.lower() in title_lower for t in FINANCE_NOISE_TITLE_TERMS):
                return False
            return True

        if any(t.lower() in title_lower for t in FINANCE_NOISE_TITLE_TERMS):
            return False

        if sum(1 for t in FINANCE_NOISE_BODY_TERMS
               if t.lower() in full_lower) >= 2:
            return False

        if any(t.lower() in title_lower for t in self.HIGH_CONFIDENCE_TERMS):
            return True

        if any(n in title_lower for n in _CARRIER_NAME_SET):
            return True

        if any(t.lower() in title_lower for t in TITLE_SHIPPING_TERMS):
            return True

        return sum(1 for t in BODY_SHIPPING_TERMS
                   if t.lower() in full_lower) >= 3

    # ── 情境分類 ──────────────────────────────────────────────
    def _classify_incident(self, title: str, summary: str) -> str:
        full_lower = (
            _html_module.unescape(title) + " " +
            _html_module.unescape(summary)
        ).lower()
        best_cat = "OTHER"
        best_pri = INCIDENT_CATEGORIES["OTHER"]["priority"]
        for kw_lower, cat in INCIDENT_KEYWORD_MAP.items():
            if kw_lower in full_lower:
                pri = INCIDENT_CATEGORIES[cat]["priority"]
                if pri < best_pri:
                    best_pri = pri
                    best_cat = cat
        return best_cat

    # ── 關鍵字比對 ────────────────────────────────────────────
    def _match_keywords(self, title: str, summary: str,
                        source_category: str = "") -> list[tuple]:
        title_clean   = _html_module.unescape(title)
        summary_clean = _html_module.unescape(summary)
        if not self._validate_shipping_context(
            title_clean, summary_clean, source_category
        ):
            return []
        full_lower = (title_clean + " " + summary_clean).lower()
        matched, seen_kw = [], set()
        for kw in self.keywords:
            kw_lower = kw.lower()
            if kw_lower in full_lower and kw not in seen_kw:
                cat = INCIDENT_KEYWORD_MAP.get(kw_lower, "OTHER")
                cfg = INCIDENT_CATEGORIES[cat]
                matched.append((kw, cfg["label"], cfg["color"]))
                seen_kw.add(kw)

        if not matched and source_category == "航商動態":
            cfg6 = INCIDENT_CATEGORIES.get("CAT6", INCIDENT_CATEGORIES["OTHER"])
            matched = [("carrier news", cfg6["label"], cfg6["color"])]

        return matched

    # ── 時間解析 ──────────────────────────────────────────────
    def _parse_published_time(self, entry) -> datetime | None:
        try:
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                t = entry.published_parsed
                if t.tm_year >= 2000:
                    ts = calendar.timegm(t)
                    if ts > 0:
                        return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass
        raw_time = (getattr(entry, 'published', '') or
                    getattr(entry, 'updated',   '') or '')
        if not raw_time:
            return None
        raw_clean = (raw_time
                     .replace(' CST', ' +0800')
                     .replace(' +0800 (CST)', ' +0800'))
        for fmt in (
            '%a, %d %b %Y %H:%M:%S %z', '%a, %d %b %Y %H:%M:%S GMT',
            '%Y-%m-%dT%H:%M:%S%z',      '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%d %H:%M:%S',        '%Y年%m月%d日 %H:%M',
            '%Y/%m/%d %H:%M:%S',
        ):
            try:
                dt = datetime.strptime(raw_clean.strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                return dt.astimezone(timezone.utc)
            except ValueError:
                continue
        return None

    # ── RSS 下載 ──────────────────────────────────────────────
    def _download_rss(self, url: str, need_clean: bool = False,
                      is_cn: bool = False, verify: bool = True):
        headers = {
            **(self.HEADERS_CN if is_cn else self.HEADERS_DEFAULT),
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control":   "no-cache",
            "Pragma":          "no-cache",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=20,
                                verify=verify, allow_redirects=True)
            resp.raise_for_status()
            if len(resp.content) < 100:
                logger.warning(f"    ⚠️  回應過短 ({len(resp.content)} bytes)")
                return None

            raw_content = resp.content

            # ★ 修正策略：依序嘗試四種解析方式
            parsed = None

            # 方式 1：直接傳 URL 給 feedparser（最相容，讓 feedparser 自行處理）
            try:
                parsed = feedparser.parse(url)
                if parsed and parsed.entries:
                    entry_count = len(parsed.entries)
                    bozo        = getattr(parsed, 'bozo', False)
                    logger.info(f"    📊 {entry_count} 則 | bozo={bozo}")
                    return parsed
            except Exception:
                pass

            # 方式 2：BytesIO（標準方式）
            if not need_clean:
                try:
                    p = feedparser.parse(io.BytesIO(raw_content))
                    if p and p.entries:
                        parsed = p
                except (TypeError, Exception):
                    pass

            # 方式 3：clean_xml_content → StringIO
            if parsed is None or not parsed.entries:
                try:
                    cleaned = clean_xml_content(raw_content)
                    p = feedparser.parse(io.StringIO(cleaned))
                    if p and p.entries:
                        parsed = p
                except Exception:
                    pass

            # 方式 4：直接傳字串
            if parsed is None or not parsed.entries:
                try:
                    cleaned = clean_xml_content(raw_content)
                    p = feedparser.parse(cleaned)
                    if p and p.entries:
                        parsed = p
                except Exception:
                    pass

            if parsed is None:
                logger.warning("    ⚠️  feedparser 解析失敗")
                return None

            entry_count = len(parsed.entries)
            bozo        = getattr(parsed, 'bozo', False)
            bozo_exc    = getattr(parsed, 'bozo_exception', None)
            logger.info(
                f"    📊 {entry_count} 則 | bozo={bozo}"
                + (f" ({type(bozo_exc).__name__})" if bozo_exc else "")
            )
            if bozo and not parsed.entries:
                logger.warning("    ⚠️  bozo 且無資料，跳過")
                return None
            return parsed

        except requests.exceptions.ConnectionError:
            logger.warning(f"    ⚠️  連線失敗: {url[:60]}")
        except requests.exceptions.Timeout:
            logger.warning(f"    ⚠️  逾時 (20s): {url[:60]}")
        except requests.exceptions.HTTPError as e:
            logger.warning(f"    ⚠️  HTTP {e.response.status_code}: {url[:60]}")
        except Exception as e:
            logger.warning(f"    ⚠️  錯誤: {url[:60]} → {e}")
        return None



    # ── 建立新聞項目 ──────────────────────────────────────────
    def _build_item(self, source: dict, title: str, summary: str,
                    link: str, pub_time: datetime | None,
                    matched: list) -> dict:
        return {
            'source_name':     source['name'],
            'source_icon':     source['icon'],
            'source_lang':     source.get('lang', 'en'),
            'source_category': source.get('category', ''),
            'title':           title.strip(),
            'summary':         summary,
            'link':            link,
            'published':       (pub_time.strftime('%Y-%m-%d %H:%M UTC')
                                if pub_time else '時間未知'),
            'matched':         matched,
            'incident_cat':    self._classify_incident(title, summary),
        }

    # ── 單一 RSS 來源抓取 ─────────────────────────────────────
    def fetch_from_source(self, source: dict) -> list:
        if source.get("_html_scraper") or source.get("_reddit_scraper"):
            return []
        results         = []
        cutoff          = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back)
        need_clean      = source.get("need_clean", False)
        is_cn           = source.get("lang", "en") == "zh-CN"
        source_category = source.get("category", "")
        # ★ Phase 2.1：per-source SSL 覆寫。只有來源設定明確寫了
        # "verify_ssl": false 才會關閉，且記錄 WARNING；預設用全域 SSL_VERIFY。
        verify_ssl = source.get("verify_ssl", SSL_VERIFY)
        if verify_ssl is False:
            logger.warning(
                f"⚠️  SSL verification disabled for source: {source.get('name', '?')}"
            )
        logger.info(
            f"\n  📡 [{source_category}]"
            f"[{source.get('lang','?')}] {source['name']}"
        )

        all_urls = [source['url']]
        if source.get('backup_url'):
            all_urls.append(source['backup_url'])
        all_urls.extend(source.get('extra_urls', []))

        feed = None
        fetch_started_at = time.monotonic()
        for attempt_url in all_urls:
            logger.info(f"    🔗 {attempt_url[:70]}")
            feed = self._download_rss(attempt_url, need_clean, is_cn, verify=verify_ssl)
            if feed and feed.entries:
                break
            logger.warning("    ❌ 無資料，嘗試下一個")

        if feed is None or not feed.entries:
            logger.warning(f"  ⛔ {source['name']} 所有 URL 均失敗")
            if self.source_health_store is not None:
                try:
                    self.source_health_store.record_failure(source['name'])
                except Exception:
                    pass   # Source Health 記錄本身絕不能讓爬蟲流程中斷
            return results

        if self.source_health_store is not None:
            try:
                latency_ms = (time.monotonic() - fetch_started_at) * 1000
                self.source_health_store.record_success(source['name'], latency_ms=latency_ms)
            except Exception:
                pass

        matched_count = skipped_time = skipped_ctx = skipped_kw = skipped_dup = 0

        for entry in feed.entries:
            try:
                title   = getattr(entry, 'title',   '') or ''
                summary = getattr(entry, 'summary', '') or ''
                link    = getattr(entry, 'link',    '') or ''

                if link and link in self.seen_urls:
                    skipped_dup += 1
                    continue

                pub_time = self._parse_published_time(entry)
                if pub_time is not None and pub_time < cutoff:
                    skipped_time += 1
                    continue

                summary_clean = _html_module.unescape(
                    re.sub(r'<[^>]+>', '', summary)
                ).strip()

                if source_category != "航商動態":
                    if any(re.search(p, title) for p in self.SKIP_PATTERNS):
                        skipped_ctx += 1
                        continue

                matched = self._match_keywords(
                    title, summary_clean, source_category
                )
                if not matched:
                    if not self._validate_shipping_context(
                        title, summary_clean, source_category
                    ):
                        skipped_ctx += 1
                    else:
                        skipped_kw += 1
                    continue

                if link:
                    self.seen_urls.add(link)
                if len(summary_clean) > 300:
                    summary_clean = summary_clean[:300] + "..."

                results.append(
                    self._build_item(source, title, summary_clean,
                                     link, pub_time, matched)
                )
                matched_count += 1

            except Exception as e:
                logger.warning(f"    ⚠️  解析失敗: {e}")

        logger.info(
            f"  📋 {source['name']} | 總 {len(feed.entries)} | "
            f"命中 {matched_count} | 無語境 {skipped_ctx} | "
            f"無關鍵字 {skipped_kw} | 時間 {skipped_time} | 重複 {skipped_dup}"
        )
        return results

    # ── 鉅亨網 JSON API ───────────────────────────────────────
    def fetch_from_cnyes(self, source: dict) -> list:
        results = []
        cutoff  = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back)
        logger.info(f"\n  📡 [鉅亨網 API][zh-TW] {source['name']}")
        logger.info(f"    🔗 {source['api_url']}")
        try:
            resp = requests.get(source['api_url'], headers=self.HEADERS_CNYES,
                                timeout=20, verify=SSL_VERIFY)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"  ⛔ 鉅亨網 API 失敗: {e}")
            return results

        items = data.get("items", {}).get("data", [])
        logger.info(f"    📊 {len(items)} 則")
        matched_count = skipped_time = skipped_ctx = skipped_dup = 0

        for item in items:
            try:
                news_id     = item.get("newsId", "")
                title       = item.get("title", "") or ""
                content_raw = (item.get("content", "") or
                               item.get("summary", "") or "")
                summary_clean = _html_module.unescape(
                    re.sub(r'<[^>]+>', '', content_raw)
                ).strip()
                if len(summary_clean) > 300:
                    summary_clean = summary_clean[:300] + "..."

                link = (f"https://news.cnyes.com/news/id/{news_id}"
                        if news_id else "")
                if link and link in self.seen_urls:
                    skipped_dup += 1
                    continue

                publish_at = item.get("publishAt", 0)
                if publish_at:
                    pub_time = datetime.fromtimestamp(publish_at, tz=timezone.utc)
                    if pub_time < cutoff:
                        skipped_time += 1
                        continue
                else:
                    pub_time = None

                matched = self._match_keywords(title, summary_clean)
                if not matched:
                    if not self._validate_shipping_context(title, summary_clean):
                        skipped_ctx += 1
                    continue

                if link:
                    self.seen_urls.add(link)
                results.append(
                    self._build_item(source, title, summary_clean,
                                     link, pub_time, matched)
                )
                matched_count += 1

            except Exception as e:
                logger.warning(f"    ⚠️  解析失敗: {e}")

        logger.info(
            f"  📋 {source['name']} | 總 {len(items)} | "
            f"命中 {matched_count} | 無語境 {skipped_ctx} | "
            f"時間 {skipped_time} | 重複 {skipped_dup}"
        )
        return results

    # ── 彙整所有來源 ──────────────────────────────────────────
    def fetch_all(self) -> dict:
        all_news: list = []

        # ── RSS 來源 ─────────────────────────────────────────
        for source in self.sources:
            all_news.extend(self.fetch_from_source(source))

        # ── 特殊 HTML 爬蟲 ───────────────────────────────────
        all_news.extend(
            OneShippingScraper(
                keywords=self.keywords, hours_back=self.hours_back
            ).fetch(self)
        )
        all_news.extend(
            LloydsListScraper(
                keywords=self.keywords, hours_back=self.hours_back
            ).fetch(self)
        )
        all_news.extend(
            Amz123Scraper(
                keywords=self.keywords, hours_back=self.hours_back
            ).fetch(self)
        )
        all_news.extend(
            XindeScraper(
                keywords=self.keywords, hours_back=self.hours_back
            ).fetch(self)
        )

        # ── Reddit 航運社群爬蟲 ──────────────────────────────
        all_news.extend(
            RedditShippingScraper(
                keywords=self.keywords,
                hours_back=self.hours_back,
                config=REDDIT_CONFIG,
            ).fetch(self)
        )

        # ── 鉅亨網 API ───────────────────────────────────────
        for cnyes_source in self.cnyes_sources:
            all_news.extend(self.fetch_from_cnyes(cnyes_source))

        # ── 時間排序（新 → 舊）──────────────────────────────
        all_news.sort(
            key=lambda x: x['published'] if x['published'] != '時間未知' else '0000',
            reverse=True
        )

        # ── 媒體分類 ──────────────────────────────────────────
        zh_tw_news    = [n for n in all_news
                         if n['source_category'] == '中文媒體'
                         and n['source_lang'] == 'zh-TW']
        zh_cn_news    = [n for n in all_news
                         if n['source_category'] == '中文媒體'
                         and n['source_lang'] == 'zh-CN']
        shipping_news = [n for n in all_news
                         if n['source_category'] == '航運專業']
        carrier_news  = [n for n in all_news
                         if n['source_category'] == '航商動態']
        intl_news     = [n for n in all_news
                         if n['source_category'] == '國際媒體']

        # ── 情境分類 ──────────────────────────────────────────
        cat_buckets: dict[str, list] = {k: [] for k in INCIDENT_CATEGORIES}
        for n in all_news:
            cat_buckets[n['incident_cat']].append(n)

        # ── 統計 log ──────────────────────────────────────────
        logger.info(f"\n{'='*60}")
        logger.info("📊 最終結果（媒體分類）:")
        logger.info(f"   🇹🇼 台灣新聞媒體:  {len(zh_tw_news)} 筆")
        logger.info(f"   🇨🇳 大陸新聞媒體:  {len(zh_cn_news)} 筆")
        logger.info(f"   🚢 航運專業媒體:   {len(shipping_news)} 筆")
        logger.info(f"   🏢 11大航商動態:   {len(carrier_news)} 筆")
        logger.info(f"   🌐 國際新聞媒體:   {len(intl_news)} 筆")
        logger.info(f"   📰 本次新聞總計:   {len(all_news)} 筆")
        logger.info("\n📊 最終結果（情境分類）:")
        for cat_key, cfg in INCIDENT_CATEGORIES.items():
            logger.info(
                f"   {cfg['icon']} {cat_key} {cfg['label']}: "
                f"{len(cat_buckets[cat_key])} 筆"
            )
        logger.info("=" * 60)

        # ── 回傳 dict ─────────────────────────────────────────
        result: dict = {
            'all':      all_news,
            'zh_tw':    zh_tw_news,
            'zh_cn':    zh_cn_news,
            'shipping': shipping_news,
            'carrier':  carrier_news,
            'intl':     intl_news,
        }
        for cat_key in INCIDENT_CATEGORIES:
            result[cat_key.lower()] = cat_buckets[cat_key]
        return result


# ══════════════════════════════════════════════════════════════
# ★ Phase 2 — Maritime Intelligence Pipeline
# ══════════════════════════════════════════════════════════════
# 資料流：
#   articles = build_articles_from_legacy(news_data)      # dict → NewsArticle
#   kept, dropped = carrier_filter.filter_articles(articles)  # §六 Carrier PR Filtering
#   for a in kept: extractor.enrich(a); scorer.score_article(a)  # §十四 Event Extraction + §七-十三 Risk Scoring
#   events = clusterer.cluster(kept)                        # §十五 Event Clustering
#   scorer.score_events(events)                              # Event 層級彙整評分
#   events = sort_events(events)                             # §二十二：priority → score → last_updated
#   compat_news_data = build_compat_news_data(news_data, ...)  # §二十一 Compatibility Adapter
#
# 舊版 fetch_from_source() / _download_rss() / 各 scraper class 完全未被修改。

def _legacy_key(item: dict) -> tuple:
    """用來在『舊版 dict』與『被 carrier filter 濾除的 article』之間比對身份。"""
    return (item.get('source_name', ''), item.get('title', ''))


def _parse_legacy_published(published_str: str | None) -> datetime | None:
    """還原 _build_item() 產生的 '%Y-%m-%d %H:%M UTC' 字串或 '時間未知'。"""
    if not published_str or published_str == '時間未知':
        return None
    try:
        return datetime.strptime(
            published_str, '%Y-%m-%d %H:%M UTC'
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def build_articles_from_legacy(news_data: dict, rules: dict) -> list[NewsArticle]:
    """
    把 scraper.fetch_all() 回傳的舊版 dict（news_data['all']）
    轉成 Phase 2 統一資料模型 NewsArticle。完全不動舊版 scraper。
    """
    tiers = rules.get("source_tiers", {})
    default_tier = tiers.get("_default", "C")
    now = datetime.now(tz=timezone.utc)
    articles: list[NewsArticle] = []

    for item in news_data.get('all', []):
        link = item.get('link') or ''
        id_seed = link or f"{item.get('source_name','')}|{item.get('title','')}"
        article_id = "art_" + hashlib.sha1(id_seed.encode('utf-8')).hexdigest()[:12]
        matched_kw = [
            m[0] for m in item.get('matched', [])
            if isinstance(m, (list, tuple)) and m
        ]
        articles.append(NewsArticle(
            article_id=article_id,
            source_name=item.get('source_name', ''),
            source_category=item.get('source_category'),
            source_lang=item.get('source_lang'),
            source_tier=tiers.get(item.get('source_name', ''), default_tier),
            title=item.get('title', ''),
            summary=item.get('summary', ''),
            url=link or None,
            published_at=_parse_legacy_published(item.get('published')),
            collected_at=now,
            matched_keywords=matched_kw,
            incident_category=item.get('incident_cat'),
            raw_legacy=item,
        ))
    return articles


def build_compat_news_data(news_data: dict, dropped_keys: set) -> dict:
    """
    §二十一 Compatibility Adapter。
    保留舊版 dict 結構（'all'/'zh_tw'/.../'cat1'.../'other'）給 email_sender.py
    不需修改就能繼續執行；唯一差異是被 CarrierNewsFilter 判定為純 PR 的項目
    會一併從這些 legacy bucket 中濾除（連舊版 Email 也不會再看到獎項/CSR公關稿）。
    """
    compat: dict = {}
    for key, val in news_data.items():
        if isinstance(val, list):
            compat[key] = [it for it in val if _legacy_key(it) not in dropped_keys]
        else:
            compat[key] = val
    return compat


def print_intelligence_report(total_articles: int, valid_articles: int,
                              events: list[MaritimeEvent],
                              dropped_pr_count: int) -> None:
    """§二十三 Console Output — 就算不寄信，開發者也能檢查 intelligence 是否合理。"""
    counts = {p: 0 for p in ManagementPriority.ORDER}
    for e in events:
        counts[e.management_priority] = counts.get(e.management_priority, 0) + 1

    lines = [
        "",
        "=" * 60,
        "🧭 MARITIME INTELLIGENCE RESULT",
        "=" * 60,
        f"Articles collected: {total_articles}",
        f"Carrier PR filtered out: {dropped_pr_count}",
        f"Valid maritime articles: {valid_articles}",
        f"Events identified: {len(events)}",
        "",
    ]
    for p in ManagementPriority.ORDER:
        lines.append(f"{p}: {counts.get(p, 0)}")
    lines.append("")
    lines.append("TOP EVENTS")
    for e in events[:5]:
        lines.append("")
        # ★ Phase 2.1：Priority 與 Confidence/information_status 分開顯示，
        # 不因為 Priority 高就暗示這件事已經被證實（見 §五）。
        status_tag = f"[{e.information_status}]" if e.information_status else ""
        lines.append(f"[{e.management_priority}][{int(e.management_score or 0)}]{status_tag}")
        lines.append(e.headline)
        lines.append(
            f"Articles: {e.article_count} | Independent sources: {e.independent_source_count}"
        )
        lines.append(f"Confidence: {e.confidence_level}")
    lines.append("=" * 60)
    logger.info("\n".join(lines))


def print_validation_diagnostics(extractor: "EventExtractor",
                                 clusterer: "EventClusterer",
                                 articles: list, events: list) -> None:
    """
    Phase 2.1 §二十三 — Validation Diagnostics。
    走 logger.debug()，預設 logging level 是 INFO 不會印出來，
    不會干擾平常 production 的 log 量；要看的話設環境變數
    INTELLIGENCE_DEBUG=true（見 __main__）把這個 logger 暫時調到 DEBUG。
    """
    conf_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for e in events:
        conf_counts[e.confidence_level] = conf_counts.get(e.confidence_level, 0) + 1
        status_counts[e.information_status] = status_counts.get(e.information_status, 0) + 1

    total_articles_ct = len(articles)
    total_independent = sum(e.independent_source_count for e in events)

    lines = [
        "",
        "-" * 60,
        "INTELLIGENCE VALIDATION",
        "-" * 60,
        f"Articles: {total_articles_ct}",
        f"Events: {len(events)}",
        "",
        f"Clustering pairs evaluated: {clusterer.diagnostics.get('pairs_evaluated', 0)}",
        f"Clustering conflicts rejected (hard reject): {clusterer.diagnostics.get('hard_rejects', 0)}",
        f"Missing-carrier matches (clustered without shared carrier): "
        f"{clusterer.diagnostics.get('missing_carrier_matches', 0)}",
        "",
        "Carrier extraction:",
        f"  Detected: {extractor.diagnostics.get('carrier_detected', 0)}",
        f"  Ambiguous rejected (e.g. bare 'one' without context): "
        f"{extractor.diagnostics.get('carrier_ambiguous_rejected', 0)}",
        "",
        "Source provenance:",
        f"  Articles across all events: {sum(e.article_count for e in events)}",
        f"  Independent sources across all events: {total_independent}",
        "",
        "Confidence:",
    ]
    for level in ("HIGH", "MEDIUM", "LOW"):
        lines.append(f"  {level}: {conf_counts.get(level, 0)}")
    lines.append("")
    lines.append("Information status:")
    for status in ("CONFIRMED", "CORROBORATED", "UNCONFIRMED", "EARLY_SIGNAL"):
        lines.append(f"  {status}: {status_counts.get(status, 0)}")
    lines.append("-" * 60)
    logger.debug("\n".join(lines))


def filter_compat_for_notification(compat_news_data: dict, keep_keys: set) -> dict:
    """
    Phase 3 §五十 Compatibility Adapter（notify-eligible 過濾）。
    在既有的 PR 過濾之上，再把「這次 execution 沒有 management-relevant
    變化」的事件對應文章從舊版 dict bucket 中拿掉——舊版 Email 渲染邏輯
    完全不用修改，只是收到的 'all' 清單只剩下真正值得通知的文章。
    如果 keep_keys 是空集合，所有 bucket 都會變成空 list，
    email_sender.send() 原本就會因為 news_data['all'] 是 falsy 而優雅跳過
    發送（不會 crash，見 §五十一）。
    """
    filtered: dict = {}
    for key, val in compat_news_data.items():
        if isinstance(val, list):
            filtered[key] = [it for it in val if _legacy_key(it) in keep_keys]
        else:
            filtered[key] = val
    return filtered


def run_intelligence_pipeline(news_data: dict, run_time: datetime,
                              db_path: str | None = None) -> dict:
    """
    整合入口：舊版 news_data → Phase 2/2.1 Intelligence Pipeline →
    Phase 3 Persistent Event Memory → compatibility news_data。

    回傳值仍是 email_sender.py 看得懂的 dict（'all'/'cat1'/... 等 bucket
    現在只保留 should_notify 的事件對應文章），額外多了：
      'events'              — 這次 run 的原始事件列表（未經記憶層比對）
      'all_current_events'  — 記憶層比對/合併/評分後的完整事件列表
      'notification_events' — 其中 should_notify=True 的子集
      'articles' / 'memory_run_id'
    供 Phase 4 Executive Email 使用。
    """
    rules        = load_risk_rules()
    memory_rules = load_memory_rules()
    extractor    = EventExtractor(rules)
    carrier_filter = CarrierNewsFilter(rules)
    scorer       = RiskScorer(rules, extractor)
    clusterer    = EventClusterer(rules)

    total_articles = len(news_data.get('all', []))
    articles = build_articles_from_legacy(news_data, rules)

    kept_articles, dropped = carrier_filter.filter_articles(articles)
    dropped_keys = {
        _legacy_key(a.raw_legacy) for a, _result in dropped if a.raw_legacy
    }

    for a in kept_articles:
        extractor.enrich(a)
        scorer.score_article(a, now=run_time)

    events = clusterer.cluster(kept_articles)
    scorer.score_events(events, now=run_time)
    events = sort_events(events)

    print_intelligence_report(
        total_articles, len(kept_articles), events, len(dropped)
    )
    print_validation_diagnostics(extractor, clusterer, kept_articles, events)

    # ★ Phase 3：DB 開啟/寫入失敗一律讓例外往上拋（EventStoreError 是
    # RuntimeError 子類別），由 __main__ 既有的 except Exception 統一
    # log ERROR + exit(1)。這裡刻意不 try/except 吞掉，因為 Persistent
    # Memory failure 屬於 production-critical failure（§四十六），
    # 絕不能 silent fallback 成「全部當新事件」。
    run_id = generate_run_id(run_time)
    with EventStore(db_path or resolve_db_path()) as store:
        memory_result = apply_persistent_memory(
            events, store, run_id, run_time, rules, memory_rules, scorer
        )
    print_persistent_memory_report(run_id, total_articles, len(events), memory_result)

    notification_events = memory_result["notification_events"]
    keep_keys = {
        _legacy_key(a.raw_legacy)
        for e in notification_events for a in e.articles if a.raw_legacy
    }

    compat_news_data = build_compat_news_data(news_data, dropped_keys)
    compat_news_data = filter_compat_for_notification(compat_news_data, keep_keys)
    compat_news_data['events']              = events
    compat_news_data['all_current_events']  = memory_result['all_current_events']
    compat_news_data['notification_events'] = notification_events
    compat_news_data['articles']            = kept_articles
    compat_news_data['memory_run_id']       = run_id
    return compat_news_data


def _run_llm_enhancement(selection: dict, run_time: datetime) -> dict:
    """
    Phase 5 入口：對 BriefingSelector 選出的事件（immediate/watch/
    industry/resolved，依此優先順序，前面的先分析，見 IntelligenceAnalyzer.
    analyze_events() 的 max_events_per_run 成本上限）跑 LLM Enhancement。

    回傳 {event_id: IntelligenceAnalysis}，只包含「驗證通過」的分析結果；
    沒有出現在這個 dict 裡的事件，email_view_model.py 會自動 fallback
    回 Phase 4 Rule-Based Summary（不需要呼叫端額外處理）。

    ★ 這個函式本身刻意用 try/except 包住整個 LLM 子系統的初始化與呼叫 ——
    LLM 是 Non-Critical Dependency（§六、§七十七）：provider 建置失敗、
    prompt 檔案遺失、cache 開啟失敗等，都只會讓本次 run 全部 fallback
    回 Rule-Based，絕不能讓 Executive Email 因此發不出去。
    """
    try:
        llm_rules = load_llm_rules()
        llm_cfg = load_llm_config(llm_rules)

        if not llm_cfg.enabled:
            logger.info("ℹ️  LLM Enhancement 未啟用（LLM_ENABLED=false），使用 Phase 4 Rule-Based Summary")
            return {}

        provider = build_provider(llm_cfg)
        cache = open_ai_cache(os.environ.get("MARITIME_AI_CACHE_DB_PATH", DEFAULT_AI_CACHE_DB_PATH))
        analyzer = IntelligenceAnalyzer(llm_cfg, llm_rules, provider, cache)

        ordered_events = (
            selection.get("immediate", []) + selection.get("watch", [])
            + selection.get("industry", []) + selection.get("resolved", [])
        )
        results = analyzer.analyze_events(ordered_events)
        cache.close()

        logger.info(analyzer.diagnostics_report())

        return {eid: analysis for eid, (analysis, _status) in results.items() if analysis is not None}

    except Exception as e:
        # 不記錄完整 exception detail 到 log（可能夾帶敏感設定），只記類別。
        logger.warning(f"⚠️  LLM Enhancement 子系統發生非預期錯誤（{type(e).__name__}），"
                        f"本次 run 全部使用 Phase 4 Rule-Based Summary")
        return {}


def _collect_operational_candidate_events(selection: dict) -> list:
    """
    Phase 6/7 共用：Operational Relevance Engine 與 Delivery Orchestrator
    必須對同一組事件跑評估——不只是 BriefingSelector 的
    immediate/watch/industry/resolved 四個桶，還包含 suppressed 桶裡
    notification_state == UNCHANGED 的事件（Phase 7 §十一〜十二
    Dual-Axis Trigger 最重要案例的前提：事件本身在 Event Axis 上是
    UNCHANGED，才需要看 Operational Axis 是否獨立升高，見
    _run_operational_relevance() / _run_delivery_orchestration() docstring）。
    MINOR_UPDATE、不具產業意義的 P3、未啟用的 P4 仍不評估，維持 Phase 6
    原本的評估範圍，避免每次 run 對大量無意義事件重算 Provider matching。
    """
    suppressed_unchanged = [
        e for e in selection.get("suppressed", [])
        if e.notification_state == NotificationState.UNCHANGED
    ]
    return (
        selection.get("immediate", []) + selection.get("watch", [])
        + selection.get("industry", []) + selection.get("resolved", [])
        + suppressed_unchanged
    )


def _run_operational_relevance(selection: dict, run_time: datetime, run_id: str) -> tuple:
    """
    Phase 6 入口：對 BriefingSelector 選出的事件跑 Operational Relevance
    Engine（Own Fleet / Port Call / Route / Regional / Regulatory 比對），
    回傳 (relevance_map, notif_state_map)：
      relevance_map     — {event_id: OperationalRelevance}，供
                           email_view_model.py 攤平成 WHL OPERATIONAL
                           EXPOSURE 展示欄位（沒出現在這個 dict 裡的事件，
                           has_operational_assessment 維持 False，
                           Renderer 不畫這段——不需要呼叫端額外處理，
                           跟 Phase 5 ai_analyses 是同一種慣例）。
      notif_state_map   — {event_id: OperationalNotificationState}，供
                           Phase 7 _run_delivery_orchestration() 讀取
                           Operational Axis（見該函式 docstring）。

    ★ Phase 7 §十一〜十二 Dual-Axis Trigger：evaluation 對象不能只看
      BriefingSelector 的 immediate/watch/industry/resolved 四個桶——
      「Event UNCHANGED 但 Exposure 升高」這個最重要的案例，事件本身
      正是被 BriefingSelector 分進 suppressed 桶（見 briefing_selector.py
      §select()）。因此這裡額外把 selection['suppressed'] 裡
      notification_state == UNCHANGED 的事件也納入評估——MINOR_UPDATE /
      不具產業意義的 P3 / 未啟用的 P4 則不評估（維持 Phase 6 原本的
      評估範圍，避免每次 run 對大量無意義事件重算 Provider matching）。

    ★ EVENT RISK ≠ COMPANY EXPOSURE（Phase 6 §三）：這裡完全不觸碰
      event.severity_score / management_priority / confidence_level /
      information_status 等 Phase 1-5 已經決定好的欄位，只是「另外」
      算一個獨立維度，寫進獨立的 OperationalRelevance 物件。
    ★ NO MATCH ≠ NO RISK、DATA UNAVAILABLE ≠ NONE（§六十四〜六十五）：
      OperationalRelevanceEngine 內部已經把單一 Fleet/Schedule/Route
      Provider 失敗處理成 per-event relevance_status=UNAVAILABLE（不是
      NONE，見 operational_relevance.py）。這裡的 try/except 只防守
      「引擎本身建置失敗」這種更根本的問題（設定檔遺失/損毀、import
      失敗等）——此時本次 run 完全不顯示 Fleet Exposure 區塊
      （has_operational_assessment=False），但絕不能讓 Executive Email
      因此發不出去，跟 Phase 5 LLM Enhancement 是同一種 Non-Critical
      Dependency 處理原則（§六、§七十七）。
    ★ Operational Relevance 必須每次 run 重新計算（§Phase 6：Time-Aware
      / Recompute Every Run），不快取成 Event 的永久屬性——這裡每次都
      重新建立 Provider/Engine，不跨 run 保留任何狀態。
    """
    try:
        rules = load_operational_rules()
        fleet_provider = ConfigFleetProvider()
        schedule_provider = ConfigScheduleProvider()
        route_provider = ConfigRouteProvider()
        engine = OperationalRelevanceEngine(rules, fleet_provider, schedule_provider, route_provider)

        ordered_events = _collect_operational_candidate_events(selection)

        history = open_operational_history(
            os.environ.get("MARITIME_OPERATIONAL_HISTORY_DB_PATH", DEFAULT_OPERATIONAL_HISTORY_DB_PATH)
        )
        relevance_map: dict = {}
        notif_state_map: dict = {}
        try:
            for event in ordered_events:
                relevance = engine.assess(event, now=run_time, run_id=run_id)
                previous = history.get_latest(event.event_id)
                notif_state = compute_operational_notification_state(previous, relevance)
                if notif_state != OperationalNotificationState.EXPOSURE_UNCHANGED:
                    logger.info(
                        f"📡 Operational Exposure [{event.event_id}]: {notif_state} "
                        f"(level={relevance.relevance_level}, status={relevance.relevance_status})"
                    )
                history.save_snapshot(relevance)
                relevance_map[event.event_id] = relevance
                notif_state_map[event.event_id] = notif_state
        finally:
            history.close()

        logger.info(engine.diagnostics_report())
        return relevance_map, notif_state_map

    except Exception as e:
        logger.warning(f"⚠️  Operational Relevance 子系統發生非預期錯誤（{type(e).__name__}），"
                        f"本次 run 不顯示 WHL Fleet Exposure 區塊")
        return {}, {}


def _record_teams_result(history, decision, run_id: str, result, delivery_type: str) -> None:
    """把單一事件的 Teams 發送結果寫進 Delivery History（§十七〜二十一）。"""
    status = DeliveryStatus.SENT if result.success else DeliveryStatus.FAILED
    if result.success:
        logger.info(f"✅ Teams 已送出 [{decision.event_id}]（{delivery_type}，第 {result.attempts} 次嘗試成功）")
    else:
        logger.warning(f"⚠️  Teams 發送失敗 [{decision.event_id}]（{delivery_type}）：{result.error}")
    history.record_delivery(
        event_id=decision.event_id, run_id=run_id, channel=DeliveryChannel.TEAMS,
        delivery_type=delivery_type, delivery_reason=decision.delivery_reason,
        dedup_key=decision.dedup_key, status=status, error_message=result.error,
    )


def _send_teams_for_decisions(decisions: dict, events_by_id: dict, relevance_map: dict,
                               history, run_id: str, delivery_rules: dict) -> dict:
    """
    Phase 7 §二十二〜三十一、八十四〜八十五：把 TEAMS channel 的
    DeliveryDecision 實際轉成訊息送出。

    ★ TEAMS_ENABLED=false（production 預設）時完全 skip，不是 Error
      （§二十四）——teams_config.load_teams_config() 已經把「未設定
      任一 webhook」也視為 disabled（見 teams_config.py）。
    ★ Per-Channel Dedup（§十八〜十九）：送出前一律先查
      history.already_sent(dedup_key, TEAMS)，已經成功送過的 dedup_key
      不重送。
    ★ Consolidation（§八十四〜八十五）：同一次 run 若達到門檻（設定於
      delivery_rules.json → teams.consolidate_min_events，預設 3）個
      非 Own Fleet 的 P1 IMMEDIATE 事件，合併成一則 Consolidated Alert，
      Own Fleet P1（own_fleet_p1_separate=true 時）一律個別發送，不參與
      合併——主管必須第一眼就看到「這是我們自己的船」。
    ★ Failure Isolation（§二十〜二十一）：單一事件/單一次 Teams 發送
      失敗只記錄 status=FAILED，不拋出例外、不影響其他事件、更不影響
      Email（呼叫端 _run_delivery_orchestration() 已經把整段包在
      try/except 內，這裡即使意外拋出例外也不會讓 Executive Email
      發不出去）。

    ★ Phase 8：回傳值 {"enabled", "attempted", "sent", "failed"} 供
      __main__ 產生 Run Complete CLI Summary 的 Teams 狀態列使用，
      純粹是額外的觀察窗口，不影響上述任何既有的失敗隔離行為。
    """
    cfg = load_teams_config(delivery_rules)
    if not cfg.enabled:
        logger.info("ℹ️  Teams 通知未啟用（TEAMS_ENABLED=false 或未設定 webhook），略過")
        return {"enabled": False, "attempted": 0, "sent": 0, "failed": 0}

    notifier = HttpTeamsNotifier(max_retries=cfg.max_retries, retry_wait_seconds=cfg.retry_wait_seconds)

    teams_decisions = [
        d for d in decisions.values()
        if DeliveryChannel.TEAMS in d.channels and d.teams_mode != "NONE"
        and not history.already_sent(d.dedup_key, DeliveryChannel.TEAMS)
    ]
    if not teams_decisions:
        return {"enabled": True, "attempted": 0, "sent": 0, "failed": 0}

    consolidate_min = delivery_rules.get("teams", {}).get("consolidate_min_events", 3)
    sent = 0
    failed = 0

    to_consolidate, individual = [], []
    for d in teams_decisions:
        relevance = relevance_map.get(d.event_id)
        own_fleet = bool(relevance and getattr(relevance, "own_fleet_involved", False))
        is_p1_immediate = (d.urgency == DeliveryUrgency.IMMEDIATE and d.management_priority == "P1")
        if (cfg.consolidate_same_run and is_p1_immediate
                and not (own_fleet and cfg.own_fleet_p1_separate)):
            to_consolidate.append(d)
        else:
            individual.append(d)

    if len(to_consolidate) >= consolidate_min:
        events_with_context = [
            (events_by_id[d.event_id], relevance_map.get(d.event_id))
            for d in to_consolidate if d.event_id in events_by_id
        ]
        message = teams_renderer.render_consolidated(
            events_with_context, max_events=cfg.max_events_per_message,
            dashboard_base_url=cfg.dashboard_base_url,
        )
        result = notifier.send(cfg.management_webhook_url, message, timeout_seconds=cfg.timeout_seconds)
        for d in to_consolidate:
            _record_teams_result(history, d, run_id, result, delivery_type="CONSOLIDATED")
        if result.success:
            sent += len(to_consolidate)
        else:
            failed += len(to_consolidate)
    else:
        individual = individual + to_consolidate   # 未達合併門檻，改逐一發送

    for d in individual:
        event = events_by_id.get(d.event_id)
        if event is None:
            continue
        relevance = relevance_map.get(d.event_id)
        message = teams_renderer.render(
            event, relevance, d, dashboard_base_url=cfg.dashboard_base_url,
            max_chars=cfg.max_message_chars, max_sources=cfg.max_sources_shown,
        )
        result = notifier.send(cfg.management_webhook_url, message, timeout_seconds=cfg.timeout_seconds)
        _record_teams_result(history, d, run_id, result, delivery_type=d.teams_mode)
        if result.success:
            sent += 1
        else:
            failed += 1

    return {"enabled": True, "attempted": sent + failed, "sent": sent, "failed": failed}


def _run_delivery_orchestration(selection: dict, operational_relevance_map: dict,
                                 operational_notif_state_map: dict,
                                 run_time: datetime, run_id: str) -> "tuple[dict, dict]":
    """
    Phase 7 入口：Delivery Orchestrator → Teams（§一〜三十一、七十八〜
    八十五 Delivery Orchestration Pipeline）。

    對 _collect_operational_candidate_events() 選出的同一組事件（跟
    Phase 6 Operational Relevance 評估對象一致），同時讀取 Event Axis
    （event.notification_state）與 Operational Axis
    （operational_notif_state_map），交給 DeliveryOrchestrator 決定
    Urgency / Channel（見 delivery_orchestrator.py，Dual-Axis Trigger
    §十一〜十二）。

    回傳 {event_id: DeliveryDecision}，供 __main__ 在 Email 實際發送
    成功/失敗後，回頭把 EMAIL channel 的送達結果記進 Delivery History
    ——Email 本身仍然是 Phase 4 既有的單一 Daily Brief/Alert 寄送路徑，
    Phase 7 不改寫寄送機制本身，只額外記錄「這次送達涵蓋了哪些事件」
    （見 delivery_models.EmailMode docstring：Email 內容 100% 仍由
    Phase 3/4 既有規則決定，Delivery Orchestrator 只讀不寫）。

    ★ Teams 是 Optional Non-Critical Dependency（跟 Phase 5 LLM、
      Phase 6 Operational Relevance 同一種處理原則）：TEAMS_ENABLED=false
      時完全 skip，不是 Error（§二十四）；Delivery History / Teams
      Notifier 本身任何非預期錯誤，都只會讓本次 run 不送 Teams 通知，
      絕不能讓 Executive Email 發不出去或讓整個 pipeline crash。
    """
    decisions: dict = {}
    teams_summary = {"enabled": False, "attempted": 0, "sent": 0, "failed": 0}
    try:
        rules = load_delivery_rules()
        history = open_delivery_history(
            os.environ.get("MARITIME_DELIVERY_HISTORY_DB_PATH", DEFAULT_DELIVERY_HISTORY_DB_PATH)
        )
        try:
            orchestrator = DeliveryOrchestrator(rules, history)
            events = _collect_operational_candidate_events(selection)
            events_by_id = {e.event_id: e for e in events}

            for event in events:
                relevance = operational_relevance_map.get(event.event_id)
                notif_state = operational_notif_state_map.get(event.event_id)
                decision = orchestrator.decide(
                    event, operational_relevance=relevance,
                    operational_notification_state=notif_state,
                    now=run_time, run_id=run_id,
                )
                decisions[event.event_id] = decision
                if decision.urgency != DeliveryUrgency.SUPPRESSED:
                    logger.info(
                        f"📬 Delivery [{event.event_id}]: {decision.urgency} "
                        f"channels={decision.channels} — {decision.delivery_reason}"
                    )

            logger.info(orchestrator.diagnostics_report())

            teams_summary = _send_teams_for_decisions(
                decisions, events_by_id, operational_relevance_map, history, run_id, rules
            )
        finally:
            history.close()

    except Exception as e:
        logger.warning(f"⚠️  Delivery Orchestration 子系統發生非預期錯誤（{type(e).__name__}），"
                        f"本次 run 不送 Teams 通知（Executive Email 不受影響）")

    return decisions, teams_summary


def _record_email_delivery(decisions: dict, run_id: str, status: str,
                            error_message: "str | None" = None) -> None:
    """
    §十七〜十八：Email 本身仍是 Phase 4 既有的單一 Daily Brief/Alert
    寄送路徑（一次寄送涵蓋多個事件），這裡只負責把「這次送達涵蓋了
    哪些事件」記進獨立的 Delivery History——供 Dashboard／未來 dedup
    查詢使用，不影響 Email 是否真的寄出（那由 email_sender.py 既有的
    SMTP retry/exit(1) 機制負責，見 §二十一）。
    """
    if not decisions:
        return
    try:
        history = open_delivery_history(
            os.environ.get("MARITIME_DELIVERY_HISTORY_DB_PATH", DEFAULT_DELIVERY_HISTORY_DB_PATH)
        )
        try:
            for decision in decisions.values():
                if DeliveryChannel.EMAIL not in decision.channels:
                    continue
                history.record_delivery(
                    event_id=decision.event_id, run_id=run_id, channel=DeliveryChannel.EMAIL,
                    delivery_type=decision.email_mode, delivery_reason=decision.delivery_reason,
                    dedup_key=decision.dedup_key, status=status, error_message=error_message,
                )
        finally:
            history.close()
    except Exception as e:
        logger.warning(f"⚠️  Email Delivery History 記錄失敗（{type(e).__name__}），不影響已寄出的 Email")


# ══════════════════════════════════════════════════════════════
# Phase 8 §五十七〜五十九：Run Complete CLI Summary
#
# ★ 刻意用 print() 而不是 logger.info()：Phase 8 §二十八〜三十一把
#   console log handler 收斂成只顯示 WARNING 以上（見檔案開頭 logging
#   設定），detail 全部進 logs/maritime_intelligence.log。但這個摘要
#   是給一般使用者/排程「看執行結果」用的，不管有沒有 WARNING 都必須
#   顯示，所以不透過 logger，直接印到 stdout。
# ══════════════════════════════════════════════════════════════
_CLI_BAR = "═" * 38


def _print_cli_summary(*, articles_collected: int, events_identified: int, stats: dict,
                        p1_count: int, p2_count: int, direct_exposure: int, high_exposure: int,
                        email_status: str, teams_status: str, dashboard_status: str,
                        run_id: str, result: str) -> None:
    def _row(label: str, value) -> str:
        return f"{label:<21}{str(value):>17}"

    print(_CLI_BAR)
    print("WHL MARITIME INTELLIGENCE")
    print("RUN COMPLETE")
    print(_CLI_BAR)
    print(_row("Articles collected:", articles_collected))
    print(_row("Events identified:", events_identified))
    print(_row("New:", stats.get("new", 0)))
    print(_row("Material Updates:", stats.get("material", 0)))
    print(_row("Resolved:", stats.get("resolved", 0)))
    print(_row("P1:", p1_count))
    print(_row("P2:", p2_count))
    print(_row("WHL Direct Exposure:", direct_exposure))
    print(_row("WHL High Exposure:", high_exposure))
    print(_row("Email:", email_status))
    print(_row("Teams:", teams_status))
    print(_row("Dashboard Data:", dashboard_status))
    print("Run ID:")
    print(run_id)
    print("Result:")
    print(result)
    print(_CLI_BAR)


def _print_run_failed(critical_component: str, error: str,
                       recommended: str = "Run scripts/health_check.py") -> None:
    """
    ★ Fatal 失敗時的精簡 console 輸出。完整 traceback 仍然完整寫進
      logs/maritime_intelligence.log（見 __main__ 例外處理的
      logger.info(traceback.format_exc())），console 不顯示大量堆疊，
      只給「發生什麼事、接下來該做什麼」。
    """
    print("RUN FAILED")
    print("Critical Component:")
    print(critical_component)
    print("Error:")
    print(error)
    print("Recommended:")
    print(recommended)


# ══════════════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # ★ Phase 8：版本橫幅用 print()，不透過 logger（console handler
    # 只顯示 WARNING 以上），確保使用者一律看得到自己執行的是哪個版本。
    print("=" * 60)
    print(f"🚢 {version_banner()}")
    print("   PUBLIC MARITIME SOURCES → EVENT INTELLIGENCE → RISK PRIORITY")
    print("   → PERSISTENT MEMORY → MATERIAL CHANGE → WHL OPERATIONAL EXPOSURE")
    print("   → DELIVERY ORCHESTRATION → EMAIL / TEAMS / DASHBOARD")
    print("=" * 60)
    logger.info(f"執行開始 — {version_banner()}")

    run_time   = datetime.now(tz=timezone.utc)
    hours_back = int(os.environ.get("NEWS_HOURS_BACK", "6"))

    # ★ Phase 8 §三十二〜三十三：Critical Config 驗證，越早驗證越好。
    # Event Database 是唯一「打不開就必須 Fatal」的資料庫（見
    # scripts/health_check.py 的 Graceful Degradation 分類一致）—— 這裡
    # 故意在爬任何新聞之前就先確認一次，避免爬完一輪、跑完 LLM 才發現
    # 連事件記憶體都寫不進去。
    try:
        with EventStore(resolve_db_path()):
            pass
    except Exception as e:
        logger.error(f"❌ Event Database 無法開啟：{e}")
        _print_run_failed("Event Database", str(e))
        exit(1)

    # ★ 可靠性修正：Email 設定（EmailConfigError）在這裡就會直接
    # 中止執行並明確報錯，不會等到爬完新聞才發現寄不出去。
    try:
        sender = NewsEmailSender(
            incident_categories = INCIDENT_CATEGORIES,
            rss_sources         = RSS_SOURCES,
            cnyes_sources       = CNYES_SOURCES,
        )
    except EmailConfigError as e:
        logger.error(str(e))
        _print_run_failed("Email Configuration", str(e),
                           recommended="Check .env — MAIL_USER / MAIL_PASSWORD / TARGET_EMAIL")
        exit(1)

    # ★ Phase 7 §五十五〜五十七：最小版 Source Health（獨立 SQLite，
    # 開啟失敗一律安全退化成 NullSourceHealthStore，絕不讓爬蟲流程中斷）。
    source_health_store = open_source_health_store(
        os.environ.get("MARITIME_SOURCE_HEALTH_DB_PATH", DEFAULT_SOURCE_HEALTH_DB_PATH)
    )

    scraper = NewsRssScraper(
        keywords      = ALL_KEYWORDS,
        sources       = RSS_SOURCES,
        cnyes_sources = CNYES_SOURCES,
        hours_back    = hours_back,
        source_health_store = source_health_store,
    )

    # ★ Phase 7：預先初始化，讓 except 區塊在例外發生於 delivery_decisions
    # 賦值之前時，仍能安全地記錄一筆「本次沒有任何事件涵蓋在內」而不是
    # 讓 NameError 蓋掉真正的錯誤訊息。
    delivery_decisions: dict = {}
    delivery_run_id: str = generate_run_id(run_time)
    # ★ Phase 8：CLI Summary 用的統計數字，預設值確保任何一個 optional
    # 子系統失敗時，摘要仍然印得出來（不會因為 NameError 蓋掉真正的錯誤）。
    articles_collected = 0
    events_identified   = 0
    memory_stats        = {"new": 0, "material": 0, "resolved": 0}
    p1_count = p2_count = direct_exposure = high_exposure = 0
    email_status = "SKIPPED"
    teams_status = "DISABLED"

    try:
        news_data = scraper.fetch_all()
        articles_collected = len(news_data.get('all', []))
        try:
            source_health_store.close()   # 之後不會再用到，盡早釋放連線
        except Exception:
            pass

        # ★ Phase 2: Article-based scraper → Event-based Intelligence Engine
        news_data = run_intelligence_pipeline(news_data, run_time)

        all_current_events = news_data.get('all_current_events', [])
        events_identified = len(all_current_events)
        memory_stats = {
            "new":      sum(1 for e in all_current_events if e.notification_state == NotificationState.NEW),
            "material": sum(1 for e in all_current_events if e.notification_state == NotificationState.MATERIAL_UPDATE),
            "resolved": sum(1 for e in all_current_events if e.notification_state == NotificationState.RESOLVED_UPDATE),
        }
        p1_count = sum(1 for e in all_current_events if e.management_priority == ManagementPriority.P1)
        p2_count = sum(1 for e in all_current_events if e.management_priority == ManagementPriority.P2)

        # ★ Phase 4：Event-based Executive Email（取代 Phase 1-3 舊版
        # 「新聞列表」Email；舊版 EmailRenderer/send() 保留於
        # email_sender.py 供備援，本階段起不再是預設路徑）。
        #
        # BriefingSelector 吃的是這次 run 比對/評分後的「完整」事件列表
        # （all_current_events），不是只有 should_notify 的子集 —— Daily
        # Brief 需要顯示 P3 Industry Watch，這些事件不一定 should_notify，
        # 但仍要出現在日報裡；UNCHANGED/MINOR_UPDATE 則由 Selector 自己
        # 過濾掉（見 briefing_selector.py）。
        selection = BriefingSelector().select(all_current_events)

        # ★ Phase 5：LLM Enhancement（Optional，預設 LLM_ENABLED=false）。
        # 任何一步失敗 —— provider 建置失敗、API timeout、JSON 格式錯誤、
        # 驗證未通過 —— 都只影響「這個事件用 AI 版還是 Rule-Based 版文字」，
        # 絕對不能讓 Email 發送失敗（§六）。
        ai_analyses = _run_llm_enhancement(selection, run_time)

        delivery_run_id = news_data.get('memory_run_id') or generate_run_id(run_time)

        # ★ Phase 6：Operational Relevance（Optional，Provider 失敗一律
        # 顯示 Unavailable，引擎建置失敗則整段區塊不顯示——兩者都不能讓
        # Executive Email 發不出去，見 _run_operational_relevance() docstring）。
        operational_relevance_map, operational_notif_state_map = _run_operational_relevance(
            selection, run_time, delivery_run_id
        )
        direct_exposure = sum(
            1 for r in operational_relevance_map.values()
            if getattr(r, "relevance_level", None) == "DIRECT"
        )
        high_exposure = sum(
            1 for r in operational_relevance_map.values()
            if getattr(r, "relevance_level", None) == "HIGH"
        )

        # ★ Phase 7：Delivery Orchestrator → Teams（Optional，Teams
        # 停用/失敗都不能讓 Executive Email 發不出去，見
        # _run_delivery_orchestration() docstring）。回傳的 decisions
        # 供本次 run 稍後 Email 實際寄送成功/失敗時，回頭記錄 EMAIL
        # channel 的 Delivery History；teams_summary 只供 CLI Summary
        # 顯示狀態使用，不影響既有的失敗隔離行為。
        delivery_decisions, teams_summary = _run_delivery_orchestration(
            selection, operational_relevance_map, operational_notif_state_map,
            run_time, delivery_run_id,
        )
        if not teams_summary.get("enabled"):
            teams_status = "DISABLED"
        elif teams_summary.get("attempted", 0) == 0:
            teams_status = "SUCCESS"   # 啟用但這次沒有需要送的事件，不是失敗
        elif teams_summary.get("failed", 0) == 0:
            teams_status = "SUCCESS"
        elif teams_summary.get("sent", 0) == 0:
            teams_status = "FAILED"
        else:
            teams_status = "PARTIAL"

        brief_vm = build_daily_brief_view_model(
            selection, generated_at=run_time, ai_analyses=ai_analyses,
            operational_relevance_map=operational_relevance_map,
        )

        # SEND_NO_RISK_BRIEF 環境變數可覆寫 email_rules.json 的 no_risk.send
        # （§四十三：不要硬編碼在 Python 裡）。
        env_no_risk = os.environ.get("SEND_NO_RISK_BRIEF")
        if env_no_risk is not None:
            send_no_risk = env_no_risk.strip().lower() not in ("false", "0", "no", "")
        else:
            from email_config import load_email_rules
            send_no_risk = load_email_rules().get("no_risk", {}).get("send", True)

        if brief_vm.is_no_risk and not send_no_risk:
            email_status = "SKIPPED"
            logger.info(
                "✅ 執行完畢：本次未發現重大風險事件，且 SEND_NO_RISK_BRIEF=false，未發送 Email"
            )
        else:
            html_body = ExecutiveEmailRenderer().render_daily_brief(brief_vm)
            sender.send_html(brief_vm.subject, html_body)
            email_status = "SUCCESS"
            logger.info(f"✅ 執行完畢：Executive Email 已發送 — {brief_vm.subject}")
            # ★ Phase 7 §十七〜十八：Email 實際寄送成功後，把這次涵蓋的
            # 事件記進 Delivery History（EMAIL channel）。
            _record_email_delivery(delivery_decisions, delivery_run_id, DeliveryStatus.SENT)

        _print_cli_summary(
            articles_collected=articles_collected, events_identified=events_identified,
            stats=memory_stats, p1_count=p1_count, p2_count=p2_count,
            direct_exposure=direct_exposure, high_exposure=high_exposure,
            email_status=email_status, teams_status=teams_status,
            dashboard_status="UPDATED", run_id=delivery_run_id, result="SUCCESS",
        )
    except EmailSendError as e:
        # ★ 可靠性修正：SMTP 重試後仍失敗，必須讓 exit code 反映失敗，
        # 排程/GitHub Actions 才能正確判斷此次執行未成功送達。
        logger.error(str(e))
        # ★ Phase 7：Email 最終失敗，也要忠實記錄 status=FAILED（供下次
        # run／Dashboard 判斷這批事件的 Email 其實沒有送達），但絕不能
        # 讓這筆記錄動作本身影響既有的 exit(1) production policy。
        _record_email_delivery(delivery_decisions, delivery_run_id, DeliveryStatus.FAILED,
                                error_message=str(e))
        _print_run_failed("Email Delivery (SMTP)", str(e),
                           recommended="Check MAIL_SMTP_SERVER/PORT and MAIL_USER/MAIL_PASSWORD, "
                                       "then run scripts/health_check.py")
        exit(1)
    except Exception as e:
        # ★ Phase 8 §五十九：console 只顯示精簡的失敗摘要（這一行
        # logger.error 沒有帶 exc_info，所以不會印出堆疊）；完整
        # traceback 改用 logger.info() 寫（INFO 只進檔案 log，不會出現
        # 在 console，因為 console handler 預設是 WARNING 以上）。
        logger.error(f"❌ 執行失敗: {e}")
        logger.info("完整 traceback（僅寫入檔案 log）：\n" + traceback.format_exc())
        _print_run_failed(type(e).__name__, str(e))
        exit(1)

