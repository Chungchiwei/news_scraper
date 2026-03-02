#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
航運安全暨地緣政治新聞監控系統
GitHub Actions 版本 - 單次執行
版本: 3.0 - 新增專業航運媒體 + 關鍵字分類權重 + 來源標籤
"""

import os
import io
import re
import ssl
import smtplib
import logging
import traceback
import calendar
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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


# ==================== 關鍵字設定（分類管理）====================
#
# 設計邏輯：
#   SHIPPING_KEYWORDS   → 純航運操作類（港口/船型/運費/航線）
#   SECURITY_KEYWORDS   → 海上安全類（攻擊/海盜/護航）
#   GEOPOLITICAL_KEYWORDS → 地緣政治類（戰事/制裁/封鎖）
#
# 命中任一分類即納入，Email 報告會顯示命中的分類標籤
# ─────────────────────────────────────────────────────────────

SHIPPING_KEYWORDS = [
    # ── 船型 ──
    "container ship", "containership", "bulk carrier", "tanker",
    "VLCC", "ULCC", "LNG carrier", "LPG carrier", "car carrier",
    "RORO", "feeder vessel", "mega vessel",
    "貨櫃船", "散裝船", "油輪", "液化天然氣船", "滾裝船",
    # ── 港口 / 航線 ──
    "port congestion", "port closure", "terminal", "berth",
    "Suez Canal", "Panama Canal", "Strait of Malacca",
    "Singapore", "Rotterdam", "Shanghai port", "Kaohsiung",
    "蘇伊士運河", "巴拿馬運河", "麻六甲海峽", "高雄港", "基隆港",
    "上海港", "寧波港", "釜山港",
    # ── 運費 / 市場 ──
    "freight rate", "shipping rate", "bunker", "fuel surcharge",
    "BAF", "GRI", "PSS", "congestion surcharge",
    "SCFI", "BDI", "Baltic Dry Index",
    "運費", "燃油附加費", "旺季附加費", "港口擁塞費",
    # ── 航運公司 ──
    "Maersk", "MSC", "CMA CGM", "COSCO", "Evergreen",
    "Hapag-Lloyd", "ONE", "Yang Ming", "HMM", "Wan Hai",
    "長榮", "陽明", "萬海", "中遠海運",
    # ── 操作 ──
    "blank sailing", "vessel delay", "schedule change",
    "omit port", "transshipment", "rerouting",
    "跳港", "停航", "改港", "繞航", "延誤",
]

SECURITY_KEYWORDS = [
    # ── 海上安全事件 ──
    "UKMTO", "IMB", "piracy", "hijack", "armed robbery",
    "vessel attack", "ship attack", "tanker attack",
    "mine", "naval mine", "torpedo",
    "distress signal", "mayday", "crew kidnap",
    "海盜", "劫船", "武裝搶劫", "水雷", "船員被劫",
    # ── 高風險海域 ──
    "Red Sea", "Gulf of Aden", "Bab el-Mandeb",
    "Persian Gulf", "Gulf of Oman", "Strait of Hormuz",
    "Arabian Sea", "Indian Ocean",
    "紅海", "亞丁灣", "曼德海峽", "波斯灣", "荷姆茲海峽",
    "阿拉伯海",
    # ── 武裝組織 ──
    "Houthi", "Houthis", "IRGC", "militia",
    "胡塞", "革命衛隊", "民兵",
    # ── 護航 / 安全措施 ──
    "naval escort", "convoy", "warship", "destroyer",
    "Operation Prosperity Guardian", "CTF-151",
    "護航", "艦隊", "驅逐艦",
    # ── 保險 ──
    "war risk", "war risk insurance", "Lloyd's",
    "戰爭險", "航運保險",
]

GEOPOLITICAL_KEYWORDS = [
    # ── 美伊 / 中東 ──
    "Iran", "Iranian", "US-Iran", "Israel", "Middle East",
    "Yemen", "Syria", "Lebanon", "Hezbollah",
    "伊朗", "美伊", "以色列", "中東", "葉門", "黎巴嫩", "真主黨",
    # ── 俄烏 / 黑海 ──
    "Russia", "Ukraine", "Black Sea", "grain corridor",
    "俄羅斯", "烏克蘭", "黑海", "糧食走廊",
    # ── 台海 / 南海 ──
    "Taiwan Strait", "South China Sea", "China blockade",
    "台灣海峽", "南海", "封鎖", "軍演",
    # ── 制裁 / 禁運 ──
    "sanctions", "embargo", "export control", "blacklist",
    "OFAC", "shadow fleet", "dark fleet",
    "制裁", "禁運", "出口管制", "黑名單", "影子船隊",
    # ── 戰事 ──
    "airstrike", "missile attack", "drone attack", "naval strike",
    "空襲", "飛彈攻擊", "無人機攻擊", "海上打擊",
]

# 合併所有關鍵字供爬取器使用
ALL_KEYWORDS = SHIPPING_KEYWORDS + SECURITY_KEYWORDS + GEOPOLITICAL_KEYWORDS

# 關鍵字分類對照（用於 Email 標籤顯示）
KEYWORD_CATEGORY_MAP = {
    **{kw.lower(): ("航運動態", "#2b6cb0") for kw in SHIPPING_KEYWORDS},
    **{kw.lower(): ("海上安全", "#c05621") for kw in SECURITY_KEYWORDS},
    **{kw.lower(): ("地緣政治", "#c53030") for kw in GEOPOLITICAL_KEYWORDS},
}


# ==================== RSS 來源設定 ====================
#
# 新增專業航運媒體：
#   TradeWinds / Lloyd's List / Splash247 / gCaptain
#   Maritime Executive / Hellenic Shipping News
# ─────────────────────────────────────────────────────

RSS_SOURCES = [

    # ════════════════════════════════
    # 🚢 專業航運媒體（英文）
    # ════════════════════════════════
    {
        "name":       "TradeWinds",
        "url":        "https://www.tradewindsnews.com/rss",
        "backup_url": None,
        "lang":       "en",
        "icon":       "🚢",
        "category":   "航運專業",
    },
    {
        "name":       "Splash247",
        "url":        "https://splash247.com/feed/",
        "backup_url": None,
        "lang":       "en",
        "icon":       "⚓",
        "category":   "航運專業",
    },
    {
        "name":       "gCaptain",
        "url":        "https://gcaptain.com/feed/",
        "backup_url": None,
        "lang":       "en",
        "icon":       "🧭",
        "category":   "航運專業",
    },
    {
        "name":       "The Maritime Executive",
        "url":        "https://maritime-executive.com/rss/articles",
        "backup_url": "https://maritime-executive.com/feed",
        "lang":       "en",
        "icon":       "⛴️",
        "category":   "航運專業",
    },
    {
        "name":       "Hellenic Shipping News",
        "url":        "https://www.hellenicshippingnews.com/feed/",
        "backup_url": None,
        "lang":       "en",
        "icon":       "🏛️",
        "category":   "航運專業",
    },
    {
        "name":       "Lloyd's List Intelligence",
        "url":        "https://lloydslist.maritimeintelligence.informa.com/rss",
        "backup_url": None,
        "lang":       "en",
        "icon":       "📋",
        "category":   "航運專業",
    },
    {
        "name":       "Container News",
        "url":        "https://container-news.com/feed/",
        "backup_url": None,
        "lang":       "en",
        "icon":       "📦",
        "category":   "航運專業",
    },
    {
        "name":       "Freightwaves",
        "url":        "https://www.freightwaves.com/news/feed",
        "backup_url": None,
        "lang":       "en",
        "icon":       "📈",
        "category":   "航運專業",
    },

    # ════════════════════════════════
    # 🌐 國際綜合媒體（英文）
    # ════════════════════════════════
    {
        "name":       "Reuters - World",
        "url":        "https://feeds.reuters.com/reuters/topNews",
        "backup_url": "https://feeds.reuters.com/reuters/worldNews",
        "lang":       "en",
        "icon":       "🌐",
        "category":   "國際媒體",
    },
    {
        "name":       "BBC News - World",
        "url":        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "backup_url": "https://feeds.bbci.co.uk/news/rss.xml",
        "lang":       "en",
        "icon":       "🇬🇧",
        "category":   "國際媒體",
    },
    {
        "name":       "Al Jazeera",
        "url":        "https://www.aljazeera.com/xml/rss/all.xml",
        "backup_url": None,
        "lang":       "en",
        "icon":       "🌍",
        "category":   "國際媒體",
    },
    {
        "name":       "The Guardian - World",
        "url":        "https://www.theguardian.com/world/rss",
        "backup_url": None,
        "lang":       "en",
        "icon":       "🗞️",
        "category":   "國際媒體",
    },

    # ════════════════════════════════
    # 📰 中文媒體
    # ════════════════════════════════
    {
        "name":       "自由時報 - 國際",
        "url":        "https://news.ltn.com.tw/rss/world.xml",
        "backup_url": None,
        "lang":       "zh",
        "icon":       "🇹🇼",
        "category":   "中文媒體",
    },
    {
        "name":       "聯合新聞網 - 國際",
        "url":        "https://udn.com/rssfeed/news/2/6638?ch=news",
        "backup_url": "https://udn.com/rssfeed/news/2/6638",
        "lang":       "zh",
        "icon":       "📰",
        "category":   "中文媒體",
    },
    {
        "name":       "中央社 - 國際",
        "url":        "https://www.cna.com.tw/rss/aall.aspx",
        "backup_url": "https://www.cna.com.tw/rss/aopl.aspx",
        "lang":       "zh",
        "icon":       "🏛️",
        "category":   "中文媒體",
        "need_clean": True,
    },
    {
        "name":       "Yahoo 新聞 - 國際",
        "url":        "https://tw.news.yahoo.com/rss/world",
        "backup_url": None,
        "lang":       "zh",
        "icon":       "🟣",
        "category":   "中文媒體",
    },
    {
        "name":       "工商時報 - 國際財經",
        "url":        "https://ctee.com.tw/feed",
        "backup_url": None,
        "lang":       "zh",
        "icon":       "💹",
        "category":   "中文媒體",
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
        "Accept":          "application/rss+xml, application/xml, text/xml, */*",
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

    # ── 關鍵字比對，回傳 (關鍵字, 分類, 顏色) 清單 ──
    def _match_keywords(self, text: str) -> list[tuple]:
        if not text:
            return []
        text_lower = text.lower()
        matched = []
        seen_kw = set()
        for kw in self.keywords:
            if kw.lower() in text_lower and kw not in seen_kw:
                cat, color = KEYWORD_CATEGORY_MAP.get(
                    kw.lower(), ("其他", "#718096")
                )
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
                url,
                headers=self.HEADERS,
                timeout=15,
                verify=False,
                allow_redirects=True,
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

                results.append({
                    'source_name':     source['name'],
                    'source_icon':     source['icon'],
                    'source_lang':     source['lang'],
                    'source_category': source.get('category', ''),
                    'title':           title.strip(),
                    'summary':         re.sub(r'<[^>]+>', '', summary).strip()[:350],
                    'link':            link,
                    'published':       (
                        pub_time.strftime('%Y-%m-%d %H:%M UTC')
                        if pub_time else '時間未知'
                    ),
                    'matched':         matched,   # [(kw, cat, color), ...]
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

        # 依來源分類
        shipping_news = [n for n in all_news if n['source_category'] == '航運專業']
        intl_news     = [n for n in all_news if n['source_category'] == '國際媒體']
        zh_news       = [n for n in all_news if n['source_category'] == '中文媒體']

        def sort_key(x):
            return x['published'] if x['published'] != '時間未知' else '0000'

        for lst in [all_news, shipping_news, intl_news, zh_news]:
            lst.sort(key=sort_key, reverse=True)

        logger.info(
            f"\n📊 抓取結果: "
            f"航運專業 {len(shipping_news)} 筆 | "
            f"國際媒體 {len(intl_news)} 筆 | "
            f"中文媒體 {len(zh_news)} 筆 | "
            f"總計 {len(all_news)} 筆"
        )
        return {
            'all':      all_news,
            'shipping': shipping_news,
            'intl':     intl_news,
            'chinese':  zh_news,
        }


# ==================== Email 發送器 ====================
class NewsEmailSender:

    def __init__(self):
        self.mail_user    = os.environ.get("MAIL_USER",          "")
        self.mail_pass    = os.environ.get("MAIL_PASSWORD",      "")
        self.target_email = os.environ.get("TARGET_EMAIL",       "")
        self.smtp_server  = os.environ.get("MAIL_SMTP_SERVER",   "smtp.gmail.com")
        self.smtp_port    = int(os.environ.get("MAIL_SMTP_PORT", "587"))

        if not all([self.mail_user, self.mail_pass, self.target_email]):
            logger.error(
                "❌ Email 環境變數未設定！\n"
                "   請在 GitHub Secrets 新增：\n"
                "   MAIL_USER / MAIL_PASSWORD / TARGET_EMAIL"
            )
            self.enabled = False
        else:
            self.enabled = True
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
                f"🚢 航運安全快報 | 共 {total} 則 | "
                f"{tpe_time.strftime('%Y-%m-%d %H:%M')} (TPE)"
            )

            msg            = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From']    = self.mail_user
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

    # ── 新聞卡片 HTML ──
    @staticmethod
    def _render_cards(news_list: list) -> str:
        if not news_list:
            return '<p style="color:#888;font-style:italic;padding:12px 0;">本次無相關新聞</p>'

        html = ""
        for idx, item in enumerate(news_list, 1):

            # 關鍵字標籤（依分類顯示不同顏色）
            kw_tags = ""
            seen_cats = set()
            for kw, cat, color in item['matched'][:8]:
                kw_tags += (
                    f'<span style="display:inline-block;background:{color};'
                    f'color:white;padding:2px 8px;border-radius:12px;'
                    f'font-size:11px;margin:2px 2px 2px 0;">{kw}</span>'
                )
                seen_cats.add((cat, color))

            # 分類徽章
            cat_badges = "".join(
                f'<span style="display:inline-block;border:1px solid {c};'
                f'color:{c};padding:1px 7px;border-radius:10px;'
                f'font-size:11px;margin-right:4px;">{cat}</span>'
                for cat, c in seen_cats
            )

            # 來源標籤
            source_badge = (
                f'<span style="background:#edf2f7;color:#4a5568;'
                f'padding:2px 8px;border-radius:10px;font-size:11px;">'
                f'{item["source_icon"]} {item["source_name"]}</span>'
            )

            html += f"""
            <div style="background:#ffffff;border:1px solid #e2e8f0;
                        border-left:4px solid #2b6cb0;
                        padding:16px 18px;margin:10px 0;border-radius:8px;
                        box-shadow:0 1px 4px rgba(0,0,0,0.05);">
                <div style="display:flex;align-items:flex-start;
                            justify-content:space-between;margin-bottom:6px;">
                    <div style="font-weight:bold;color:#1a202c;
                                font-size:15px;line-height:1.4;flex:1;">
                        {idx}. {item['title']}
                    </div>
                </div>
                <div style="margin-bottom:8px;">
                    {source_badge}
                    &nbsp;
                    <span style="color:#888;font-size:11px;">
                        📅 {item['published']}
                    </span>
                    &nbsp;
                    {cat_badges}
                </div>
                <div style="color:#4a5568;font-size:13px;
                            line-height:1.75;margin-bottom:10px;">
                    {item['summary'] or '（無摘要）'}
                </div>
                <div style="margin-bottom:6px;">{kw_tags}</div>
                <a href="{item['link']}" target="_blank"
                   style="color:#3182ce;font-size:13px;
                          text-decoration:none;font-weight:500;">
                    🔗 閱讀全文 →
                </a>
            </div>
            """
        return html

    def _generate_html(self, news_data: dict, run_time: datetime) -> str:
        tpe_time      = run_time.astimezone(timezone(timedelta(hours=8)))
        all_news      = news_data.get('all',      [])
        shipping_news = news_data.get('shipping', [])
        intl_news     = news_data.get('intl',     [])
        zh_news       = news_data.get('chinese',  [])

        # ── 來源統計表格 ──
        source_stats = {}
        for item in all_news:
            key = f"{item['source_icon']} {item['source_name']}"
            source_stats[key] = source_stats.get(key, 0) + 1

        source_rows = "".join(
            f'<tr>'
            f'<td style="padding:5px 12px;font-size:13px;">{src}</td>'
            f'<td style="padding:5px 12px;font-size:13px;text-align:center;">'
            f'<strong>{cnt}</strong></td>'
            f'</tr>'
            for src, cnt in sorted(source_stats.items(),
                                   key=lambda x: -x[1])
        )

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1.0">
    <title>航運安全快報</title>
</head>
<body style="font-family:'Microsoft JhengHei',Arial,sans-serif;
             background:#edf2f7;margin:0;padding:20px;">
<div style="max-width:960px;margin:0 auto;">

    <!-- ══ Header ══ -->
    <div style="background:linear-gradient(135deg,#1a365d,#2b6cb0);
                padding:32px;color:white;text-align:center;
                border-radius:12px 12px 0 0;">
        <div style="font-size:36px;margin-bottom:8px;">🚢</div>
        <h1 style="margin:0;font-size:22px;letter-spacing:1px;">
            航運安全暨地緣政治新聞快報
        </h1>
        <p style="margin:8px 0 0;opacity:0.85;font-size:13px;">
            GitHub Actions 自動監控 v3.0 &nbsp;|&nbsp;
            {tpe_time.strftime('%Y-%m-%d %H:%M')} (TPE)
        </p>
    </div>

    <!-- ══ 統計列 ══ -->
    <div style="background:white;padding:20px 30px;
                border-bottom:2px solid #bee3f8;">
        <table style="width:100%;border-collapse:collapse;">
            <tr>
                <td style="text-align:center;padding:10px;">
                    <div style="font-size:32px;font-weight:bold;
                                color:#1a365d;">{len(all_news)}</div>
                    <div style="font-size:12px;color:#718096;
                                margin-top:2px;">總則數</div>
                </td>
                <td style="text-align:center;padding:10px;
                           border-left:1px solid #e2e8f0;">
                    <div style="font-size:32px;font-weight:bold;
                                color:#2b6cb0;">{len(shipping_news)}</div>
                    <div style="font-size:12px;color:#718096;
                                margin-top:2px;">🚢 航運專業</div>
                </td>
                <td style="text-align:center;padding:10px;
                           border-left:1px solid #e2e8f0;">
                    <div style="font-size:32px;font-weight:bold;
                                color:#c05621;">{len(intl_news)}</div>
                    <div style="font-size:12px;color:#718096;
                                margin-top:2px;">🌐 國際媒體</div>
                </td>
                <td style="text-align:center;padding:10px;
                           border-left:1px solid #e2e8f0;">
                    <div style="font-size:32px;font-weight:bold;
                                color:#276749;">{len(zh_news)}</div>
                    <div style="font-size:12px;color:#718096;
                                margin-top:2px;">📰 中文媒體</div>
                </td>
            </tr>
        </table>
    </div>

    <!-- ══ 來源統計 ══ -->
    <div style="background:#f7fafc;padding:16px 30px;
                border-bottom:1px solid #e2e8f0;">
        <div style="font-size:13px;font-weight:bold;
                    color:#4a5568;margin-bottom:8px;">
            📊 本次來源分布
        </div>
        <table style="border-collapse:collapse;">
            {source_rows if source_rows else
             '<tr><td style="color:#888;font-size:13px;">無資料</td></tr>'}
        </table>
    </div>

    <!-- ══ 關鍵字圖例 ══ -->
    <div style="background:#fffaf0;padding:12px 30px;
                border-bottom:1px solid #fbd38d;font-size:12px;">
        <strong style="color:#744210;">關鍵字分類：</strong>
        <span style="background:#2b6cb0;color:white;padding:2px 8px;
                     border-radius:10px;margin:0 4px;">航運動態</span>
        <span style="background:#c05621;color:white;padding:2px 8px;
                     border-radius:10px;margin:0 4px;">海上安全</span>
        <span style="background:#c53030;color:white;padding:2px 8px;
                     border-radius:10px;margin:0 4px;">地緣政治</span>
    </div>

    <!-- ══ 航運專業新聞 ══ -->
    <div style="background:white;padding:24px 30px;
                border-bottom:2px solid #bee3f8;">
        <h2 style="color:#1a365d;margin:0 0 16px;font-size:17px;
                   border-left:4px solid #2b6cb0;padding-left:12px;">
            🚢 航運專業媒體 &nbsp;
            <span style="font-size:14px;font-weight:normal;
                         color:#718096;">({len(shipping_news)} 則)</span>
        </h2>
        {self._render_cards(shipping_news)}
    </div>

    <!-- ══ 國際媒體 ══ -->
    <div style="background:white;padding:24px 30px;
                border-bottom:2px solid #fbd38d;">
        <h2 style="color:#7b341e;margin:0 0 16px;font-size:17px;
                   border-left:4px solid #c05621;padding-left:12px;">
            🌐 國際媒體 &nbsp;
            <span style="font-size:14px;font-weight:normal;
                         color:#718096;">({len(intl_news)} 則)</span>
        </h2>
        {self._render_cards(intl_news)}
    </div>

    <!-- ══ 中文媒體 ══ -->
    <div style="background:white;padding:24px 30px;">
        <h2 style="color:#276749;margin:0 0 16px;font-size:17px;
                   border-left:4px solid #38a169;padding-left:12px;">
            📰 中文媒體 &nbsp;
            <span style="font-size:14px;font-weight:normal;
                         color:#718096;">({len(zh_news)} 則)</span>
        </h2>
        {self._render_cards(zh_news)}
    </div>

    <!-- ══ Footer ══ -->
    <div style="background:#2d3748;padding:20px 30px;
                border-radius:0 0 12px 12px;text-align:center;
                color:#a0aec0;font-size:12px;">
        <p style="margin:0 0 4px;">
            🤖 此為 GitHub Actions 自動發送郵件，請勿直接回覆
        </p>
        <p style="margin:0;">
            航運安全監控系統 v3.0 &nbsp;|&nbsp;
            監控來源：{len(RSS_SOURCES)} 個 &nbsp;|&nbsp;
            關鍵字：{len(ALL_KEYWORDS)} 個
        </p>
    </div>

</div>
</body>
</html>"""


# ==================== 主程式 ====================
if __name__ == "__main__":
    logger.info("\n" + "=" * 60)
    logger.info("🚢 航運安全暨地緣政治新聞監控系統 v3.0")
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
