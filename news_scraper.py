#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新聞自動爬取與 Email 發送模組
GitHub Actions 版本 - 移除本地排程，單次執行即可
版本: 2.0 - GitHub Actions 適配
"""

import os
import io
import re
import ssl
import smtplib
import logging
import traceback
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==================== 全域初始化 ====================
# GitHub Actions 環境不需要 load_dotenv()，直接讀環境變數
# 本地測試時若有 .env 則載入
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ssl._create_default_https_context = ssl._create_unverified_context

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler()   # GitHub Actions 只需要 stdout
    ]
)
logger = logging.getLogger(__name__)


# ==================== 設定區 ====================
NEWS_KEYWORDS = [
    # 英文 - 核心事件
    "Iran", "Iranian", "US-Iran", "Israel", "Middle East",
    "Persian Gulf", "Gulf", "Hormuz", "Red Sea", "Bab el-Mandeb",
    "UKMTO", "Houthi", "Houthis", "Yemen",
    "tanker", "vessel", "ship attack", "naval", "warship",
    "missile", "drone attack", "airstrike", "strike",
    "sanctions", "nuclear", "IRGC",
    # 中文 - 核心事件
    "伊朗", "美伊", "以色列", "中東", "波斯灣", "波灣",
    "荷姆茲", "紅海", "曼德海峽", "葉門",
    "胡塞", "飛彈", "無人機", "空襲", "攻擊",
    "油輪", "船隻", "航運", "海峽封鎖",
    "核武", "制裁", "革命衛隊",
    # 中文 - 戰事相關
    "戰爭", "戰事", "衝突", "轟炸", "爆炸",
    "美軍", "以軍", "伊軍", "基地", "陣亡",
    "史詩怒火", "報復", "反擊",
]

RSS_SOURCES = [
    # ── 英文媒體 ──
    {
        "name": "Reuters - Top News",
        "url": "https://feeds.reuters.com/reuters/topNews",
        "backup_url": "https://www.reutersagency.com/feed/?best-topics=political-general&post_type=best",
        "lang": "en",
        "icon": "🌐"
    },
    {
        "name": "BBC News - World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "backup_url": "https://feeds.bbci.co.uk/news/rss.xml",
        "lang": "en",
        "icon": "🇬🇧"
    },
    {
        "name": "CNN - World",
        "url": "http://rss.cnn.com/rss/edition_world.rss",
        "backup_url": "http://rss.cnn.com/rss/edition.rss",
        "lang": "en",
        "icon": "📺"
    },
    {
        "name": "Al Jazeera - All",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "backup_url": None,
        "lang": "en",
        "icon": "🌍"
    },
    {
        "name": "The Guardian - World",
        "url": "https://www.theguardian.com/world/rss",
        "backup_url": None,
        "lang": "en",
        "icon": "🗞️"
    },
    # ── 中文媒體 ──
    {
        "name": "自由時報 - 國際",
        "url": "https://news.ltn.com.tw/rss/world.xml",
        "backup_url": None,
        "lang": "zh",
        "icon": "🇹🇼"
    },
    {
        "name": "聯合新聞網 - 國際",
        "url": "https://udn.com/rssfeed/news/2/6638?ch=news",
        "backup_url": "https://udn.com/rssfeed/news/2/6638",
        "lang": "zh",
        "icon": "📰"
    },
    {
        "name": "中央社 - 國際",
        "url": "https://www.cna.com.tw/rss/aall.aspx",
        "backup_url": "https://www.cna.com.tw/rss/aopl.aspx",
        "lang": "zh",
        "icon": "🏛️",
        "need_clean": True
    },
    {
        "name": "Yahoo 新聞 - 國際",
        "url": "https://tw.news.yahoo.com/rss/world",
        "backup_url": None,
        "lang": "zh",
        "icon": "🟣"
    },
    {
        "name": "ETtoday - 國際",
        "url": "https://www.ettoday.net/news/rss/news.xml",
        "backup_url": None,
        "lang": "zh",
        "icon": "📲"
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
            "Chrome/120.0.0.0 Safari/537.36"
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

    def _is_keyword_match(self, text: str) -> list:
        if not text:
            return []
        text_lower = text.lower()
        return [kw for kw in self.keywords if kw.lower() in text_lower]

    def _parse_published_time(self, entry) -> datetime | None:
        try:
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                import calendar
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
                cleaned = clean_xml_content(raw)
                return feedparser.parse(io.StringIO(cleaned))
            else:
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

        logger.info(f"  📡 抓取: {source['name']}")

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

                combined_text    = f"{title} {summary}"
                matched_keywords = self._is_keyword_match(combined_text)
                if not matched_keywords:
                    continue

                if link:
                    self.seen_urls.add(link)

                results.append({
                    'source_name':      source['name'],
                    'source_icon':      source['icon'],
                    'source_lang':      source['lang'],
                    'title':            title.strip(),
                    'summary':          re.sub(r'<[^>]+>', '', summary).strip()[:300],
                    'link':             link,
                    'published':        (
                        pub_time.strftime('%Y-%m-%d %H:%M UTC')
                        if pub_time else '時間未知'
                    ),
                    'matched_keywords': matched_keywords,
                })
                matched_count += 1

            except Exception as e:
                logger.warning(f"    ⚠️  解析 entry 失敗: {e}")
                continue

        logger.info(
            f"  ✅ {source['name']} 共 {len(feed.entries)} 則，"
            f"命中 {matched_count} 筆"
        )
        return results

    def fetch_all(self) -> dict:
        all_news = []
        for source in self.sources:
            all_news.extend(self.fetch_from_source(source))

        en_news = [n for n in all_news if n['source_lang'] == 'en']
        zh_news = [n for n in all_news if n['source_lang'] == 'zh']

        def sort_key(x):
            return x['published'] if x['published'] != '時間未知' else '0000'

        all_news.sort(key=sort_key, reverse=True)
        en_news.sort(key=sort_key,  reverse=True)
        zh_news.sort(key=sort_key,  reverse=True)

        logger.info(
            f"\n📊 抓取結果: 英文 {len(en_news)} 筆 | "
            f"中文 {len(zh_news)} 筆 | 總計 {len(all_news)} 筆"
        )
        return {'all': all_news, 'english': en_news, 'chinese': zh_news}


# ==================== Email 發送器 ====================
class NewsEmailSender:

    def __init__(self):
        # GitHub Actions 透過 Secrets 注入環境變數
        self.mail_user    = os.environ.get("MAIL_USER",     "")
        self.mail_pass    = os.environ.get("MAIL_PASSWORD", "")
        self.target_email = os.environ.get("TARGET_EMAIL",  "")
        self.smtp_server  = os.environ.get("MAIL_SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port    = int(os.environ.get("MAIL_SMTP_PORT", "587"))

        if not all([self.mail_user, self.mail_pass, self.target_email]):
            logger.error(
                "❌ Email 環境變數未設定！\n"
                "   請在 GitHub Repo → Settings → Secrets and variables → Actions\n"
                "   新增：MAIL_USER / MAIL_PASSWORD / TARGET_EMAIL"
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
                f"⚡ 美伊戰事新聞快報 | 共 {total} 則 | "
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
            logger.error("❌ Gmail 認證失敗，請確認 App Password 是否正確")
        except Exception as e:
            logger.error(f"❌ Email 發送失敗: {e}")
            traceback.print_exc()
        return False

    def _generate_html(self, news_data: dict, run_time: datetime) -> str:
        tpe_time = run_time.astimezone(timezone(timedelta(hours=8)))
        all_news = news_data.get('all',     [])
        en_news  = news_data.get('english', [])
        zh_news  = news_data.get('chinese', [])

        def render_news_cards(news_list: list) -> str:
            if not news_list:
                return '<p style="color:#888;font-style:italic;">本次無相關新聞</p>'
            html = ""
            for idx, item in enumerate(news_list, 1):
                kw_tags = "".join(
                    f'<span style="display:inline-block;background:#e53e3e;'
                    f'color:white;padding:2px 8px;border-radius:12px;'
                    f'font-size:11px;margin:2px;">{kw}</span>'
                    for kw in item['matched_keywords'][:6]
                )
                html += f"""
                <div style="background:#f9f9f9;border-left:4px solid #e53e3e;
                            padding:14px 16px;margin:12px 0;border-radius:6px;">
                    <div style="font-weight:bold;color:#1a202c;
                                font-size:15px;margin-bottom:6px;">
                        {item['source_icon']} {idx}. {item['title']}
                    </div>
                    <div style="color:#555;font-size:13px;
                                line-height:1.7;margin-bottom:8px;">
                        {item['summary'] or '（無摘要）'}
                    </div>
                    <div style="font-size:12px;color:#888;margin-bottom:6px;">
                        📰 {item['source_name']} &nbsp;|&nbsp;
                        📅 {item['published']}
                    </div>
                    <div style="margin-bottom:8px;">{kw_tags}</div>
                    <a href="{item['link']}" target="_blank"
                       style="color:#3182ce;font-size:13px;text-decoration:none;">
                        🔗 閱讀全文 →
                    </a>
                </div>
                """
            return html

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family:'Microsoft JhengHei',Arial,sans-serif;
             background:#f0f2f5;margin:0;padding:20px;">
<div style="max-width:900px;margin:0 auto;background:white;
            border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,0.1);
            overflow:hidden;">

    <div style="background:linear-gradient(135deg,#c53030,#742a2a);
                padding:30px;color:white;text-align:center;">
        <h1 style="margin:0;font-size:24px;">⚡ 美伊戰事新聞快報</h1>
        <p style="margin:8px 0 0;opacity:0.9;font-size:14px;">
            GitHub Actions 自動監控 v2.0 |
            {tpe_time.strftime('%Y-%m-%d %H:%M')} (TPE)
        </p>
    </div>

    <div style="background:#fff5f5;padding:16px 30px;
                border-bottom:1px solid #fed7d7;">
        <table style="width:100%;border-collapse:collapse;">
            <tr>
                <td style="text-align:center;padding:8px;">
                    <div style="font-size:28px;font-weight:bold;
                                color:#c53030;">{len(all_news)}</div>
                    <div style="font-size:12px;color:#666;">總則數</div>
                </td>
                <td style="text-align:center;padding:8px;">
                    <div style="font-size:28px;font-weight:bold;
                                color:#2b6cb0;">{len(en_news)}</div>
                    <div style="font-size:12px;color:#666;">英文媒體</div>
                </td>
                <td style="text-align:center;padding:8px;">
                    <div style="font-size:28px;font-weight:bold;
                                color:#276749;">{len(zh_news)}</div>
                    <div style="font-size:12px;color:#666;">中文媒體</div>
                </td>
            </tr>
        </table>
    </div>

    <div style="padding:24px 30px;">
        <h2 style="color:#2b6cb0;border-left:4px solid #2b6cb0;
                   padding-left:12px;margin-top:0;">
            🌐 外國媒體 ({len(en_news)} 則)
        </h2>
        {render_news_cards(en_news)}

        <h2 style="color:#276749;border-left:4px solid #276749;
                   padding-left:12px;margin-top:30px;">
            📰 中文媒體 ({len(zh_news)} 則)
        </h2>
        {render_news_cards(zh_news)}
    </div>

    <div style="background:#f7fafc;padding:16px 30px;
                border-top:1px solid #e2e8f0;text-align:center;
                color:#888;font-size:12px;">
        <p style="margin:0;">此為 GitHub Actions 自動發送郵件，請勿直接回覆</p>
        <p style="margin:4px 0 0;">新聞監控系統 v2.0</p>
    </div>
</div>
</body>
</html>"""


# ==================== 主程式（單次執行）====================
# GitHub Actions 每次觸發就執行一次，不需要本地排程迴圈
if __name__ == "__main__":
    logger.info("\n" + "="*60)
    logger.info("⚡ 美伊戰事新聞監控系統 v2.0 (GitHub Actions)")
    logger.info("="*60)

    run_time   = datetime.now(tz=timezone.utc)
    hours_back = int(os.environ.get("NEWS_HOURS_BACK", "2"))

    scraper = NewsRssScraper(
        keywords   = NEWS_KEYWORDS,
        sources    = RSS_SOURCES,
        hours_back = hours_back,
    )
    sender = NewsEmailSender()

    try:
        news_data = scraper.fetch_all()
        result    = sender.send(news_data, run_time)

        # GitHub Actions 用 exit code 判斷成敗
        # 0 = 成功（含無新聞），1 = 程式錯誤
        logger.info("✅ 執行完畢")

    except Exception as e:
        logger.error(f"❌ 執行失敗: {e}")
        traceback.print_exc()
        exit(1)
