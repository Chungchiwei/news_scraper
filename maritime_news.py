#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
maritime_news.py  v6.2
海事航運新聞監控系統 — 主程式
職責：爬蟲 / 關鍵字比對 / 分類
新增：11 大航商 RSS 來源 / CAT6 語境驗證 / 航商名稱對照表
Email 發送 → 委派給 email_sender.py
"""

import os
import io
import re
import ssl
import json
import html as _html_module
import logging
import traceback
import calendar
import warnings
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

from urllib3.exceptions import InsecureRequestWarning
warnings.filterwarnings('ignore', category=InsecureRequestWarning)

from email_sender import NewsEmailSender

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ssl._create_default_https_context = ssl._create_unverified_context

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


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

# ── ★ 新增：航商名稱對照表（從 JSON 載入）────────────────────
# 結構：{ "MSC": ["MSC", "Mediterranean Shipping", "地中海航運"], ... }
CARRIER_NAMES: dict[str, list[str]] = (
    _VAL.get("carrier_names", {})
)
# 建立扁平化航商名稱集合（供語境驗證快速查找）
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
     # ★ v6.3 新增：Hellenic 分類子 feed（高命中率）
    {
        "name": "Hellenic — Piracy & Security", "icon": "🏴‍☠️",
        "url":        "https://www.hellenicshippingnews.com/category/shipping-news/piracy-and-security-news/feed/",
        "backup_url": None, "extra_urls": [],
        "lang": "en", "category": "航運專業", "need_clean": True,
    },
    {
        "name": "Hellenic — International", "icon": "🌐",
        "url":        "https://www.hellenicshippingnews.com/category/shipping-news/international-shipping-news/feed/",
        "backup_url": None, "extra_urls": [],
        "lang": "en", "category": "航運專業", "need_clean": True,
    },
    {
        "name": "Hellenic — Port News", "icon": "⚓",
        "url":        "https://www.hellenicshippingnews.com/category/shipping-news/port-news/feed/",
        "backup_url": None, "extra_urls": [],
        "lang": "en", "category": "航運專業", "need_clean": True,
    },    
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
    {
    "name": "MarineLink", "icon": "⚓",
    "url":        "https://www.marinelink.com/news/rss",
    "backup_url": "https://www.marinelink.com/news/rss?take=20",
    "extra_urls": [],
    "lang": "en", "category": "航運專業", "need_clean": True},

    # ── ★ 新增：11 大航商官方新聞 RSS ────────────────────────
    # Maersk
    {"name": "Maersk News", "icon": "🔵",
     "url":        "https://www.maersk.com/news/rss",
     "backup_url": "https://rsshub.app/maersk/news",
     "extra_urls": ["https://rsshub.rssforever.com/maersk/news"],
     "lang": "en", "category": "航商動態", "need_clean": True},
    # CMA CGM
    {"name": "CMA CGM News", "icon": "🔴",
     "url":        "https://www.cma-cgm.com/news/rss",
     "backup_url": "https://rsshub.app/cmacgm/news",
     "extra_urls": [],
     "lang": "en", "category": "航商動態", "need_clean": True},
    # Hapag-Lloyd
    {"name": "Hapag-Lloyd News", "icon": "🟠",
     "url":        "https://www.hapag-lloyd.com/en/news-insights/rss.xml",
     "backup_url": "https://rsshub.app/hapag-lloyd/news",
     "extra_urls": [],
     "lang": "en", "category": "航商動態", "need_clean": True},
    # Evergreen
    {"name": "長榮海運新聞", "icon": "🟢",
     "url":        "https://www.evergreen-marine.com/rss/news_zh.xml",
     "backup_url": "https://www.evergreen-marine.com/rss/news_en.xml",
     "extra_urls": ["https://rsshub.app/evergreen/news"],
     "lang": "zh-TW", "category": "航商動態", "need_clean": True},
    # Yang Ming
    {"name": "陽明海運新聞", "icon": "🟡",
     "url":        "https://www.yangming.com/rss/news.xml",
     "backup_url": "https://rsshub.app/yangming/news",
     "extra_urls": [],
     "lang": "zh-TW", "category": "航商動態", "need_clean": True},
    # Wan Hai
    {"name": "萬海航運新聞", "icon": "🔷",
     "url":        "https://www.wanhai.com/views/RSSFeed.xhtml",
     "backup_url": "https://rsshub.app/wanhai/news",
     "extra_urls": [],
     "lang": "zh-TW", "category": "航商動態", "need_clean": True},
    # ONE
    {"name": "ONE News", "icon": "🟣",
     "url":        "https://www.one-line.com/en/rss/news",
     "backup_url": "https://rsshub.app/one-line/news",
     "extra_urls": [],
     "lang": "en", "category": "航商動態", "need_clean": True},
    # HMM
    {"name": "HMM News", "icon": "🔶",
     "url":        "https://www.hmm21.com/cms/business/rss/news_en.xml",
     "backup_url": "https://rsshub.app/hmm/news",
     "extra_urls": [],
     "lang": "en", "category": "航商動態", "need_clean": True},
    # PIL
    {"name": "PIL News", "icon": "⬛",
     "url":        "https://www.pilship.com/en/rss/news.xml",
     "backup_url": "https://rsshub.app/pil/news",
     "extra_urls": [],
     "lang": "en", "category": "航商動態", "need_clean": True},
    # COSCO / OOCL（官方 RSS 較少，改用 RSSHub）
    {"name": "COSCO Shipping News", "icon": "🔴",
     "url":        "https://rsshub.app/cosco/news",
     "backup_url": "https://rsshub.rssforever.com/cosco/news",
     "extra_urls": [],
     "lang": "zh-CN", "category": "航商動態", "need_clean": True},
    {"name": "OOCL News", "icon": "🟤",
     "url":        "https://www.oocl.com/eng/rss/news.xml",
     "backup_url": "https://rsshub.app/oocl/news",
     "extra_urls": [],
     "lang": "en", "category": "航商動態", "need_clean": True},
    # MSC（無官方 RSS，透過 RSSHub 或 Splash247 過濾）
    {"name": "MSC News (via Splash247)", "icon": "⬜",
     "url":        "https://splash247.com/tag/msc/feed/",
     "backup_url": "https://splash247.com/feed/",
     "extra_urls": [],
     "lang": "en", "category": "航商動態"},

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
# 壹航運 HTML 爬蟲（不變）
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

    def __init__(self, keywords: list, hours_back: int = 2):
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
                                timeout=15, verify=False, allow_redirects=True)
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
                                timeout=20, verify=False)
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
                                      timeout=20, verify=False, params={"page": 2})
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
# Lloyd's List 搜尋頁爬蟲（更新搜尋主題）
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

    # ★ 擴大搜尋主題，涵蓋航商動態
    SEARCH_TOPICS = [
        "maritime+casualty",
        "container+shipping",
        "liner+shipping",
    ]

    def __init__(self, keywords: list, hours_back: int = 2):
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
                                        timeout=20, verify=False)
                    if resp.status_code == 200:
                        data  = resp.json()
                        items = (data.get("results") or data.get("items") or
                                 data.get("data") or [])
                        if items:
                            logger.info(
                                f"    ✅ Lloyd's List API [{topic}]: "
                                f"{len(items)} 筆"
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
                            break   # 此 topic 已取得，換下一個 topic
                except (ValueError, KeyError):
                    continue
                except Exception as e:
                    logger.debug(
                        f"    Lloyd's List API 嘗試失敗: "
                        f"{endpoint[:50]} → {e}"
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
                    timeout=20, verify=False
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

        # 去重（同一 URL 可能被多個 topic 重複取得）
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
# 新聞爬取器（核心邏輯更新）
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

    # ── 標題快速排除 pattern ──────────────────────────────────
    SKIP_PATTERNS = [
        r'為何', r'為什麼', r'焦點股', r'熱門股', r'漲停', r'跌停',
        r'外資', r'法人', r'ETF', r'基金', r'股息', r'財報',
        r'油價.*美元', r'美元.*油價', r'石油危機', r'能源危機',
        r'大洗牌', r'資金輪動', r'恐慌指數', r'VIX', r'台股', r'股市',
    ]

    # ── ★ 高信心詞（新增航商名稱）────────────────────────────
    HIGH_CONFIDENCE_TERMS = {
        # 地緣政治 / 海事事故
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
        # 航商名稱（英文）— 標題含航商名稱即視為航運語境
        "maersk", "msc", "cma cgm", "cosco", "hapag-lloyd",
        "evergreen", "yang ming", "hmm", "one line",
        "ocean network express", "pil", "wan hai", "oocl",
        "gemini cooperation", "ocean alliance", "the alliance",
        "premier alliance",
        # 航商動態術語
        "blank sailing", "void sailing", "port omission",
        "gri", "baf surcharge", "pss surcharge",
        "scfi", "ccfi", "wci", "fbx",
        # 中文高信心
        "荷姆茲", "荷莫茲", "霍爾木茲", "霍尔木兹",
        "波斯灣", "波斯湾", "阿曼灣", "阿曼湾",
        "胡塞", "革命衛隊", "革命卫队",
        "油輪遭攻擊", "商船遇襲", "油轮遭攻击", "商船遇袭",
        "水雷封鎖", "水雷封锁",
        "船舶火災", "船舶碰撞", "船舶擱淺", "船舶沉沒",
        "船舶火灾", "船舶搁浅", "船舶沉没",
        "船員落海", "海上搜救", "棄船",
        "船员落海", "弃船",
        # 航商中文名稱
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
                 cnyes_sources: list, hours_back: int = 2):
        self.keywords      = keywords
        self.sources       = sources
        self.cnyes_sources = cnyes_sources
        self.hours_back    = hours_back
        self.seen_urls: set = set()

    # ── ★ 語境驗證（新增航商來源直通邏輯）───────────────────
    def _validate_shipping_context(self, title: str, summary: str,
                                   source_category: str = "") -> bool:
        title_clean   = _html_module.unescape(title)
        summary_clean = _html_module.unescape(summary)
        title_lower   = title_clean.lower()
        full_lower    = (title_clean + " " + summary_clean).lower()

        # ★ 航商官方來源直接通過（無需關鍵字驗證）
        if source_category == "航商動態":
            # 仍需排除明顯財經雜訊
            if any(t.lower() in title_lower for t in FINANCE_NOISE_TITLE_TERMS):
                return False
            return True

        # 第一關：標題財經雜訊 → 排除
        if any(t.lower() in title_lower for t in FINANCE_NOISE_TITLE_TERMS):
            return False

        # 第二關：正文財經雜訊 ≥ 2 → 排除
        if sum(1 for t in FINANCE_NOISE_BODY_TERMS
               if t.lower() in full_lower) >= 2:
            return False

        # 第三關：高信心詞 → 直接通過
        if any(t.lower() in title_lower for t in self.HIGH_CONFIDENCE_TERMS):
            return True

        # ★ 第三關補充：標題含任一航商名稱變體 → 直接通過
        if any(n in title_lower for n in _CARRIER_NAME_SET):
            return True

        # 第四關：標題含航運複合詞 → 通過
        if any(t.lower() in title_lower for t in TITLE_SHIPPING_TERMS):
            return True

        # 第五關：正文航運詞 ≥ 3 → 通過
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

    # ── 關鍵字比對（傳入 source_category）───────────────────
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

        # ★ 航商來源：若無關鍵字命中，補一個預設 CAT6 標籤
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
                      is_cn: bool = False):
        headers = {
            **(self.HEADERS_CN if is_cn else self.HEADERS_DEFAULT),
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control":   "no-cache",
            "Pragma":          "no-cache",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=20,
                                verify=False, allow_redirects=True)
            resp.raise_for_status()
            if len(resp.content) < 100:
                logger.warning(f"    ⚠️  回應過短 ({len(resp.content)} bytes)")
                return None

            if need_clean:
                parsed = feedparser.parse(
                    io.StringIO(clean_xml_content(resp.content))
                )
            else:
                try:
                    parsed = feedparser.parse(io.BytesIO(resp.content))
                except Exception:
                    parsed = feedparser.parse(
                        io.StringIO(clean_xml_content(resp.content))
                    )

            if getattr(parsed, 'bozo', False) and not parsed.entries:
                try:
                    parsed2 = feedparser.parse(
                        io.StringIO(clean_xml_content(resp.content))
                    )
                    if parsed2.entries:
                        parsed = parsed2
                except Exception:
                    pass

            entry_count = len(parsed.entries) if parsed else 0
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

    # ── 單一 RSS 來源抓取（傳入 source_category）────────────
    def fetch_from_source(self, source: dict) -> list:
        if source.get("_html_scraper"):
            return []
        results          = []
        cutoff           = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back)
        need_clean       = source.get("need_clean", False)
        is_cn            = source.get("lang", "en") == "zh-CN"
        source_category  = source.get("category", "")   # ★ 取得來源分類
        logger.info(
            f"\n  📡 [{source_category}]"
            f"[{source.get('lang','?')}] {source['name']}"
        )

        all_urls = [source['url']]
        if source.get('backup_url'):
            all_urls.append(source['backup_url'])
        all_urls.extend(source.get('extra_urls', []))

        feed = None
        for attempt_url in all_urls:
            logger.info(f"    🔗 {attempt_url[:70]}")
            feed = self._download_rss(attempt_url, need_clean, is_cn)
            if feed and feed.entries:
                break
            logger.warning("    ❌ 無資料，嘗試下一個")

        if feed is None or not feed.entries:
            logger.warning(f"  ⛔ {source['name']} 所有 URL 均失敗")
            return results

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

                # 快速排除財經噪音（航商來源跳過此步）
                if source_category != "航商動態":
                    if any(re.search(p, title) for p in self.SKIP_PATTERNS):
                        skipped_ctx += 1
                        continue

                # ★ 傳入 source_category，讓航商來源直通語境驗證
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
                                timeout=20, verify=False)
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

    # ── ★ 彙整所有來源（新增航商動態分類）───────────────────
    def fetch_all(self) -> dict:
        all_news: list = []

        # RSS 來源（含航商官方 RSS）
        for source in self.sources:
            all_news.extend(self.fetch_from_source(source))

        # 特殊爬蟲
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

        # 鉅亨網 API
        for cnyes_source in self.cnyes_sources:
            all_news.extend(self.fetch_from_cnyes(cnyes_source))

        # 時間排序（新 → 舊）
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
                         if n['source_category'] == '航商動態']   # ★ 新增
        intl_news     = [n for n in all_news
                         if n['source_category'] == '國際媒體']

        # ── 情境分類 ──────────────────────────────────────────
        cat_buckets: dict[str, list] = {k: [] for k in INCIDENT_CATEGORIES}
        for n in all_news:
            cat_buckets[n['incident_cat']].append(n)

        # ── 統計 log ──────────────────────────────────────────
        logger.info(f"\n{'='*60}")
        logger.info("📊 最終結果（媒體分類）:")
        logger.info(f"   🇹🇼 台灣新聞媒體: {len(zh_tw_news)} 筆")
        logger.info(f"   🇨🇳 大陸新聞媒體: {len(zh_cn_news)} 筆")
        logger.info(f"   🚢 航運專業媒體:  {len(shipping_news)} 筆")
        logger.info(f"   🏢 11大航商動態:  {len(carrier_news)} 筆")   # ★
        logger.info(f"   🌐 國際新聞媒體:  {len(intl_news)} 筆")
        logger.info(f"   📰 本次新聞總計:  {len(all_news)} 筆")
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
            'carrier':  carrier_news,   # ★ 新增
            'intl':     intl_news,
        }
        for cat_key in INCIDENT_CATEGORIES:
            result[cat_key.lower()] = cat_buckets[cat_key]
        return result


# ══════════════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logger.info("\n" + "=" * 60)
    logger.info("🚢 海事航運新聞監控系統 v6.2")
    logger.info("   分類：火災 / 碰撞觸礁 / 擱淺沉沒 / 海盜攻擊 / 船員傷亡")
    logger.info("   新增：11大航商營運動態 (CAT6)")
    logger.info("   發信模組：email_sender.py")
    logger.info("=" * 60)

    run_time   = datetime.now(tz=timezone.utc)
    hours_back = int(os.environ.get("NEWS_HOURS_BACK", "2"))

    scraper = NewsRssScraper(
        keywords      = ALL_KEYWORDS,
        sources       = RSS_SOURCES,
        cnyes_sources = CNYES_SOURCES,
        hours_back    = hours_back,
    )

    sender = NewsEmailSender(
        incident_categories = INCIDENT_CATEGORIES,
        rss_sources         = RSS_SOURCES,
        cnyes_sources       = CNYES_SOURCES,
    )

    try:
        news_data = scraper.fetch_all()
        sender.send(news_data, run_time)
        logger.info("✅ 執行完畢")
    except Exception as e:
        logger.error(f"❌ 執行失敗: {e}")
        traceback.print_exc()
        exit(1)
