#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
航運安全暨地緣政治新聞監控系統
GitHub Actions 版本 - 單次執行
版本: 4.3 - 繁簡雙語關鍵字 + 大陸航運來源
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


# ==================== 關鍵字設定（繁簡雙語）====================
#
# 每個中文關鍵字都同時提供繁體與簡體版本
# 確保大陸媒體（簡體）與台灣媒體（繁體）都能被命中
# ─────────────────────────────────────────────────────────────

SHIPPING_KEYWORDS = [
    # ── 英文船型 ──
    "tanker", "VLCC", "LNG carrier", "LPG carrier",
    "container ship", "containership", "bulk carrier",
    "cargo ship", "oil tanker", "merchant vessel",

    # ── 中文船型（繁體）──
    "油輪", "貨櫃船", "散裝船", "液化天然氣船", "商船", "化學品船",
    # ── 中文船型（簡體）──
    "货柜船", "散装船", "液化天然气船", "化学品船","集装箱",

    # ── 英文航行狀態 ──
    "vessel delay", "rerouting", "diversion", "Cape of Good Hope",
    "port closure", "channel closure", "freight rate",

    # ── 中文航行狀態（繁體）──
    "繞航", "改港", "停航", "好望角", "航道封閉", "塞港", "運價",
    # ── 中文航行狀態（簡體）──
    "绕航", "改港", "停航", "好望角", "航道封闭", "塞港", "运价",
]

SECURITY_KEYWORDS = [
    # ── 英文安全事件 ──
    "UKMTO", "IMB", "maritime security",
    "piracy", "ship hijack", "armed robbery at sea", "vessel boarding",
    "vessel attack", "ship attack", "tanker attack", "merchant ship struck",
    "sea mine", "limpet mine", "crew kidnapped", "Red Sea attack",

    # ── 中文安全事件（繁體）──
    "海盜", "劫船", "武裝登船", "水雷", "商船遇襲", "貨輪被飛彈", "船員被劫", "紅海危機",
    # ── 中文安全事件（簡體）──
    "海盗", "劫船", "武装登船", "水雷", "商船遇袭", "货轮被导弹", "船员被劫", "红海危机",

    # ── 英文關鍵航道 ──
    "Strait of Hormuz", "Hormuz", "Suez Canal", "Panama Canal",
    "Red Sea", "Gulf of Aden", "Bab el-Mandeb",
    "Persian Gulf", "Gulf of Oman", "Black Sea shipping",

    # ── 中文關鍵航道（繁體）──
    "霍爾木茲海峽", "荷姆茲海峽", "蘇伊士運河", "巴拿馬運河",
    "波斯灣", "阿曼灣", "紅海", "亞丁灣", "曼德海峽", "黑海航運",
    # ── 中文關鍵航道（簡體）──
    "霍尔木兹海峡", "苏伊士运河", "巴拿马运河",
    "波斯湾", "阿曼湾", "红海", "亚丁湾", "曼德海峡", "黑海航运",

    # ── 英文武裝組織 ──
    "Houthi", "Houthis", "IRGC", "Iranian Revolutionary Guard",

    # ── 中文武裝組織（繁體）──
    "胡塞", "革命衛隊", "伊斯蘭革命衛隊",
    # ── 中文武裝組織（簡體）──
    "胡塞武装", "伊斯兰革命卫队", "革命卫队",

    # ── 英文護航保險 ──
    "naval escort", "Operation Prosperity Guardian", "CTF-151",
    "war risk insurance", "war risk premium",

    # ── 中文護航保險（繁體）──
    "護航艦隊", "繁榮衛士行動", "戰爭險", "戰爭附加費", "航運保險",
    # ── 中文護航保險（簡體）──
    "护航舰队", "繁荣卫士行动", "战争险", "战争附加费", "航运保险",
]

GEOPOLITICAL_KEYWORDS = [
    # ── 英文地區 ──
    "Iran", "Iranian", "US-Iran", "Iran sanctions", "Iran oil",
    "oil embargo", "shadow fleet", "dark fleet", "shipping sanctions",
    "strait closure", "naval blockade", "maritime patrol",

    # ── 中文地區（繁體）──
    "制裁船隊", "石油禁運", "影子船隊", "黑名單船舶", "海峽封鎖", "海上封鎖",
    # ── 中文地區（簡體）──
    "制裁船队", "石油禁运", "影子船队", "黑名单船舶", "海峡封锁", "海上封锁",
]

# ── 合併並去重（大小寫不敏感）──
ALL_KEYWORDS = SHIPPING_KEYWORDS + SECURITY_KEYWORDS + GEOPOLITICAL_KEYWORDS
_seen_kw = set()
_deduped = []
for kw in ALL_KEYWORDS:
    if kw.lower() not in _seen_kw:
        _deduped.append(kw)
        _seen_kw.add(kw.lower())
ALL_KEYWORDS = _deduped

# ── 關鍵字分類對應（繁簡體共用同一分類）──
KEYWORD_CATEGORY_MAP = {
    **{kw.lower(): ("航運動態", "#3b82f6") for kw in SHIPPING_KEYWORDS},
    **{kw.lower(): ("海上安全", "#f97316") for kw in SECURITY_KEYWORDS},
    **{kw.lower(): ("地區", "#ef4444") for kw in GEOPOLITICAL_KEYWORDS},
}

logger.info(
    f"📚 關鍵字載入完成 | "
    f"航運動態: {len(SHIPPING_KEYWORDS)} | "
    f"海上安全: {len(SECURITY_KEYWORDS)} | "
    f"地緣政治: {len(GEOPOLITICAL_KEYWORDS)} | "
    f"去重後總計: {len(ALL_KEYWORDS)} 個"
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
        "lang": "zh-TW", "icon": "🇹🇼", "category": "中文媒體",
    },
    {
        "name": "聯合新聞網",
        "url": "https://udn.com/rssfeed/news/2/6638?ch=news",
        "backup_url": "https://udn.com/rssfeed/news/2/6638",
        "lang": "zh-TW", "icon": "📰", "category": "中文媒體",
    },
    {
        "name": "中央社",
        "url": "https://www.cna.com.tw/rss/fnall.aspx",
        "backup_url": "https://www.cna.com.tw/rss/aie.aspx",
        "lang": "zh-TW", "icon": "🏛️", "category": "中文媒體",
        "need_clean": True,
    },
    {
        "name": "Yahoo新聞",
        "url": "https://tw.news.yahoo.com/rss/world",
        "backup_url": "https://tw.news.yahoo.com/rss/",
        "lang": "zh-TW", "icon": "🟣", "category": "中文媒體",
    },
    {
        "name": "風傳媒",
        "url": "https://www.storm.mg/feeds/rss",
        "backup_url": None,
        "lang": "zh-TW", "icon": "🌪️", "category": "中文媒體",
    },

    # ════════════════════════════════
    # 🇨🇳 中文媒體（大陸航運專業）
    # ════════════════════════════════
    {
        # 海事服務網 CNSS — 大陸最大海事資訊平台
        "name": "海事服務網",
        "url": "https://www.cnss.com.cn/rss/news.xml",
        "backup_url": "https://www.cnss.com.cn/rss/",
        "lang": "zh-CN", "icon": "⚓", "category": "中文媒體",
    },
    {
        # 航運界網 — 中國航運市場分析、運費、港口動態
        "name": "航運界網",
        "url": "https://www.shippingchina.com/rss.xml",
        "backup_url": "https://www.shippingchina.com/feed/",
        "lang": "zh-CN", "icon": "🚢", "category": "中文媒體",
    },
    {
        # 人民網 - 國際 — 地緣政治、制裁、中東局勢
        "name": "人民網",
        "url": "http://www.people.com.cn/rss/world.xml",
        "backup_url": "https://rsshub.app/people/world",
        "lang": "zh-CN", "icon": "🏮", "category": "中文媒體",
    },
    {
        # 環球時報 — 中東/伊朗/制裁/紅海報導豐富
        "name": "環球時報",
        "url": "https://www.huanqiu.com/rss",
        "backup_url": "https://rsshub.app/huanqiu/world",
        "lang": "zh-CN", "icon": "🌏", "category": "中文媒體",
    },
    {
        # 新浪財經 - 物流 — 運費、船公司、影子船隊財經新聞
        "name": "新浪物流",
        "url": "https://rsshub.app/sina/finance/logistics",
        "backup_url": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2512&num=50&page=1",
        "lang": "zh-CN", "icon": "📈", "category": "中文媒體",
    },
    {
        # 中國水運網 — 內河、港口、航道政策
        "name": "中國水運網",
        "url": "http://www.zgsyb.com/rss.xml",
        "backup_url": "https://rsshub.app/zgsy/news",
        "lang": "zh-CN", "icon": "🛳️", "category": "中文媒體",
    },

    # ════════════════════════════════
    # 🚢 專業航運媒體（英文）
    # ════════════════════════════════
    {
        "name": "TradeWinds",
        "url": "https://rss.app/feeds/tvCHOGHBWmcHkBKM.xml",
        "backup_url": "https://www.tradewindsnews.com/latest",
        "lang": "en", "icon": "🚢", "category": "航運專業",
    },
    {
        "name": "Splash247",
        "url": "https://splash247.com/feed/",
        "backup_url": None,
        "lang": "en", "icon": "⚓", "category": "航運專業",
    },
    {
        "name": "gCaptain",
        "url": "https://gcaptain.com/feed/",
        "backup_url": "https://gcaptain.com/feed/rss/",
        "lang": "en", "icon": "🧭", "category": "航運專業",
    },
    {
        "name": "Maritime Exec",
        "url": "https://maritime-executive.com/magazine/rss",
        "backup_url": "https://maritime-executive.com/rss",
        "lang": "en", "icon": "⛴️", "category": "航運專業",
    },
    {
        "name": "Hellenic Ship",
        "url": "https://www.hellenicshippingnews.com/feed/",
        "backup_url": "https://www.hellenicshippingnews.com/feed/rss/",
        "lang": "en", "icon": "🏛️", "category": "航運專業",
    },
    {
        "name": "Safety4Sea",
        "url": "https://safety4sea.com/feed/",
        "backup_url": "https://safety4sea.com/feed/rss/",
        "lang": "en", "icon": "🛡️", "category": "航運專業",
    },
    {
        "name": "Container News",
        "url": "https://container-news.com/feed/",
        "backup_url": None,
        "lang": "en", "icon": "📦", "category": "航運專業",
    },
    {
        "name": "Freightwaves",
        "url": "https://www.freightwaves.com/news/feed",
        "backup_url": "https://www.freightwaves.com/feed",
        "lang": "en", "icon": "📊", "category": "航運專業",
    },
    {
        "name": "Offshore Energy",
        "url": "https://www.offshore-energy.biz/feed/",
        "backup_url": None,
        "lang": "en", "icon": "⚡", "category": "航運專業",
    },

    # ════════════════════════════════
    # 🌐 國際綜合媒體（英文）
    # ════════════════════════════════
    {
        "name": "Reuters",
        "url": "https://feeds.reuters.com/reuters/worldNews",
        "backup_url": "https://news.yahoo.com/rss/world",
        "lang": "en", "icon": "🌐", "category": "國際媒體",
    },
    {
        "name": "BBC News",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "backup_url": "https://feeds.bbci.co.uk/news/rss.xml",
        "lang": "en", "icon": "🇬🇧", "category": "國際媒體",
    },
    {
        "name": "Al Jazeera",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "backup_url": None,
        "lang": "en", "icon": "🌍", "category": "國際媒體",
    },
    {
        "name": "The Guardian",
        "url": "https://www.theguardian.com/world/rss",
        "backup_url": None,
        "lang": "en", "icon": "🗞️", "category": "國際媒體",
    },
    {
        "name": "AP News",
        "url": "https://rsshub.app/apnews/topics/world-news",
        "backup_url": "https://feeds.apnews.com/rss/apf-topnews",
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
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }

    def __init__(self, keywords: list, sources: list, hours_back: int = 2):
        self.keywords   = keywords
        self.sources    = sources
        self.hours_back = hours_back
        self.seen_urls  = set()

    def _match_keywords(self, text: str) -> list[tuple]:
        if not text:
            return []
        text_lower = text.lower()
        matched, seen_kw = [], set()
        for kw in self.keywords:
            if kw.lower() in text_lower and kw not in seen_kw:
                cat, color = KEYWORD_CATEGORY_MAP.get(kw.lower(), ("其他", "#94a3b8"))
                matched.append((kw, cat, color))
                seen_kw.add(kw)
        return matched

    def _parse_published_time(self, entry) -> datetime | None:
        try:
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                ts = calendar.timegm(entry.published_parsed)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass
        return None

    def _download_rss(self, url: str, need_clean: bool = False):
        try:
            resp = requests.get(
                url, headers=self.HEADERS,
                timeout=15, verify=False, allow_redirects=True,
            )
            resp.raise_for_status()
            if need_clean:
                return feedparser.parse(io.StringIO(clean_xml_content(resp.content)))
            return feedparser.parse(io.BytesIO(resp.content))
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"    ⚠️  連線失敗: {url} → {e}")
        except requests.exceptions.Timeout:
            logger.warning(f"    ⚠️  連線逾時: {url}")
        except requests.exceptions.HTTPError as e:
            logger.warning(f"    ⚠️  HTTP 錯誤: {url} → {e}")
        except Exception as e:
            logger.warning(f"    ⚠️  下載失敗: {url} → {e}")
        return None

    def fetch_from_source(self, source: dict) -> list:
        results    = []
        cutoff     = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back)
        need_clean = source.get("need_clean", False)

        logger.info(f"  📡 [{source.get('category','?')}][{source.get('lang','?')}] {source['name']}")

        feed = self._download_rss(source['url'], need_clean)
        if (feed is None or not feed.entries) and source.get('backup_url'):
            logger.info(f"    🔄 切換備用: {source['backup_url']}")
            feed = self._download_rss(source['backup_url'], need_clean)

        if feed is None or (feed.bozo and not feed.entries):
            logger.warning(f"  ❌ {source['name']} 無法取得資料，跳過")
            return results

        matched_count = 0
        for entry in feed.entries:
            try:
                title   = getattr(entry, 'title',   '') or ''
                summary = getattr(entry, 'summary', '') or ''
                link    = getattr(entry, 'link',    '') or ''

                if link and link in self.seen_urls:
                    continue

                pub_time = self._parse_published_time(entry)
                if pub_time and pub_time < cutoff:
                    continue

                matched = self._match_keywords(f"{title} {summary}")
                if not matched:
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
                logger.warning(f"    ⚠️  解析 entry 失敗: {e}")

        logger.info(
            f"  ✅ {source['name']} | "
            f"共 {len(feed.entries)} 則，命中 {matched_count} 筆"
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
            f"\n📊 抓取結果: "
            f"台灣中文 {len(zh_tw_news)} 筆 | "
            f"大陸中文 {len(zh_cn_news)} 筆 | "
            f"航運專業 {len(shipping_news)} 筆 | "
            f"國際媒體 {len(intl_news)} 筆 | "
            f"總計 {len(all_news)} 筆"
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
            logger.error(
                "❌ Email 環境變數未設定！\n"
                "   請在 GitHub Secrets 新增：\n"
                "   MAIL_USER / MAIL_PASSWORD / TARGET_EMAIL"
            )
        else:
            logger.info(f"✅ Email 設定完成 → {self.target_email}")

    def send(self, news_data: dict, run_time: datetime) -> bool:
        if not self.enabled:
            return False

        total = len(news_data.get('all', []))
        if total == 0:
            logger.info("ℹ️  無相關新聞，跳過發送 Email")
            return False

        try:
            tpe_time = run_time.astimezone(timezone(timedelta(hours=8)))
            subject  = (
                f"🚢 航運情報快遞 | 發現 {total} 則動態 "
                f"({tpe_time.strftime('%m/%d %H:%M')})"
            )
            msg            = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From']    = f"航運監控系統 <{self.mail_user}>"
            msg['To']      = self.target_email
            msg.attach(MIMEText(
                self._generate_html(news_data, run_time), 'html', 'utf-8'
            ))

            logger.info(f"📧 發送 Email 至 {self.target_email}...")
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
        # 關鍵字標籤
        kw_parts = []
        for kw, cat, color in item['matched'][:6]:
            kw_parts.append(
                f'<td bgcolor="{color}" style="padding:3px 8px;border-radius:4px;">'
                f'<font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#ffffff">'
                f'<b>{kw}</b>'
                f'</font>'
                f'</td>'
                f'<td width="4"></td>'
            )
        kw_html = (
            f'<table border="0" cellpadding="0" cellspacing="0">'
            f'<tr>{"".join(kw_parts)}</tr>'
            f'</table>'
        ) if kw_parts else ""

        # 時間轉台北
        pub = item['published']
        if pub != '時間未知':
            try:
                dt  = datetime.strptime(pub, '%Y-%m-%d %H:%M UTC')
                dt  = dt.replace(tzinfo=timezone.utc)
                tpe = dt.astimezone(timezone(timedelta(hours=8)))
                pub = tpe.strftime('%m/%d %H:%M')
            except Exception:
                pass

        safe_title   = (item['title']
                        .replace('&', '&amp;')
                        .replace('<', '&lt;')
                        .replace('>', '&gt;'))
        safe_summary = (item['summary']
                        .replace('&', '&amp;')
                        .replace('<', '&lt;')
                        .replace('>', '&gt;'))

        return f"""
        <table width="100%" border="0" cellpadding="1" cellspacing="0" bgcolor="#e2e8f0">
        <tr><td>
            <table width="100%" border="0" cellpadding="0" cellspacing="0" bgcolor="#ffffff">
            <tr>
                <td width="5" bgcolor="{border_color}">&nbsp;</td>
                <td>
                    <table width="100%" border="0" cellpadding="14" cellspacing="0">
                    <tr><td>

                        <!-- 來源 + 時間 -->
                        <table width="100%" border="0" cellpadding="0" cellspacing="0">
                        <tr>
                            <td align="left">
                                <table border="0" cellpadding="4" cellspacing="0" bgcolor="#f1f5f9">
                                <tr><td>
                                    <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">
                                        <b>{item['source_icon']} {item['source_name']}</b>
                                    </font>
                                </td></tr>
                                </table>
                            </td>
                            <td align="right">
                                <font face="Arial,sans-serif" size="2" color="#94a3b8">
                                    🕐 {pub}
                                </font>
                            </td>
                        </tr>
                        </table>

                        <br>

                        <!-- 標題 -->
                        <a href="{item['link']}" target="_blank" style="text-decoration:none;">
                            <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
                                <b>{safe_title}</b>
                            </font>
                        </a>

                        <br><br>

                        <!-- 摘要 -->
                        <table width="100%" border="0" cellpadding="10" cellspacing="0" bgcolor="#f8fafc">
                        <tr><td>
                            <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#64748b">
                                {safe_summary or '（無摘要）'}
                            </font>
                        </td></tr>
                        </table>

                        <br>

                        <!-- 關鍵字 + 閱讀按鈕 -->
                        <table width="100%" border="0" cellpadding="0" cellspacing="0">
                        <tr>
                            <td align="left" valign="middle">{kw_html}</td>
                            <td align="right" valign="middle">
                                <table border="0" cellpadding="8" cellspacing="0" bgcolor="{border_color}">
                                <tr><td>
                                    <a href="{item['link']}" target="_blank" style="text-decoration:none;">
                                        <font face="Arial,sans-serif" size="2" color="#ffffff">
                                            <b>閱讀原文 &rarr;</b>
                                        </font>
                                    </a>
                                </td></tr>
                                </table>
                            </td>
                        </tr>
                        </table>

                    </td></tr>
                    </table>
                </td>
            </tr>
            </table>
        </td></tr>
        </table>
        <br>
        """

    def _render_section(self, title: str, news_list: list, cfg_key: str) -> str:
        if not news_list:
            return ""

        cfg   = self.SECTION_COLORS.get(cfg_key, {
            'color': '#64748b', 'bg': '#f1f5f9', 'icon': '📄'
        })
        cards = "".join(
            self._render_card(item, cfg['color'])
            for item in news_list
        )

        return f"""
        <table width="100%" border="0" cellpadding="0" cellspacing="0" bgcolor="{cfg['bg']}">
        <tr>
            <td width="5" bgcolor="{cfg['color']}">&nbsp;</td>
            <td>
                <table width="100%" border="0" cellpadding="10" cellspacing="0">
                <tr>
                    <td align="left" valign="middle">
                        <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
                            <b>{cfg['icon']} {title}</b>
                        </font>
                    </td>
                    <td align="right" valign="middle">
                        <table border="0" cellpadding="5" cellspacing="0" bgcolor="{cfg['color']}">
                        <tr><td>
                            <font face="Arial,sans-serif" size="2" color="#ffffff">
                                <b>{len(news_list)} 篇</b>
                            </font>
                        </td></tr>
                        </table>
                    </td>
                </tr>
                </table>
            </td>
        </tr>
        </table>
        <br>
        {cards}
        <br>
        """

    def _generate_html(self, news_data: dict, run_time: datetime) -> str:
        tpe_str = run_time.astimezone(
            timezone(timedelta(hours=8))
        ).strftime('%Y-%m-%d %H:%M')

        # 來源統計（先排序再 join，避免 f-string 括號 SyntaxError）
        source_stats = {}
        for item in news_data.get('all', []):
            key = f"{item['source_icon']} {item['source_name']}"
            source_stats[key] = source_stats.get(key, 0) + 1

        _sorted_sources = sorted(source_stats.items(), key=lambda x: -x[1])

        def _stat_row(src: str, cnt: int) -> str:
            return (
                f'<tr>'
                f'<td align="left" bgcolor="#ffffff" style="padding:8px 12px;">'
                f'<font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">'
                f'{src}</font></td>'
                f'<td align="right" bgcolor="#ffffff" style="padding:8px 12px;">'
                f'<font face="Arial,sans-serif" size="2" color="#3b82f6">'
                f'<b>{cnt} 則</b></font></td>'
                f'</tr>'
            )

        stat_rows = (
            "".join(_stat_row(s, c) for s, c in _sorted_sources)
            or (
                '<tr><td colspan="2" style="padding:10px 12px;">'
                '<font face="Arial,sans-serif" size="2" color="#94a3b8">無資料</font>'
                '</td></tr>'
            )
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

    <!-- ══ HEADER ══ -->
    <tr>
        <td bgcolor="#0f172a" align="center">
            <table width="100%" border="0" cellpadding="30" cellspacing="0">
            <tr><td align="center">
                <font size="7" color="#ffffff">🚢</font><br><br>
                <font face="Microsoft JhengHei,Arial,sans-serif" size="5" color="#f8fafc">
                    <b>航運安全暨地緣政治新聞快報</b>
                </font><br><br>
                <font face="Arial,sans-serif" size="2" color="#94a3b8">
                    GitHub Actions 自動監控 v4.3
                    &nbsp;|&nbsp;
                    {tpe_str} (台北時間)
                </font><br><br>
                <table border="0" cellpadding="6" cellspacing="0" bgcolor="#1e293b">
                <tr><td>
                    <font face="Arial,sans-serif" size="2" color="#94a3b8">
                        監控來源 {len(RSS_SOURCES)} 個
                        &nbsp;|&nbsp;
                        關鍵字 {len(ALL_KEYWORDS)} 個（繁簡雙語）
                    </font>
                </td></tr>
                </table>
            </td></tr>
            </table>
        </td>
    </tr>

    <!-- ══ 統計列 ══ -->
    <tr>
        <td bgcolor="#ffffff">
            <table width="100%" border="1" bordercolor="#e2e8f0"
                   cellpadding="15" cellspacing="0">
            <tr>
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
                    <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#94a3b8">
                        🇹🇼 台灣
                    </font>
                </td>
                <td align="center" width="20%">
                    <font face="Arial,sans-serif" size="6" color="#ef4444">
                        <b>{len(news_data['zh_cn'])}</b>
                    </font><br>
                    <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#94a3b8">
                        🇨🇳 大陸
                    </font>
                </td>
                <td align="center" width="20%">
                    <font face="Arial,sans-serif" size="6" color="#3b82f6">
                        <b>{len(news_data['shipping'])}</b>
                    </font><br>
                    <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#94a3b8">
                        🚢 專業
                    </font>
                </td>
                <td align="center" width="20%">
                    <font face="Arial,sans-serif" size="6" color="#f97316">
                        <b>{len(news_data['intl'])}</b>
                    </font><br>
                    <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#94a3b8">
                        🌐 國際
                    </font>
                </td>
            </tr>
            </table>
        </td>
    </tr>

    <!-- ══ 關鍵字圖例 ══ -->
    <tr>
        <td bgcolor="#fffbeb">
            <table width="100%" border="0" cellpadding="10" cellspacing="0">
            <tr><td align="left" valign="middle">
                <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#92400e">
                    <b>🏷️ 關鍵字分類：</b>
                </font>
                &nbsp;
                <table border="0" cellpadding="0" cellspacing="0"
                       style="display:inline-table;">
                <tr>
                    <td bgcolor="#3b82f6" style="padding:3px 10px;">
                        <font face="Microsoft JhengHei,Arial,sans-serif"
                              size="2" color="#ffffff">航運動態</font>
                    </td>
                    <td width="6"></td>
                    <td bgcolor="#f97316" style="padding:3px 10px;">
                        <font face="Microsoft JhengHei,Arial,sans-serif"
                              size="2" color="#ffffff">航行安全</font>
                    </td>
                    <td width="6"></td>
                    <td bgcolor="#ef4444" style="padding:3px 10px;">
                        <font face="Microsoft JhengHei,Arial,sans-serif"
                              size="2" color="#ffffff">地區</font>
                    </td>
                    <td width="12"></td>
                    <td>
                        <font face="Microsoft JhengHei,Arial,sans-serif"
                              size="1" color="#92400e">
                            ✦ 繁簡雙語比對
                        </font>
                    </td>
                </tr>
                </table>
            </td></tr>
            </table>
        </td>
    </tr>

    <!-- ══ 主內容（中文優先）══ -->
    <tr>
        <td bgcolor="#f8fafc">
            <table width="100%" border="0" cellpadding="20" cellspacing="0">
            <tr><td>
                {self._render_section('中文媒體（台灣）', news_data['zh_tw'],    '中文媒體台灣')}
                {self._render_section('中文媒體（大陸）', news_data['zh_cn'],    '中文媒體大陸')}
                {self._render_section('航運專業媒體',     news_data['shipping'], '航運專業')}
                {self._render_section('國際媒體',         news_data['intl'],     '國際媒體')}
            </td></tr>
            </table>
        </td>
    </tr>

    <!-- ══ 來源統計 ══ -->
    <tr>
        <td bgcolor="#ffffff">
            <table width="100%" border="0" cellpadding="20" cellspacing="0">
            <tr><td>
                <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#475569">
                    <b>📊 本次來源分布</b>
                </font>
                <br><br>
                <table width="100%" border="1" bordercolor="#e2e8f0"
                       cellpadding="0" cellspacing="0">
                    {stat_rows}
                </table>
            </td></tr>
            </table>
        </td>
    </tr>

    <!-- ══ FOOTER ══ -->
    <tr>
        <td bgcolor="#1e293b" align="center">
            <table width="100%" border="0" cellpadding="20" cellspacing="0">
            <tr><td align="center">
                <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
                    🤖 此為 GitHub Actions 自動發送郵件，請勿直接回覆
                </font><br><br>
                <font face="Arial,sans-serif" size="2" color="#475569">
                    航運安全監控系統 v4.3
                    &nbsp;·&nbsp;
                    Powered by Python &amp; GitHub Actions
                </font>
            </td></tr>
            </table>
        </td>
    </tr>

</table>

</td></tr>
</table>

</body>
</html>"""


# ==================== 主程式 ====================
if __name__ == "__main__":
    logger.info("\n" + "=" * 60)
    logger.info("🚢 航運安全暨地緣政治新聞監控系統 v4.3")
    logger.info("   繁簡雙語關鍵字 + 大陸航運來源整合版")
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
