#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
航運安全暨地緣政治新聞監控系統
GitHub Actions 版本 - 單次執行
版本: 4.5 - 精準關鍵字過濾 + 語境驗證
"""

import os
import io
import re
import ssl
import smtplib
import logging
import traceback
import calendar
import warnings
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from urllib3.exceptions import InsecureRequestWarning
warnings.filterwarnings('ignore', category=InsecureRequestWarning)

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


# ==================== 關鍵字設定 ====================
#
# 設計原則：
#   ✅ 使用「複合詞組」而非單字，確保有航運語境
#   ✅ 英文關鍵字需要至少 2 個詞（避免 Iran / Israel 單字誤觸）
#   ✅ 中文關鍵字保留單詞（中文不易誤觸）
#   ❌ 禁止：Iran, Israeli, Yemen, Gaza 等純地名/人名單字
# ─────────────────────────────────────────────────────────────

SHIPPING_KEYWORDS = [
    # ── 英文：船型（精確複合詞）──
    "oil tanker", "product tanker", "chemical tanker",
    "VLCC", "ULCC", "Aframax", "Suezmax",
    "LNG carrier", "LNG tanker", "LPG carrier",
    "container ship", "containership", "container vessel",
    "bulk carrier", "bulk vessel", "cargo vessel",
    "merchant vessel", "merchant ship",

    # ── 英文：航行狀態（需有航運語境）──
    "vessel rerouting", "ship diversion", "vessel delay",
    "port congestion", "port closure", "port blockade",
    "channel closure", "waterway closure",
    "Cape of Good Hope rerouting", "Cape routing",
    "freight rate", "shipping rate", "charter rate",
    "bunker fuel", "shipping cost",

    # ── 中文船型（繁體）──
    "油輪", "成品油輪", "化學品船", "貨櫃船", "散裝船",
    "液化天然氣船", "液化石油氣船", "商船", "貨輪",
    # ── 中文船型（簡體）──
    "油船", "成品油船", "化学品船", "集装箱船", "散装船",
    "液化天然气船", "商船", "货轮", "船舶",

    # ── 中文航行狀態（繁體）──
    "繞航", "改港", "停航", "好望角", "航道封閉", "塞港",
    "運費上漲", "運價", "航運市場",
    # ── 中文航行狀態（簡體）──
    "绕航", "停航", "航道封闭", "运费上涨", "运价", "航运市场",
    "港口拥堵", "港口封锁",
]

SECURITY_KEYWORDS = [
    # ── 英文：海上安全事件（必須有船舶語境）──
    "UKMTO alert", "UKMTO incident", "IMB piracy",
    "maritime piracy", "ship hijacking", "vessel hijacking",
    "armed attack on vessel", "armed robbery at sea",
    "vessel boarding", "ship boarding",
    "tanker attack", "vessel attack", "ship attack",
    "merchant ship attack", "merchant vessel attack",
    "sea mine", "naval mine", "limpet mine",
    "crew kidnapped", "seafarer kidnapped", "crew hostage",
    "Red Sea attack", "Red Sea incident", "Red Sea shipping",
    "maritime security incident", "maritime security alert",

    # ── 英文：關鍵航道（複合詞，避免單字誤觸）──
    "Strait of Hormuz", "Hormuz Strait", "Hormuz closure",
    "Hormuz shipping", "Hormuz tanker",
    "Suez Canal closure", "Suez Canal transit", "Suez Canal attack",
    "Panama Canal closure", "Panama Canal transit",
    "Red Sea closure", "Red Sea transit",
    "Gulf of Aden", "Bab el-Mandeb",
    "Persian Gulf shipping", "Persian Gulf tanker",
    "Gulf of Oman shipping",
    "Black Sea shipping", "Black Sea tanker",

    # ── 英文：武裝組織（需有攻擊/船舶語境）──
    "Houthi attack", "Houthi missile", "Houthi drone",
    "Houthi shipping", "Houthi tanker", "Houthi Red Sea",
    "IRGC vessel", "IRGC tanker", "IRGC seizure",
    "Iranian navy", "Iranian vessel", "Iranian tanker",

    # ── 英文：護航與保險（需有航運語境）──
    "naval escort shipping", "Operation Prosperity Guardian",
    "CTF-151", "Combined Maritime Forces",
    "war risk insurance shipping", "war risk premium tanker",
    "maritime war risk",

    # ── 中文安全事件（繁體）──
    "海盜攻擊", "海盜劫船", "武裝登船", "水雷威脅",
    "商船遇襲", "貨輪遭攻擊", "船員被劫", "船員被扣押",
    "紅海危機", "紅海攻擊", "紅海封鎖",
    # ── 中文安全事件（簡體）──
    "海盗攻击", "海盗劫船", "武装登船", "水雷威胁",
    "商船遇袭", "货轮遭攻击", "船员被劫", "船员被扣押",
    "红海危机", "红海攻击", "红海封锁",

    # ── 中文關鍵航道（繁體）──
    "霍爾木茲海峽", "荷姆茲海峽", "蘇伊士運河",
    "巴拿馬運河", "波斯灣航運", "亞丁灣",
    "曼德海峽", "黑海航運", "紅海航運",
    # ── 中文關鍵航道（簡體）──
    "霍尔木兹海峡", "苏伊士运河", "巴拿马运河",
    "波斯湾航运", "亚丁湾", "曼德海峡", "黑海航运", "红海航运",

    # ── 中文武裝組織（需有攻擊語境）──
    "胡塞攻擊", "胡塞飛彈", "胡塞無人機", "胡塞武裝攻船",
    "革命衛隊扣押", "革命衛隊船隻", "伊朗海軍扣押",
    # ── 簡體 ──
    "胡塞攻击", "胡塞导弹", "胡塞无人机", "胡塞武装攻船",
    "革命卫队扣押", "伊朗海军扣押",

    # ── 中文護航（繁體）──
    "護航艦隊", "繁榮衛士行動", "戰爭險", "航運保險",
    "戰爭附加費",
    # ── 簡體 ──
    "护航舰队", "繁荣卫士行动", "战争险", "航运保险",
    "战争附加费",
]

GEOPOLITICAL_KEYWORDS = [
    # ── 英文：必須有航運/能源語境的複合詞 ──
    "Iran oil sanctions", "Iran shipping sanctions",
    "Iran oil exports", "Iran oil tanker",
    "Iran nuclear shipping", "Iran strait threat",
    "oil embargo shipping", "energy sanctions tanker",
    "shadow fleet tanker", "shadow fleet sanctions",
    "dark fleet vessel", "dark fleet tanker",
    "sanctioned vessel", "sanctioned tanker",
    "shipping sanctions", "tanker sanctions",
    "strait closure threat", "naval blockade shipping",
    "maritime blockade", "oil supply disruption",
    "energy supply shipping", "crude oil shipping",
    "OPEC oil supply", "oil production shipping",

    # ── 中文（繁體）──
    "伊朗石油制裁", "伊朗航運制裁", "伊朗石油出口",
    "制裁油輪", "制裁船隊", "石油禁運",
    "影子船隊", "黑名單船舶", "海峽封鎖威脅",
    "海上封鎖", "能源供應中斷", "石油供應",
    # ── 簡體 ──
    "伊朗石油制裁", "伊朗航运制裁", "伊朗石油出口",
    "制裁油轮", "制裁船队", "石油禁运",
    "影子船队", "黑名单船舶", "海峡封锁威胁",
    "海上封锁", "能源供应中断", "石油供应",
]

# ── 合併並去重 ──
ALL_KEYWORDS = SHIPPING_KEYWORDS + SECURITY_KEYWORDS + GEOPOLITICAL_KEYWORDS
_seen_kw = set()
_deduped = []
for kw in ALL_KEYWORDS:
    if kw.lower() not in _seen_kw:
        _deduped.append(kw)
        _seen_kw.add(kw.lower())
ALL_KEYWORDS = _deduped

KEYWORD_CATEGORY_MAP = {
    **{kw.lower(): ("航運動態", "#3b82f6") for kw in SHIPPING_KEYWORDS},
    **{kw.lower(): ("海上安全", "#f97316") for kw in SECURITY_KEYWORDS},
    **{kw.lower(): ("地緣政治", "#ef4444") for kw in GEOPOLITICAL_KEYWORDS},
}

# ==================== 核心航運詞（語境驗證用）====================
#
# 一篇文章必須同時包含「至少一個核心航運詞」
# 才能通過語境驗證，避免純政治/社會新聞誤入
#
CORE_SHIPPING_TERMS = {
    # 英文核心詞
    "tanker", "vessel", "ship", "shipping", "maritime",
    "fleet", "cargo", "freight", "port", "canal",
    "strait", "suez", "hormuz", "panama", "red sea",
    "gulf of aden", "persian gulf", "bab el-mandeb",
    "vlcc", "lng", "lpg", "bunker", "charter",
    # 中文核心詞（繁體）
    "油輪", "船", "航運", "海運", "貨輪", "港口",
    "運河", "海峽", "紅海", "波斯灣", "亞丁灣",
    "運費", "船舶", "貨櫃",
    # 中文核心詞（簡體）
    "油船", "航运", "海运", "货轮", "港口",
    "运河", "海峡", "红海", "波斯湾", "亚丁湾",
    "运费", "船舶", "集装箱",
}

logger.info(
    f"📚 關鍵字載入 | "
    f"航運: {len(SHIPPING_KEYWORDS)} | "
    f"安全: {len(SECURITY_KEYWORDS)} | "
    f"地緣: {len(GEOPOLITICAL_KEYWORDS)} | "
    f"去重後: {len(ALL_KEYWORDS)} 個"
)


# ==================== RSS 來源設定 ====================
RSS_SOURCES = [

    # ════════════════════════════════
    # 📰 中文媒體（台灣）
    # ════════════════════════════════
    {
        "name": "自由時報",
        "url": "https://news.ltn.com.tw/rss/world.xml",
        "backup_url": "https://news.ltn.com.tw/rss/all.xml",
        "extra_urls": [],
        "lang": "zh-TW", "icon": "🇹🇼", "category": "中文媒體",
    },
    {
        "name": "聯合新聞網",
        "url": "https://udn.com/rssfeed/news/2/6638?ch=news",
        "backup_url": "https://udn.com/rssfeed/news/2/6638",
        "extra_urls": [],
        "lang": "zh-TW", "icon": "📰", "category": "中文媒體",
    },
    {
        "name": "中央社",
        "url": "https://www.cna.com.tw/rss/fnall.aspx",
        "backup_url": "https://www.cna.com.tw/rss/aie.aspx",
        "extra_urls": [],
        "lang": "zh-TW", "icon": "🏛️", "category": "中文媒體",
        "need_clean": True,
    },
    {
        "name": "Yahoo新聞",
        "url": "https://tw.news.yahoo.com/rss/world",
        "backup_url": "https://tw.news.yahoo.com/rss/",
        "extra_urls": [],
        "lang": "zh-TW", "icon": "🟣", "category": "中文媒體",
    },
    {
        "name": "風傳媒",
        "url": "https://www.storm.mg/feeds/rss",
        "backup_url": None,
        "extra_urls": [],
        "lang": "zh-TW", "icon": "🌪️", "category": "中文媒體",
    },

    # ════════════════════════════════
    # 🇨🇳 中文媒體（大陸）
    # ════════════════════════════════
    {
        "name": "海事服務網 CNSS",
        "url": "https://rsshub.app/cnss/news",
        "backup_url": "https://www.cnss.com.cn/rss/news.xml",
        "extra_urls": [
            "https://rsshub.rssforever.com/cnss/news",
            "https://hub.slarker.me/cnss/news",
        ],
        "lang": "zh-CN", "icon": "⚓", "category": "中文媒體",
        "need_clean": True,
    },
    {
        "name": "人民網 國際",
        "url": "https://rsshub.app/people/world",
        "backup_url": "http://www.people.com.cn/rss/world.xml",
        "extra_urls": [
            "https://rsshub.rssforever.com/people/world",
        ],
        "lang": "zh-CN", "icon": "🏮", "category": "中文媒體",
        "need_clean": True,
    },
    {
        "name": "環球時報",
        "url": "https://rsshub.app/huanqiu/world",
        "backup_url": "https://www.huanqiu.com/rss",
        "extra_urls": [
            "https://rsshub.rssforever.com/huanqiu/world",
            "https://rsshub.app/huanqiu/mil",
        ],
        "lang": "zh-CN", "icon": "🌏", "category": "中文媒體",
        "need_clean": True,
    },
    {
        "name": "新華社 國際",
        "url": "https://rsshub.app/xinhua/world",
        "backup_url": "http://www.xinhuanet.com/world/news_world.xml",
        "extra_urls": [
            "https://rsshub.rssforever.com/xinhua/world",
        ],
        "lang": "zh-CN", "icon": "📻", "category": "中文媒體",
        "need_clean": True,
    },
    {
        "name": "澎湃新聞 國際",
        "url": "https://rsshub.app/thepaper/channel/25950",
        "backup_url": "https://rsshub.app/thepaper/channel/121811",
        "extra_urls": [
            "https://rsshub.rssforever.com/thepaper/channel/25950",
        ],
        "lang": "zh-CN", "icon": "🗞️", "category": "中文媒體",
        "need_clean": True,
    },
    {
        "name": "財新網 國際",
        "url": "https://rsshub.app/caixin/international",
        "backup_url": "https://rsshub.app/caixin/economy",
        "extra_urls": [
            "https://rsshub.rssforever.com/caixin/international",
        ],
        "lang": "zh-CN", "icon": "💹", "category": "中文媒體",
        "need_clean": True,
    },

    # ════════════════════════════════
    # 🚢 專業航運媒體（英文）
    # ════════════════════════════════
    {
        "name": "TradeWinds",
        "url": "https://rss.app/feeds/tvCHOGHBWmcHkBKM.xml",
        "backup_url": "https://www.tradewindsnews.com/latest",
        "extra_urls": [],
        "lang": "en", "icon": "🚢", "category": "航運專業",
    },
    {
        "name": "Splash247",
        "url": "https://splash247.com/feed/",
        "backup_url": None,
        "extra_urls": [],
        "lang": "en", "icon": "⚓", "category": "航運專業",
    },
    {
        "name": "gCaptain",
        "url": "https://gcaptain.com/feed/",
        "backup_url": "https://gcaptain.com/feed/rss/",
        "extra_urls": [],
        "lang": "en", "icon": "🧭", "category": "航運專業",
    },
    {
        "name": "Maritime Exec",
        "url": "https://maritime-executive.com/magazine/rss",
        "backup_url": "https://maritime-executive.com/rss",
        "extra_urls": [],
        "lang": "en", "icon": "⛴️", "category": "航運專業",
    },
    {
        "name": "Hellenic Ship",
        "url": "https://www.hellenicshippingnews.com/feed/",
        "backup_url": "https://www.hellenicshippingnews.com/feed/rss/",
        "extra_urls": [],
        "lang": "en", "icon": "🏛️", "category": "航運專業",
    },
    {
        "name": "Safety4Sea",
        "url": "https://safety4sea.com/feed/",
        "backup_url": "https://safety4sea.com/feed/rss/",
        "extra_urls": [],
        "lang": "en", "icon": "🛡️", "category": "航運專業",
    },
    {
        "name": "Container News",
        "url": "https://container-news.com/feed/",
        "backup_url": None,
        "extra_urls": [],
        "lang": "en", "icon": "📦", "category": "航運專業",
    },
    {
        "name": "Freightwaves",
        "url": "https://www.freightwaves.com/news/feed",
        "backup_url": "https://www.freightwaves.com/feed",
        "extra_urls": [],
        "lang": "en", "icon": "📊", "category": "航運專業",
    },
    {
        "name": "Offshore Energy",
        "url": "https://www.offshore-energy.biz/feed/",
        "backup_url": None,
        "extra_urls": [],
        "lang": "en", "icon": "⚡", "category": "航運專業",
    },

    # ════════════════════════════════
    # 🌐 國際綜合媒體（英文）
    # ════════════════════════════════
    {
        "name": "Reuters",
        "url": "https://feeds.reuters.com/reuters/worldNews",
        "backup_url": "https://news.yahoo.com/rss/world",
        "extra_urls": [],
        "lang": "en", "icon": "🌐", "category": "國際媒體",
    },
    {
        "name": "BBC News",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "backup_url": "https://feeds.bbci.co.uk/news/rss.xml",
        "extra_urls": [],
        "lang": "en", "icon": "🇬🇧", "category": "國際媒體",
    },
    {
        "name": "Al Jazeera",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "backup_url": None,
        "extra_urls": [],
        "lang": "en", "icon": "🌍", "category": "國際媒體",
    },
    {
        "name": "The Guardian",
        "url": "https://www.theguardian.com/world/rss",
        "backup_url": None,
        "extra_urls": [],
        "lang": "en", "icon": "🗞️", "category": "國際媒體",
    },
    {
        "name": "AP News",
        "url": "https://rsshub.app/apnews/topics/world-news",
        "backup_url": "https://feeds.apnews.com/rss/apf-topnews",
        "extra_urls": [],
        "lang": "en", "icon": "📡", "category": "國際媒體",
    },
]


# ==================== XML 清洗工具 ====================
def clean_xml_content(raw_bytes: bytes) -> str:
    try:
        text = raw_bytes.decode('utf-8', errors='replace')
    except Exception:
        text = raw_bytes.decode('latin-1', errors='replace')
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    text = re.sub(r'&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', text)
    return text


# ==================== 新聞爬取器 ====================
class NewsRssScraper:
    HEADERS_DEFAULT = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }
    HEADERS_CN = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }

    def __init__(self, keywords: list, sources: list, hours_back: int = 2):
        self.keywords   = keywords
        self.sources    = sources
        self.hours_back = hours_back
        self.seen_urls  = set()

    def _match_keywords(self, text: str) -> list[tuple]:
        """
        關鍵字比對 + 語境驗證
        規則：命中關鍵字 AND 文章包含至少一個核心航運詞
        """
        if not text:
            return []
        text_lower = text.lower()

        # ── Step 1：先做語境驗證（核心航運詞檢查）──
        has_shipping_context = any(
            core in text_lower for core in CORE_SHIPPING_TERMS
        )
        if not has_shipping_context:
            return []  # 無航運語境，直接排除

        # ── Step 2：比對關鍵字 ──
        matched, seen_kw = [], set()
        for kw in self.keywords:
            if kw.lower() in text_lower and kw not in seen_kw:
                cat, color = KEYWORD_CATEGORY_MAP.get(kw.lower(), ("其他", "#94a3b8"))
                matched.append((kw, cat, color))
                seen_kw.add(kw)
        return matched

    def _parse_published_time(self, entry) -> datetime | None:
        """處理大陸 RSS 常見的非標準時間格式（CST、中文格式等）"""
        try:
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                t = entry.published_parsed
                if t.tm_year >= 2000:
                    ts = calendar.timegm(t)
                    if ts > 0:
                        return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass

        raw_time = getattr(entry, 'published', '') or getattr(entry, 'updated', '') or ''
        if raw_time:
            formats = [
                '%a, %d %b %Y %H:%M:%S %z',
                '%a, %d %b %Y %H:%M:%S GMT',
                '%Y-%m-%dT%H:%M:%S%z',
                '%Y-%m-%dT%H:%M:%SZ',
                '%Y-%m-%d %H:%M:%S',
                '%Y年%m月%d日 %H:%M',
                '%Y/%m/%d %H:%M:%S',
            ]
            raw_clean = raw_time.replace(' CST', ' +0800').replace(' +0800 (CST)', ' +0800')
            for fmt in formats:
                try:
                    dt = datetime.strptime(raw_clean.strip(), fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                    return dt.astimezone(timezone.utc)
                except ValueError:
                    continue
        return None

    def _download_rss(self, url: str, need_clean: bool = False,
                      is_cn: bool = False):
        headers = self.HEADERS_CN if is_cn else self.HEADERS_DEFAULT
        try:
            resp = requests.get(
                url, headers=headers,
                timeout=20, verify=False, allow_redirects=True,
            )
            resp.raise_for_status()

            if len(resp.content) < 100:
                logger.warning(f"    ⚠️  回應過短 ({len(resp.content)} bytes): {url[:60]}")
                return None

            if need_clean:
                parsed = feedparser.parse(io.StringIO(clean_xml_content(resp.content)))
            else:
                parsed = feedparser.parse(io.BytesIO(resp.content))

            entry_count = len(parsed.entries) if parsed else 0
            bozo        = getattr(parsed, 'bozo', False)
            bozo_exc    = getattr(parsed, 'bozo_exception', None)
            logger.info(
                f"    📊 {entry_count} 則 | bozo={bozo}"
                + (f" ({type(bozo_exc).__name__})" if bozo_exc else "")
            )
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

    def fetch_from_source(self, source: dict) -> list:
        results    = []
        cutoff     = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back)
        need_clean = source.get("need_clean", False)
        is_cn      = source.get("lang", "en") == "zh-CN"

        logger.info(
            f"\n  📡 [{source.get('category','?')}]"
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
            logger.warning(f"    ❌ 無資料，嘗試下一個")

        if feed is None or not feed.entries:
            logger.warning(f"  ⛔ {source['name']} 所有 URL 均失敗")
            return results

        matched_count = 0
        skipped_time  = 0
        skipped_ctx   = 0  # 語境過濾
        skipped_kw    = 0
        skipped_dup   = 0

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

                full_text = f"{title} {summary}"

                # 語境預檢（快速排除）
                full_lower = full_text.lower()
                has_ctx = any(c in full_lower for c in CORE_SHIPPING_TERMS)
                if not has_ctx:
                    skipped_ctx += 1
                    continue

                matched = self._match_keywords(full_text)
                if not matched:
                    skipped_kw += 1
                    continue

                if link:
                    self.seen_urls.add(link)

                summary_clean = re.sub(r'<[^>]+>', '', summary).strip()[:300]
                if len(summary_clean) == 300:
                    summary_clean += "..."

                results.append({
                    'source_name':     source['name'],
                    'source_icon':     source['icon'],
                    'source_lang':     source.get('lang', 'en'),
                    'source_category': source.get('category', ''),
                    'title':           title.strip(),
                    'summary':         summary_clean,
                    'link':            link,
                    'published': (
                        pub_time.strftime('%Y-%m-%d %H:%M UTC')
                        if pub_time else '時間未知'
                    ),
                    'matched': matched,
                })
                matched_count += 1

            except Exception as e:
                logger.warning(f"    ⚠️  解析失敗: {e}")

        logger.info(
            f"  📋 {source['name']} | "
            f"總 {len(feed.entries)} | "
            f"命中 {matched_count} | "
            f"無語境 {skipped_ctx} | "
            f"無關鍵字 {skipped_kw} | "
            f"時間 {skipped_time} | "
            f"重複 {skipped_dup}"
        )
        return results

    def fetch_all(self) -> dict:
        all_news = []
        for source in self.sources:
            all_news.extend(self.fetch_from_source(source))

        all_news.sort(
            key=lambda x: x['published'] if x['published'] != '時間未知' else '0000',
            reverse=True
        )

        zh_tw_news    = [n for n in all_news if n['source_category'] == '中文媒體' and n['source_lang'] == 'zh-TW']
        zh_cn_news    = [n for n in all_news if n['source_category'] == '中文媒體' and n['source_lang'] == 'zh-CN']
        shipping_news = [n for n in all_news if n['source_category'] == '航運專業']
        intl_news     = [n for n in all_news if n['source_category'] == '國際媒體']

        logger.info(
            f"\n{'='*50}\n"
            f"📊 最終結果:\n"
            f"   🇹🇼 台灣中文: {len(zh_tw_news)} 筆\n"
            f"   🇨🇳 大陸中文: {len(zh_cn_news)} 筆\n"
            f"   🚢 航運專業: {len(shipping_news)} 筆\n"
            f"   🌐 國際媒體: {len(intl_news)} 筆\n"
            f"   📰 總計:     {len(all_news)} 筆\n"
            f"{'='*50}"
        )

        return {
            'all':      all_news,
            'zh_tw':    zh_tw_news,
            'zh_cn':    zh_cn_news,
            'shipping': shipping_news,
            'intl':     intl_news,
        }


# ==================== Email 發送器（純 HTML Table 版）====================
class NewsEmailSender:
    SECTION_COLORS = {
        '中文媒體台灣': {'color': '#10b981', 'bg': '#ecfdf5', 'icon': '🇹🇼'},
        '中文媒體大陸': {'color': '#ef4444', 'bg': '#fef2f2', 'icon': '🇨🇳'},
        '航運專業':     {'color': '#3b82f6', 'bg': '#eff6ff', 'icon': '🚢'},
        '國際媒體':     {'color': '#f97316', 'bg': '#fff7ed', 'icon': '🌐'},
    }

    def __init__(self):
        self.mail_user    = os.environ.get("MAIL_USER",          "")
        self.mail_pass    = os.environ.get("MAIL_PASSWORD",      "")
        self.target_email = os.environ.get("TARGET_EMAIL",       "")
        self.smtp_server  = os.environ.get("MAIL_SMTP_SERVER",   "smtp.gmail.com")
        self.smtp_port    = int(os.environ.get("MAIL_SMTP_PORT", "587"))
        self.enabled      = all([self.mail_user, self.mail_pass, self.target_email])

        if not self.enabled:
            logger.error("❌ Email 環境變數未設定：MAIL_USER / MAIL_PASSWORD / TARGET_EMAIL")
        else:
            logger.info(f"✅ Email → {self.target_email}")

    def send(self, news_data: dict, run_time: datetime) -> bool:
        if not self.enabled:
            return False
        total = len(news_data.get('all', []))
        if total == 0:
            logger.info("ℹ️  無相關新聞，跳過發送")
            return False
        try:
            tpe_time = run_time.astimezone(timezone(timedelta(hours=8)))
            subject  = (
                f"Maritime Intel News Alert - {total} News Matched "
                f"({tpe_time.strftime('%m/%d %H:%M')})"
            )
            msg            = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From']    = f"航運監控系統 <{self.mail_user}>"
            msg['To']      = self.target_email
            msg.attach(MIMEText(
                self._generate_html(news_data, run_time), 'html', 'utf-8'
            ))
            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.mail_user, self.mail_pass)
                server.send_message(msg)
            logger.info("✅ Email 發送成功")
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error("❌ Gmail 認證失敗，請確認 App Password")
        except Exception as e:
            logger.error(f"❌ Email 發送失敗: {e}")
            traceback.print_exc()
        return False

    @staticmethod
    def _render_card(item: dict, border_color: str) -> str:
        kw_parts = []
        for kw, cat, color in item['matched'][:6]:
            kw_parts.append(
                f'<td bgcolor="{color}" style="padding:3px 8px;">'
                f'<font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#ffffff">'
                f'<b>{kw}</b></font></td><td width="4"></td>'
            )
        kw_html = (
            f'<table border="0" cellpadding="0" cellspacing="0">'
            f'<tr>{"".join(kw_parts)}</tr></table>'
        ) if kw_parts else ""

        pub = item['published']
        if pub != '時間未知':
            try:
                dt  = datetime.strptime(pub, '%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc)
                pub = dt.astimezone(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')
            except Exception:
                pass

        safe_title   = item['title'].replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        safe_summary = item['summary'].replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

        return f"""
        <table width="100%" border="0" cellpadding="1" cellspacing="0" bgcolor="#e2e8f0">
        <tr><td>
        <table width="100%" border="0" cellpadding="0" cellspacing="0" bgcolor="#ffffff">
        <tr>
            <td width="5" bgcolor="{border_color}">&nbsp;</td>
            <td>
            <table width="100%" border="0" cellpadding="14" cellspacing="0"><tr><td>
                <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
                    <td align="left">
                        <table border="0" cellpadding="4" cellspacing="0" bgcolor="#f1f5f9"><tr><td>
                            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">
                                <b>{item['source_icon']} {item['source_name']}</b>
                            </font>
                        </td></tr></table>
                    </td>
                    <td align="right">
                        <font face="Arial,sans-serif" size="2" color="#94a3b8">🕐 {pub}</font>
                    </td>
                </tr></table>
                <br>
                <a href="{item['link']}" target="_blank" style="text-decoration:none;">
                    <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
                        <b>{safe_title}</b>
                    </font>
                </a>
                <br><br>
                <table width="100%" border="0" cellpadding="10" cellspacing="0" bgcolor="#f8fafc"><tr><td>
                    <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#64748b">
                        {safe_summary or '（無摘要）'}
                    </font>
                </td></tr></table>
                <br>
                <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
                    <td align="left" valign="middle">{kw_html}</td>
                    <td align="right" valign="middle">
                        <table border="0" cellpadding="8" cellspacing="0" bgcolor="{border_color}"><tr><td>
                            <a href="{item['link']}" target="_blank" style="text-decoration:none;">
                                <font face="Arial,sans-serif" size="2" color="#ffffff">
                                    <b>閱讀原文 &rarr;</b>
                                </font>
                            </a>
                        </td></tr></table>
                    </td>
                </tr></table>
            </td></tr></table>
            </td>
        </tr>
        </table>
        </td></tr></table>
        <br>
        """

    def _render_section(self, title: str, news_list: list, cfg_key: str) -> str:
        if not news_list:
            return ""
        cfg   = self.SECTION_COLORS.get(cfg_key, {'color':'#64748b','bg':'#f1f5f9','icon':'📄'})
        cards = "".join(self._render_card(item, cfg['color']) for item in news_list)
        return f"""
        <table width="100%" border="0" cellpadding="0" cellspacing="0" bgcolor="{cfg['bg']}">
        <tr>
            <td width="5" bgcolor="{cfg['color']}">&nbsp;</td>
            <td>
            <table width="100%" border="0" cellpadding="10" cellspacing="0"><tr>
                <td align="left" valign="middle">
                    <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
                        <b>{cfg['icon']} {title}</b>
                    </font>
                </td>
                <td align="right" valign="middle">
                    <table border="0" cellpadding="5" cellspacing="0" bgcolor="{cfg['color']}"><tr><td>
                        <font face="Arial,sans-serif" size="2" color="#ffffff">
                            <b>{len(news_list)} 篇</b>
                        </font>
                    </td></tr></table>
                </td>
            </tr></table>
            </td>
        </tr>
        </table>
        <br>
        {cards}
        <br>
        """

    def _generate_html(self, news_data: dict, run_time: datetime) -> str:
        tpe_str = run_time.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')

        source_stats = {}
        for item in news_data.get('all', []):
            key = f"{item['source_icon']} {item['source_name']}"
            source_stats[key] = source_stats.get(key, 0) + 1

        _sorted_sources = sorted(source_stats.items(), key=lambda x: -x[1])

        def _stat_row(src: str, cnt: int) -> str:
            return (
                f'<tr>'
                f'<td align="left" bgcolor="#ffffff" style="padding:8px 12px;">'
                f'<font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">{src}</font>'
                f'</td>'
                f'<td align="right" bgcolor="#ffffff" style="padding:8px 12px;">'
                f'<font face="Arial,sans-serif" size="2" color="#3b82f6"><b>{cnt} 則</b></font>'
                f'</td>'
                f'</tr>'
            )

        stat_rows = (
            "".join(_stat_row(s, c) for s, c in _sorted_sources)
            or '<tr><td colspan="2" style="padding:10px 12px;">'
               '<font face="Arial,sans-serif" size="2" color="#94a3b8">無資料</font></td></tr>'
        )

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>航運安全快報</title>
</head>
<body bgcolor="#e2e8f0" text="#000000">
<table width="100%" border="0" cellpadding="20" cellspacing="0" bgcolor="#e2e8f0">
<tr><td align="center" valign="top">
<table width="700" border="0" cellpadding="0" cellspacing="0" bgcolor="#ffffff">

    <!-- HEADER -->
    <tr><td bgcolor="#0f172a" align="center">
        <table width="100%" border="0" cellpadding="30" cellspacing="0"><tr><td align="center">
            <font size="7" color="#ffffff">🚢</font><br><br>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="5" color="#f8fafc">
                <b>Maritime Intel News Alert</b>
            </font><br><br>
            <font face="Arial,sans-serif" size="2" color="#94a3b8">
                {tpe_str} (台北時間)
            </font><br><br>
            <table border="0" cellpadding="6" cellspacing="0" bgcolor="#1e293b"><tr><td>
                <font face="Arial,sans-serif" size="2" color="#94a3b8">
                    來源 {len(RSS_SOURCES)} 個 &nbsp;|&nbsp;
                    關鍵字 {len(ALL_KEYWORDS)} 個（繁簡雙語 + 語境驗證）
                </font>
            </td></tr></table>
        </td></tr></table>
    </td></tr>

    <!-- 統計列 -->
    <tr><td bgcolor="#ffffff">
        <table width="100%" border="1" bordercolor="#e2e8f0" cellpadding="15" cellspacing="0"><tr>
            <td align="center" width="20%">
                <font face="Arial,sans-serif" size="6" color="#0f172a">
                    <b>{len(news_data['all'])}</b>
                </font><br>
                <font face="Arial,sans-serif" size="1" color="#94a3b8">TOTAL</font>
            </td>
            <td align="center" width="20%">
                <font face="Arial,sans-serif" size="6" color="#10b981">
                    <b>{len(news_data['zh_tw'])}</b>
                </font><br>
                <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#94a3b8">🇹🇼 台灣</font>
            </td>
            <td align="center" width="20%">
                <font face="Arial,sans-serif" size="6" color="#ef4444">
                    <b>{len(news_data['zh_cn'])}</b>
                </font><br>
                <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#94a3b8">🇨🇳 大陸</font>
            </td>
            <td align="center" width="20%">
                <font face="Arial,sans-serif" size="6" color="#3b82f6">
                    <b>{len(news_data['shipping'])}</b>
                </font><br>
                <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#94a3b8">🚢 專業</font>
            </td>
            <td align="center" width="20%">
                <font face="Arial,sans-serif" size="6" color="#f97316">
                    <b>{len(news_data['intl'])}</b>
                </font><br>
                <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#94a3b8">🌐 國際</font>
            </td>
        </tr></table>
    </td></tr>

    <!-- 關鍵字圖例 -->
    <tr><td bgcolor="#fffbeb">
        <table width="100%" border="0" cellpadding="10" cellspacing="0"><tr><td align="left" valign="middle">
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#92400e">
                <b>🏷️ 關鍵字分類：</b>
            </font>
            &nbsp;
            <table border="0" cellpadding="0" cellspacing="0" style="display:inline-table;"><tr>
                <td bgcolor="#3b82f6" style="padding:3px 10px;">
                    <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#ffffff">航運動態</font>
                </td>
                <td width="6"></td>
                <td bgcolor="#f97316" style="padding:3px 10px;">
                    <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#ffffff">海上安全</font>
                </td>
                <td width="6"></td>
                <td bgcolor="#ef4444" style="padding:3px 10px;">
                    <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#ffffff">地緣政治</font>
                </td>
                <td width="12"></td>
                <td>
                    <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#92400e">
                        ✦ 繁簡雙語 + 語境驗證
                    </font>
                </td>
            </tr></table>
        </td></tr></table>
    </td></tr>

    <!-- 主內容 -->
    <tr><td bgcolor="#f8fafc">
        <table width="100%" border="0" cellpadding="20" cellspacing="0"><tr><td>
            {self._render_section('中文媒體（台灣）', news_data['zh_tw'],    '中文媒體台灣')}
            {self._render_section('中文媒體（大陸）', news_data['zh_cn'],    '中文媒體大陸')}
            {self._render_section('航運專業媒體',     news_data['shipping'], '航運專業')}
            {self._render_section('國際媒體',         news_data['intl'],     '國際媒體')}
        </td></tr></table>
    </td></tr>

    <!-- 來源統計 -->
    <tr><td bgcolor="#ffffff">
        <table width="100%" border="0" cellpadding="20" cellspacing="0"><tr><td>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#475569">
                <b>📊 本次來源分布</b>
            </font>
            <br><br>
            <table width="100%" border="1" bordercolor="#e2e8f0" cellpadding="0" cellspacing="0">
                {stat_rows}
            </table>
        </td></tr></table>
    </td></tr>

    <!-- FOOTER -->
    <tr><td bgcolor="#1e293b" align="center">
        <table width="100%" border="0" cellpadding="20" cellspacing="0"><tr><td align="center">
            <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
                🤖 此為 GitHub Actions 自動發送郵件，請勿直接回覆
            </font><br><br>
            <font face="Arial,sans-serif" size="2" color="#475569">
                航運安全監控系統 v4.5 &nbsp;·&nbsp; Powered by Python &amp; GitHub Actions
            </font>
        </td></tr></table>
    </td></tr>

</table>
</td></tr></table>
</body>
</html>"""


# ==================== 主程式 ====================
if __name__ == "__main__":
    logger.info("\n" + "=" * 60)
    logger.info("🚢 航運安全監控系統 v4.5")
    logger.info("   精準關鍵字 + 語境驗證 + 繁簡雙語")
    logger.info("=" * 60)

    run_time   = datetime.now(tz=timezone.utc)
    hours_back = int(os.environ.get("NEWS_HOURS_BACK", "2"))

    scraper = NewsRssScraper(
        keywords   = ALL_KEYWORDS,
        sources    = RSS_SOURCES,
        hours_back = hours_back,
    )
    sender = NewsEmailSender()

    try:
        news_data = scraper.fetch_all()
        sender.send(news_data, run_time)
        logger.info("✅ 執行完畢")
    except Exception as e:
        logger.error(f"❌ 執行失敗: {e}")
        traceback.print_exc()
        exit(1)
