#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
航運安全暨地緣政治新聞監控系統
GitHub Actions 版本 - 單次執行
版本: 4.1 - 大陸航運來源 + 中文優先 + Table 相容 Email UI
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

# ── 壓制 InsecureRequestWarning ──
from urllib3.exceptions import InsecureRequestWarning
warnings.filterwarnings('ignore', category=InsecureRequestWarning)

# ==================== 全域初始化 ====================
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


# ==================== 關鍵字設定（聚焦：船舶安全 + 霍爾木茲）====================
SHIPPING_KEYWORDS = [
    # ── 船型與貨物 ──
    "tanker", "VLCC", "LNG carrier", "LPG carrier",
    "container ship", "containership", "bulk carrier",
    "cargo ship", "oil tanker", "merchant vessel",
    "油輪", "貨櫃船", "散裝船", "液化天然氣船", "商船", "化學品船",

    # ── 航行狀態與市場 ──
    "vessel delay", "rerouting", "diversion", "Cape of Good Hope",
    "port closure", "channel closure", "freight rate",
    "繞航", "改港", "停航", "好望角", "航道封閉", "塞港", "運價",
]

SECURITY_KEYWORDS = [
    # ── 海上具體安全事件 ──
    "UKMTO", "IMB", "maritime security",
    "piracy", "ship hijack", "armed robbery at sea", "vessel boarding",
    "vessel attack", "ship attack", "tanker attack", "merchant ship struck",
    "sea mine", "limpet mine", "crew kidnapped", "Red Sea attack",
    "海盜", "劫船", "武裝登船", "水雷", "商船遇襲", "貨輪被飛彈", "船員被劫", "紅海危機",

    # ── 關鍵航道與高風險海域 ──
    "Strait of Hormuz", "Hormuz", "Suez Canal", "Panama Canal",
    "Red Sea", "Gulf of Aden", "Bab el-Mandeb",
    "Persian Gulf", "Gulf of Oman", "Black Sea shipping",
    "霍爾木茲海峽", "荷姆茲海峽", "蘇伊士運河", "巴拿馬運河",
    "波斯灣", "阿曼灣", "紅海", "亞丁灣", "曼德海峽", "黑海航運",

    # ── 武裝組織 ──
    "Houthi", "Houthis", "IRGC", "Iranian Revolutionary Guard",
    "胡塞", "革命衛隊", "伊斯蘭革命衛隊",

    # ── 護航與保險 ──
    "naval escort", "Operation Prosperity Guardian", "CTF-151",
    "war risk insurance", "war risk premium",
    "護航艦隊", "繁榮衛士行動", "戰爭險", "戰爭附加費", "航運保險",
]

GEOPOLITICAL_KEYWORDS = [
    # ── 直接影響航運的地緣政治 ──
    "Iran", "Iranian", "US-Iran", "Iran sanctions", "Iran oil",
    "oil embargo", "shadow fleet", "dark fleet", "shipping sanctions",
    "strait closure", "naval blockade", "maritime patrol",
    "伊朗", "美伊", "伊朗制裁", "伊朗石油",
    "制裁船隊", "石油禁運", "影子船隊", "黑名單船舶", "海峽封鎖", "海上封鎖",
]

# 合併並去重
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


# ==================== RSS 來源設定（v4.1：含大陸來源，中文優先排列）====================
RSS_SOURCES = [

    # ════════════════════════════════
    # 📰 中文媒體（台灣）
    # ════════════════════════════════
    {
        "name": "自由時報 - 國際",
        "url": "https://news.ltn.com.tw/rss/world.xml",
        "backup_url": "https://news.ltn.com.tw/rss/all.xml",
        "lang": "zh-TW", "icon": "🇹🇼", "category": "中文媒體",
    },
    {
        "name": "聯合新聞網 - 國際",
        "url": "https://udn.com/rssfeed/news/2/6638?ch=news",
        "backup_url": "https://udn.com/rssfeed/news/2/6638",
        "lang": "zh-TW", "icon": "📰", "category": "中文媒體",
    },
    {
        "name": "中央社 - 財經國際",
        "url": "https://www.cna.com.tw/rss/fnall.aspx",
        "backup_url": "https://www.cna.com.tw/rss/aie.aspx",
        "lang": "zh-TW", "icon": "🏛️", "category": "中文媒體",
        "need_clean": True,
    },
    {
        "name": "Yahoo 新聞 - 國際",
        "url": "https://tw.news.yahoo.com/rss/world",
        "backup_url": "https://tw.news.yahoo.com/rss/",
        "lang": "zh-TW", "icon": "🟣", "category": "中文媒體",
    },
    {
        "name": "風傳媒 - 國際",
        "url": "https://www.storm.mg/feeds/rss",
        "backup_url": None,
        "lang": "zh-TW", "icon": "🌪️", "category": "中文媒體",
    },

    # ════════════════════════════════
    # 🇨🇳 中文媒體（大陸航運專業）
    # ════════════════════════════════
    {
        # 海事服務網 CNSS — 大陸最大海事資訊平台
        # 涵蓋：船舶、港口、航運政策、安全事件
        "name": "海事服務網 CNSS",
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
        # 人民網 - 國際 — 地緣政治、制裁、中東局勢官方立場
        "name": "人民網 - 國際",
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
        "name": "新浪財經 - 物流",
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
        "name": "The Maritime Executive",
        "url": "https://maritime-executive.com/magazine/rss",
        "backup_url": "https://maritime-executive.com/rss",
        "lang": "en", "icon": "⛴️", "category": "航運專業",
    },
    {
        "name": "Hellenic Shipping News",
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
        "name": "Reuters - World",
        "url": "https://feeds.reuters.com/reuters/worldNews",
        "backup_url": "https://news.yahoo.com/rss/world",
        "lang": "en", "icon": "🌐", "category": "國際媒體",
    },
    {
        "name": "BBC News - World",
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
        "name": "The Guardian - World",
        "url": "https://www.theguardian.com/world/rss",
        "backup_url": None,
        "lang": "en", "icon": "🗞️", "category": "國際媒體",
    },
    {
        "name": "AP News - World",
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
        logger.info(
            f"✅ NewsRssScraper 初始化 | "
            f"關鍵字: {len(keywords)} 個 | "
            f"來源: {len(sources)} 個 | "
            f"時間範圍: 最近 {hours_back} 小時"
        )

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
            raw = resp.content
            if need_clean:
                return feedparser.parse(io.StringIO(clean_xml_content(raw)))
            return feedparser.parse(io.BytesIO(raw))
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

        logger.info(f"  📡 [{source.get('category','?')}] {source['name']}")

        feed = self._download_rss(source['url'], need_clean)
        if (feed is None or not feed.entries) and source.get('backup_url'):
            logger.info(f"    🔄 切換備用 URL: {source['backup_url']}")
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

        def sort_key(x):
            return x['published'] if x['published'] != '時間未知' else '0000'

        all_news.sort(key=sort_key, reverse=True)

        # 分類
        zh_tw_news    = [n for n in all_news if n['source_category'] == '中文媒體' and n['source_lang'] == 'zh-TW']
        zh_cn_news    = [n for n in all_news if n['source_category'] == '中文媒體' and n['source_lang'] == 'zh-CN']
        shipping_news = [n for n in all_news if n['source_category'] == '航運專業']
        intl_news     = [n for n in all_news if n['source_category'] == '國際媒體']

        logger.info(
            f"\n📊 抓取結果: "
            f"中文(台灣) {len(zh_tw_news)} 筆 | "
            f"中文(大陸) {len(zh_cn_news)} 筆 | "
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


# ==================== Email 發送器 ====================
class NewsEmailSender:

    # 分類配色（純色，無 gradient，Email 客戶端相容）
    SECTION_COLORS = {
        '中文媒體台灣': {
            'color': '#10b981', 'bg': '#ecfdf5',
            'border': '#10b981', 'icon': '🇹🇼',
        },
        '中文媒體大陸': {
            'color': '#ef4444', 'bg': '#fef2f2',
            'border': '#ef4444', 'icon': '🇨🇳',
        },
        '航運專業': {
            'color': '#3b82f6', 'bg': '#eff6ff',
            'border': '#3b82f6', 'icon': '🚢',
        },
        '國際媒體': {
            'color': '#f97316', 'bg': '#fff7ed',
            'border': '#f97316', 'icon': '🌐',
        },
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
                f"🚢 航運情報快遞 | 發現 {total} 則重要動態 "
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

    # ── 單則新聞卡片（Table 佈局，Email 客戶端相容）──
    @staticmethod
    def _render_card(item: dict, border_color: str) -> str:
        # 關鍵字標籤
        kw_parts = []
        for kw, cat, color in item['matched'][:6]:
            kw_parts.append(
                f'<span style="display:inline-block;background:{color}18;'
                f'color:{color};padding:3px 10px;border-radius:6px;'
                f'font-size:11px;font-weight:600;margin:2px 4px 2px 0;'
                f'border:1px solid {color}40;">{kw}</span>'
            )
        kw_html = "".join(kw_parts)

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

        # 安全處理標題（避免 & 等符號破壞 HTML）
        safe_title   = item['title'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        safe_summary = item['summary'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        return (
            # 外框
            f'<table width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin:0 0 14px 0;border-collapse:collapse;">'
            f'<tr>'
            # 左側色條
            f'<td width="4" style="background:{border_color};'
            f'border-radius:4px 0 0 4px;">&nbsp;</td>'
            # 主體
            f'<td style="background:#ffffff;border:1px solid #e2e8f0;'
            f'border-left:none;border-radius:0 10px 10px 0;padding:16px 18px;">'

            # 來源 + 時間列
            f'<table width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin-bottom:10px;">'
            f'<tr>'
            f'<td>'
            f'<span style="background:#f1f5f9;color:#475569;padding:3px 10px;'
            f'border-radius:20px;font-size:12px;font-weight:600;">'
            f'{item["source_icon"]} {item["source_name"]}'
            f'</span>'
            f'</td>'
            f'<td align="right" style="color:#94a3b8;font-size:12px;">🕐 {pub}</td>'
            f'</tr>'
            f'</table>'

            # 標題（可點擊）
            f'<a href="{item["link"]}" target="_blank" style="text-decoration:none;">'
            f'<div style="font-size:15px;font-weight:700;color:#0f172a;'
            f'line-height:1.5;margin-bottom:10px;">'
            f'{safe_title}'
            f'</div>'
            f'</a>'

            # 摘要
            f'<div style="font-size:13px;color:#64748b;line-height:1.7;'
            f'padding:10px 12px;background:#f8fafc;border-radius:6px;'
            f'border-left:3px solid #e2e8f0;margin-bottom:12px;">'
            f'{safe_summary or "<em style=color:#94a3b8>（無摘要）</em>"}'
            f'</div>'

            # 關鍵字 + 閱讀按鈕
            f'<table width="100%" cellpadding="0" cellspacing="0">'
            f'<tr>'
            f'<td style="padding-right:12px;">{kw_html}</td>'
            f'<td width="1" style="white-space:nowrap;">'
            f'<a href="{item["link"]}" target="_blank" '
            f'style="display:inline-block;background:{border_color};'
            f'color:#ffffff;padding:7px 16px;border-radius:20px;'
            f'font-size:12px;font-weight:700;text-decoration:none;">'
            f'閱讀原文 →'
            f'</a>'
            f'</td>'
            f'</tr>'
            f'</table>'

            f'</td>'
            f'</tr>'
            f'</table>'
        )

    def _render_section(self, title: str, news_list: list, cfg_key: str) -> str:
        if not news_list:
            return ""

        cfg   = self.SECTION_COLORS.get(cfg_key, {
            'color': '#64748b', 'bg': '#f1f5f9',
            'border': '#64748b', 'icon': '📄',
        })
        cards = "".join(
            self._render_card(item, cfg['border'])
            for item in news_list
        )

        return (
            f'<div style="margin-bottom:32px;">'

            # Section Header
            f'<table width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin-bottom:14px;border-collapse:collapse;">'
            f'<tr>'
            f'<td width="4" style="background:{cfg["border"]};'
            f'border-radius:4px 0 0 4px;">&nbsp;</td>'
            f'<td style="background:{cfg["bg"]};padding:12px 16px;'
            f'border-radius:0 8px 8px 0;">'
            f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td style="font-size:16px;font-weight:700;color:#0f172a;">'
            f'{cfg["icon"]} {title}'
            f'</td>'
            f'<td align="right">'
            f'<span style="background:{cfg["border"]};color:#ffffff;'
            f'padding:3px 12px;border-radius:20px;'
            f'font-size:12px;font-weight:700;">'
            f'{len(news_list)} 篇'
            f'</span>'
            f'</td>'
            f'</tr></table>'
            f'</td>'
            f'</tr>'
            f'</table>'

            f'{cards}'
            f'</div>'
        )

    def _generate_html(self, news_data: dict, run_time: datetime) -> str:
        tpe_time      = run_time.astimezone(timezone(timedelta(hours=8)))
        tpe_str       = tpe_time.strftime('%Y-%m-%d %H:%M')
        all_news      = news_data.get('all',      [])
        zh_tw_news    = news_data.get('zh_tw',    [])
        zh_cn_news    = news_data.get('zh_cn',    [])
        shipping_news = news_data.get('shipping', [])
        intl_news     = news_data.get('intl',     [])

        # 來源統計（先排序再 join，避免 f-string 括號 SyntaxError）
        source_stats = {}
        for item in all_news:
            key = f"{item['source_icon']} {item['source_name']}"
            source_stats[key] = source_stats.get(key, 0) + 1

        _sorted_sources = sorted(source_stats.items(), key=lambda x: -x[1])

        def _stat_row(src: str, cnt: int) -> str:
            return (
                f'<tr style="border-bottom:1px solid #f1f5f9;">'
                f'<td style="padding:7px 14px;font-size:13px;color:#475569;">{src}</td>'
                f'<td style="padding:7px 14px;font-size:13px;text-align:right;'
                f'font-weight:700;color:#3b82f6;">{cnt} 則</td>'
                f'</tr>'
            )

        source_rows = (
            "".join(_stat_row(s, c) for s, c in _sorted_sources)
            or (
                '<tr><td colspan="2" style="padding:12px 14px;'
                'color:#94a3b8;font-size:13px;">無資料</td></tr>'
            )
        )

        return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1.0">
    <title>航運安全快報</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',
             'Microsoft JhengHei',Roboto,Helvetica,Arial,sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0"
       style="background:#f1f5f9;padding:24px 12px;">
<tr><td align="center">
<table width="700" cellpadding="0" cellspacing="0"
       style="max-width:700px;width:100%;">

    <!-- ══ HEADER ══ -->
    <tr>
        <td style="background:#0f172a;border-radius:14px 14px 0 0;
                   padding:32px 36px;text-align:center;">
            <div style="font-size:40px;margin-bottom:10px;">🚢</div>
            <div style="font-size:22px;font-weight:800;color:#f8fafc;
                        letter-spacing:0.5px;margin-bottom:8px;">
                航運安全暨地緣政治新聞快報
            </div>
            <div style="font-size:13px;color:#94a3b8;margin-bottom:14px;">
                GitHub Actions 自動監控 v4.1
                &nbsp;·&nbsp;
                {tpe_str} (台北時間)
            </div>
            <table cellpadding="0" cellspacing="0"
                   style="margin:0 auto;">
                <tr>
                    <td style="background:#1e293b;border:1px solid #334155;
                               border-radius:20px;padding:5px 18px;
                               font-size:12px;color:#94a3b8;">
                        監控來源 {len(RSS_SOURCES)} 個
                        &nbsp;|&nbsp;
                        關鍵字 {len(ALL_KEYWORDS)} 個
                    </td>
                </tr>
            </table>
        </td>
    </tr>

    <!-- ══ 統計列 ══ -->
    <tr>
        <td style="background:#ffffff;border-left:1px solid #e2e8f0;
                   border-right:1px solid #e2e8f0;">
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td style="text-align:center;padding:20px 12px;
                               border-right:1px solid #f1f5f9;">
                        <div style="font-size:34px;font-weight:800;
                                    color:#0f172a;line-height:1;">
                            {len(all_news)}
                        </div>
                        <div style="font-size:11px;color:#94a3b8;
                                    margin-top:5px;letter-spacing:1px;">
                            TOTAL
                        </div>
                    </td>
                    <td style="text-align:center;padding:20px 12px;
                               border-right:1px solid #f1f5f9;">
                        <div style="font-size:34px;font-weight:800;
                                    color:#10b981;line-height:1;">
                            {len(zh_tw_news)}
                        </div>
                        <div style="font-size:11px;color:#94a3b8;
                                    margin-top:5px;">🇹🇼 台灣中文</div>
                    </td>
                    <td style="text-align:center;padding:20px 12px;
                               border-right:1px solid #f1f5f9;">
                        <div style="font-size:34px;font-weight:800;
                                    color:#ef4444;line-height:1;">
                            {len(zh_cn_news)}
                        </div>
                        <div style="font-size:11px;color:#94a3b8;
                                    margin-top:5px;">🇨🇳 大陸中文</div>
                    </td>
                    <td style="text-align:center;padding:20px 12px;
                               border-right:1px solid #f1f5f9;">
                        <div style="font-size:34px;font-weight:800;
                                    color:#3b82f6;line-height:1;">
                            {len(shipping_news)}
                        </div>
                        <div style="font-size:11px;color:#94a3b8;
                                    margin-top:5px;">🚢 航運專業</div>
                    </td>
                    <td style="text-align:center;padding:20px 12px;">
                        <div style="font-size:34px;font-weight:800;
                                    color:#f97316;line-height:1;">
                            {len(intl_news)}
                        </div>
                        <div style="font-size:11px;color:#94a3b8;
                                    margin-top:5px;">🌐 國際媒體</div>
                    </td>
                </tr>
            </table>
        </td>
    </tr>

    <!-- ══ 關鍵字圖例 ══ -->
    <tr>
        <td style="background:#fffbeb;padding:10px 24px;
                   border-left:1px solid #e2e8f0;
                   border-right:1px solid #e2e8f0;
                   border-bottom:2px solid #fcd34d;">
            <span style="font-size:12px;color:#92400e;
                         font-weight:700;margin-right:8px;">
                🏷️ 關鍵字分類：
            </span>
            <span style="background:#3b82f6;color:#ffffff;padding:2px 10px;
                         border-radius:20px;font-size:11px;font-weight:600;
                         margin-right:5px;">航運動態</span>
            <span style="background:#f97316;color:#ffffff;padding:2px 10px;
                         border-radius:20px;font-size:11px;font-weight:600;
                         margin-right:5px;">海上安全</span>
            <span style="background:#ef4444;color:#ffffff;padding:2px 10px;
                         border-radius:20px;font-size:11px;font-weight:600;">
                地緣政治
            </span>
        </td>
    </tr>

    <!-- ══ 主內容（中文優先）══ -->
    <tr>
        <td style="background:#f8fafc;padding:24px 28px;
                   border-left:1px solid #e2e8f0;
                   border-right:1px solid #e2e8f0;">

            {self._render_section('中文媒體（台灣）', zh_tw_news,    '中文媒體台灣')}
            {self._render_section('中文媒體（大陸）', zh_cn_news,    '中文媒體大陸')}
            {self._render_section('航運專業媒體',     shipping_news, '航運專業')}
            {self._render_section('國際媒體',         intl_news,     '國際媒體')}

        </td>
    </tr>

    <!-- ══ 來源統計 ══ -->
    <tr>
        <td style="background:#ffffff;padding:18px 28px;
                   border-left:1px solid #e2e8f0;
                   border-right:1px solid #e2e8f0;
                   border-top:1px solid #f1f5f9;">
            <div style="font-size:13px;font-weight:700;
                        color:#475569;margin-bottom:10px;">
                📊 本次來源分布
            </div>
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="border:1px solid #e2e8f0;border-radius:8px;
                          overflow:hidden;">
                {source_rows}
            </table>
        </td>
    </tr>

    <!-- ══ FOOTER ══ -->
    <tr>
        <td style="background:#1e293b;padding:18px 28px;
                   border-radius:0 0 14px 14px;text-align:center;">
            <div style="color:#64748b;font-size:12px;margin-bottom:4px;">
                🤖 此為 GitHub Actions 自動發送郵件，請勿直接回覆
            </div>
            <div style="color:#475569;font-size:12px;">
                航運安全監控系統 v4.1
                &nbsp;·&nbsp;
                Powered by Python &amp; GitHub Actions
            </div>
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
    logger.info("🚢 航運安全暨地緣政治新聞監控系統 v4.1")
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
