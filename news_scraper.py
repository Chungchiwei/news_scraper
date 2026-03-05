#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
航運安全暨地緣政治新聞監控系統
GitHub Actions 版本 - 單次執行
"""

import os
import io
import re
import ssl
import html as _html_module
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


# ╔══════════════════════════════════════════════════════════════╗
# ║              五大情境分類定義                                 ║
# ╚══════════════════════════════════════════════════════════════╝
INCIDENT_CATEGORIES = {
    "CAT1": {
        "label":    "船舶於波斯灣/荷姆茲海峽週遭被攻擊事件",
        "icon":     "💥",
        "color":    "#dc2626",
        "bg":       "#fef2f2",
        "priority": 1,
    },
    "CAT2": {
        "label":    "海灣國家及美軍基地被攻擊事件",
        "icon":     "🎯",
        "color":    "#b45309",
        "bg":       "#fffbeb",
        "priority": 2,
    },
    "CAT3": {
        "label":    "伊朗已採取水雷封鎖",
        "icon":     "💣",
        "color":    "#7c3aed",
        "bg":       "#f5f3ff",
        "priority": 3,
    },
    "CAT4": {
        "label":    "紅海/曼德海峽胡塞含伊朗攻擊事件",
        "icon":     "🚀",
        "color":    "#0369a1",
        "bg":       "#eff6ff",
        "priority": 4,
    },
    "CAT5": {
        "label":    "航商宣佈採取繞航措施及波斯灣內避難點",
        "icon":     "🔀",
        "color":    "#047857",
        "bg":       "#ecfdf5",
        "priority": 5,
    },
    "OTHER": {
        "label":    "其他航運新聞（非上述五大情境）",
        "icon":     "🚢",
        "color":    "#475569",
        "bg":       "#f8fafc",
        "priority": 6,
    },
}

# ══════════════════════════════════════════════════════════════
# 關鍵字定義
# ══════════════════════════════════════════════════════════════
CAT1_KEYWORDS = [
    "tanker attack Persian Gulf", "vessel attack Persian Gulf",
    "ship attack Persian Gulf", "tanker attack Gulf of Oman",
    "vessel attack Gulf of Oman", "ship attack Gulf of Oman",
    "tanker attack Strait of Hormuz", "vessel attack Strait of Hormuz",
    "ship attack Strait of Hormuz", "tanker attack Hormuz",
    "vessel seized Persian Gulf", "tanker seized Persian Gulf",
    "ship seized Persian Gulf", "vessel seized Gulf of Oman",
    "tanker seized Strait of Hormuz",
    "IRGC vessel seizure", "IRGC tanker seizure",
    "IRGC seized vessel", "IRGC seized tanker",
    "Iranian seizure tanker", "Iranian seizure vessel",
    "Iranian navy seized", "Iranian navy tanker",
    "tanker hijacked Persian Gulf", "vessel hijacked Persian Gulf",
    "merchant vessel attacked Gulf",
    "drone attack tanker Gulf", "missile attack tanker Gulf",
    "explosion tanker Persian Gulf", "explosion vessel Persian Gulf",
    "tanker struck Persian Gulf", "vessel struck Persian Gulf",
    "ship struck Persian Gulf",
    "armed attack tanker Gulf", "armed boarding Persian Gulf",
    "tanker traffic halt", "tanker traffic stopped",
    "vessels struck Gulf", "tanker struck Hormuz",
    "shipping halt Hormuz", "tanker halt Persian Gulf",
    "波斯灣油輪遭攻擊", "波斯灣商船遇襲", "波斯灣貨輪被攻擊",
    "荷姆茲海峽油輪遭攻擊", "荷姆茲海峽商船遇襲",
    "霍爾木茲海峽油輪遭攻擊", "霍爾木茲海峽商船遇襲",
    "阿曼灣油輪遭攻擊", "阿曼灣商船遇襲",
    "波斯灣油輪被扣押", "波斯灣商船被扣押",
    "革命衛隊扣押油輪", "革命衛隊扣押商船",
    "伊朗海軍扣押油輪", "伊朗海軍扣押商船",
    "伊朗扣押船隻", "伊朗扣押油輪",
    "波斯灣無人機攻船", "波斯灣飛彈攻船",
    "波斯灣武裝登船",
    "波斯湾油轮遭攻击", "波斯湾商船遇袭", "波斯湾货轮被攻击",
    "霍尔木兹海峡油轮遭攻击", "霍尔木兹海峡商船遇袭",
    "阿曼湾油轮遭攻击", "阿曼湾商船遇袭",
    "波斯湾油轮被扣押", "波斯湾商船被扣押",
    "革命卫队扣押油轮", "革命卫队扣押商船",
    "伊朗海军扣押油轮", "伊朗海军扣押商船",
    "伊朗巡防艦","魚雷",
    "伊朗扣押船只", "伊朗扣押油轮",
    "波斯湾无人机攻船", "波斯湾导弹攻船",
    # ── 新增：荷莫茲（台灣常見異體字）──
    "荷莫茲海峽油輪", "荷莫茲海峽商船", "荷莫茲海峽封鎖",
    "荷莫茲海峽攻擊", "荷莫茲海峽被攻擊",
    "控制荷莫茲", "封鎖荷莫茲",
]

CAT2_KEYWORDS = [
    "US military base attack Gulf", "US base attack Middle East",
    "US base attacked Iraq", "US base attacked Syria",
    "US base attacked Kuwait", "US base attacked Bahrain",
    "US base attacked Qatar", "US base attacked UAE",
    "US Navy attacked Gulf", "US warship attacked Gulf",
    "Fifth Fleet attacked", "CENTCOM base attack",
    "drone attack US base", "missile attack US base",
    "ballistic missile US base", "cruise missile US base",
    "attack on Saudi Arabia", "attack on UAE",
    "attack on Kuwait", "attack on Bahrain",
    "attack on Qatar", "attack on Oman",
    "Saudi Arabia oil facility attack", "Saudi Aramco attack",
    "Gulf state attacked", "Gulf country attacked",
    "Abu Dhabi attack", "Dubai attack",
    "Riyadh attack", "Manama attack",
    "Al Udeid attack", "Al Dhafra attack",
    "Camp Arifjan attack", "NSA Bahrain attack",
    "美軍基地遭攻擊", "美軍基地被攻擊", "美軍基地受攻擊",
    "美國海軍遭攻擊", "美軍艦艇遭攻擊",
    "第五艦隊遭攻擊", "中央司令部基地遭攻擊",
    "沙烏地阿拉伯遭攻擊", "沙烏地油田遭攻擊",
    "阿拉伯聯合大公國遭攻擊", "科威特遭攻擊",
    "巴林遭攻擊", "卡達遭攻擊", "阿曼遭攻擊",
    "阿布達比遭攻擊", "杜拜遭攻擊",
    "利雅德遭攻擊", "沙烏地阿美遭攻擊",
    "海灣國家遭攻擊", "海灣地區美軍遭攻擊",
    "無人機攻擊美軍基地", "飛彈攻擊美軍基地",
    "彈道飛彈攻擊海灣", "巡弋飛彈攻擊海灣",
    "美军基地遭攻击", "美军基地被攻击", "美军基地受攻击",
    "美国海军遭攻击", "美军舰艇遭攻击",
    "第五舰队遭攻击", "中央司令部基地遭攻击",
    "沙特阿拉伯遭攻击", "沙特油田遭攻击",
    "阿联酋遭攻击", "科威特遭攻击",
    "巴林遭攻击", "卡塔尔遭攻击", "阿曼遭攻击",
    "阿布扎比遭攻击", "迪拜遭攻击",
    "利雅得遭攻击", "沙特阿美遭攻击",
    "海湾国家遭攻击", "海湾地区美军遭攻击",
    "无人机攻击美军基地", "导弹攻击美军基地",
    "弹道导弹攻击海湾", "巡航导弹攻击海湾",
]

CAT3_KEYWORDS = [
    "sea mine Strait of Hormuz", "naval mine Strait of Hormuz",
    "sea mine Persian Gulf", "naval mine Persian Gulf",
    "sea mine Gulf of Oman", "naval mine Gulf of Oman",
    "mine threat Hormuz", "mine threat Persian Gulf",
    "mine threat Gulf of Oman",
    "Iran mine laying", "Iran mine threat",
    "Iran naval mine", "Iran sea mine",
    "Iran mine warfare", "Iran mine blockade",
    "IRGC mine laying", "IRGC mine threat",
    "limpet mine tanker", "limpet mine vessel",
    "limpet mine Gulf", "limpet mine Hormuz",
    "mine explosion tanker", "mine explosion vessel",
    "mine strike tanker", "mine strike vessel",
    "mine detonation ship", "underwater mine tanker",
    "Hormuz minefield", "Persian Gulf minefield",
    "mine clearance Gulf", "mine sweeping Gulf",
    "mine sweeping Hormuz",
    "Strait of Hormuz closure",
    "Persian Gulf blockade",
    "mine the strait", "mining the strait",
    "mining Hormuz", "mining Persian Gulf",
    "Iran mining campaign", "Iranian mining",
    "submarine minelaying", "mine laying submarine",
    "Hormuz oil flow", "Hormuz oil supply",
    "tanker traffic Hormuz",
    "oil flow disruption Hormuz",
    # ── 新增：伊朗封鎖荷姆茲（台灣媒體常用句型）──
    "伊朗封鎖荷莫茲", "伊朗封鎖霍爾木茲",
    "伊朗控制荷姆茲", "伊朗控制荷莫茲", "伊朗控制霍爾木茲",
    "完全控制荷姆茲", "完全控制荷莫茲", "完全控制霍爾木茲",
    "荷莫茲海峽封鎖", "霍爾木茲海峽封鎖",
    "封鎖荷莫茲海峽",
    "水雷封鎖荷姆茲", "水雷封鎖霍爾木茲",
    "水雷封鎖波斯灣", "水雷封鎖阿曼灣",
    "伊朗布雷", "伊朗水雷威脅",
    "伊朗水雷攻擊", "伊朗水雷封鎖",
    "革命衛隊布雷", "革命衛隊水雷",
    "磁吸水雷油輪", "磁吸水雷商船",
    "水雷爆炸油輪", "水雷爆炸商船",
    "水雷擊中油輪", "水雷擊中商船",
    "荷姆茲水雷", "波斯灣水雷",
    "阿曼灣水雷", "掃雷行動海灣",
    "水雷清除荷姆茲", "水雷威脅航運",
    "水雷攻擊船隻", "水雷攻擊油輪",
    "水雷封锁霍尔木兹", "水雷封锁波斯湾",
    "水雷封锁阿曼湾",
    "伊朗水雷威胁", "伊朗水雷攻击", "伊朗水雷封锁",
    "革命卫队布雷", "革命卫队水雷",
    "磁吸水雷油轮", "磁吸水雷商船",
    "水雷爆炸油轮", "水雷爆炸商船",
    "水雷击中油轮", "水雷击中商船",
    "霍尔木兹水雷", "波斯湾水雷",
    "阿曼湾水雷", "扫雷行动海湾",
    "水雷清除霍尔木兹", "水雷威胁航运",
    "水雷攻击船只", "水雷攻击油轮",
    # ── 新增：油輪被砲擊（CAT3 情境）──
    "油輪被砲擊", "油輪遭砲擊", "商船被砲擊",
    "油輪被炮弹击中", "油轮遭炮击", "商船被炮弹击中",
    "tanker shelled", "vessel shelled Hormuz",
    "tanker fired upon", "vessel fired upon Gulf",
]

CAT4_KEYWORDS = [
    "Houthi attack", "Houthi missile", "Houthi drone",
    "Houthi ship attack", "Houthi tanker attack",
    "Houthi vessel attack", "Houthi Red Sea",
    "Houthi Bab el-Mandeb", "Houthi shipping",
    "Houthi ballistic missile", "Houthi cruise missile",
    "Houthi anti-ship missile", "Houthi naval drone",
    "Houthi underwater drone", "Houthi USV attack",
    "Red Sea attack", "Red Sea incident",
    "Red Sea shipping attack", "Red Sea tanker attack",
    "Red Sea vessel attack", "Red Sea missile attack",
    "Red Sea drone attack",
    "Bab el-Mandeb attack", "Bab el-Mandeb incident",
    "Bab el-Mandeb shipping", "Bab el-Mandeb tanker",
    "Gulf of Aden attack", "Gulf of Aden incident",
    "Gulf of Aden tanker attack", "Gulf of Aden vessel attack",
    "Yemen attack shipping", "Yemen missile ship",
    "Ansarallah attack", "Ansarallah shipping",
    "Iranian-backed attack shipping",
    "Iran proxy attack tanker", "Iran proxy Red Sea",
    "胡塞攻擊", "胡塞飛彈", "胡塞無人機",
    "胡塞攻擊船隻", "胡塞攻擊油輪",
    "胡塞攻擊商船", "胡塞紅海攻擊",
    "胡塞曼德海峽攻擊", "胡塞反艦飛彈",
    "胡塞彈道飛彈攻船", "胡塞巡弋飛彈攻船",
    "胡塞水面無人艇", "胡塞水下無人艇",
    "紅海攻擊", "紅海船隻遇襲",
    "紅海油輪遭攻擊", "紅海商船遇襲",
    "紅海飛彈攻擊", "紅海無人機攻擊",
    "曼德海峽攻擊", "曼德海峽船隻遇襲",
    "亞丁灣攻擊", "亞丁灣油輪遭攻擊",
    "葉門攻擊船隻", "葉門飛彈攻船",
    "伊朗代理人攻擊船", "伊朗支持攻擊航運",
    "胡塞攻击", "胡塞导弹", "胡塞无人机",
    "胡塞攻击船只", "胡塞攻击油轮",
    "胡塞攻击商船", "胡塞红海攻击",
    "胡塞曼德海峡攻击", "胡塞反舰导弹",
    "胡塞弹道导弹攻船", "胡塞巡航导弹攻船",
    "胡塞水面无人艇", "胡塞水下无人艇",
    "红海攻击", "红海船只遇袭",
    "红海油轮遭攻击", "红海商船遇袭",
    "红海导弹攻击", "红海无人机攻击",
    "曼德海峡攻击", "曼德海峡船只遇袭",
    "亚丁湾攻击", "亚丁湾油轮遭攻击",
    "也门攻击船只", "也门导弹攻船",
    "伊朗代理人攻击船", "伊朗支持攻击航运",
]

CAT5_KEYWORDS = [
    "vessel rerouting", "ship diversion", "vessel diversion",
    "rerouting Cape of Good Hope", "Cape of Good Hope rerouting",
    "Cape routing", "Cape diversion",
    "avoiding Red Sea", "avoiding Suez Canal",
    "avoiding Strait of Hormuz", "avoiding Persian Gulf",
    "avoiding Gulf of Aden", "avoiding Bab el-Mandeb",
    "Red Sea avoidance", "Hormuz avoidance",
    "shipping line reroute", "carrier reroute",
    "container line reroute", "tanker reroute",
    "Maersk reroute", "MSC reroute", "CMA CGM reroute",
    "Evergreen reroute", "COSCO reroute",
    "shipping suspended Red Sea", "shipping suspended Gulf",
    "port of refuge Persian Gulf", "anchorage Persian Gulf",
    "safe anchorage Gulf", "refuge anchorage Gulf",
    "vessels anchored Gulf", "ships waiting Gulf",
    "Fujairah anchorage", "Khor Fakkan anchorage",
    "Muscat anchorage", "Salalah refuge",
    "war risk surcharge", "war risk premium",
    "additional war risk", "war risk insurance",
    "shipping suspended", "service suspended Red Sea",
    "transit suspended Hormuz", "transit suspended Red Sea",
    "Gulf bypass route", "bypass Hormuz",
    "pipeline bypass Gulf", "East-West Pipeline",
    "Fujairah terminal", "ADCOP pipeline",
    "oil supply cover", "storage capacity Gulf",
    "production cut Gulf", "oil evacuation Gulf",
    "Hormuz disruption supply", "energy security Gulf",
    "tanker insurance suspended", "insurers suspended",
    "trading house suspended Gulf",
    # ── 新增：保險/護航（台灣媒體常用）──
    "tanker insurance Gulf", "US escort tanker",
    "navy escort tanker Hormuz", "US Navy escort Gulf",
    "government backstop tanker", "insurance backstop shipping",
    "booking freeze Gulf", "booking cancelled Gulf",
    "ONE cancelled bookings", "container booking freeze",
    "航商宣佈繞航", "航商改道", "航線改道",
    "繞航好望角", "改走好望角",
    "避開紅海", "避開蘇伊士運河",
    "避開荷姆茲海峽", "避開霍爾木茲海峽",
    "避開波斯灣", "避開亞丁灣",
    "避開曼德海峽",
    "馬士基繞航", "地中海航運繞航",
    "達飛輪船繞航", "長榮海運繞航",
    "中遠海運繞航", "陽明海運繞航",
    "暫停紅海航線", "暫停波斯灣航線",
    "暫停荷姆茲通行", "暫停蘇伊士通行",
    "波斯灣避難錨地", "波斯灣錨泊等待",
    "富查伊拉錨地", "科爾法坎錨地",
    "馬斯喀特避難", "薩拉拉避難",
    "戰爭附加費", "戰爭險保費上漲",
    "航運保險費率上漲", "繞航費用增加",
    # ── 新增：航運股/ETF（台灣財經媒體）──
    "遶行改道",
    "遶航好望角", "改走好望角",
    "避開紅海", "避開蘇伊士運河",
    "避開荷莫茲海峽", "避開波斯灣",
    "避開亞丁灣", "避開曼德海峽",
    "馬士基繞航", "地中海航運繞航",
    "達飛輪船繞航", "長榮海運繞航",
    "中遠海運繞航", "陽明海運繞航",
    "暫停紅海航線", "暫停波斯灣航線",
    "暫停荷莫茲通行", "暫停蘇伊士通行",
    "波斯灣避難錨地", "波斯灣錨泊等待",
    "富查伊拉錨地", "科爾法坎錨地",
    "馬斯喀特避難", "薩拉拉避難",
    "戰爭附加費", "戰爭險保費上漲",
    "航運保險費率上漲", "繞航費用增加",
]

OTHER_KEYWORDS = [
    "oil tanker", "product tanker", "chemical tanker",
    "VLCC", "ULCC", "Aframax", "Suezmax",
    "LNG carrier", "LNG tanker", "LPG carrier",
    "container ship", "containership", "container vessel",
    "bulk carrier", "bulk vessel",
    "merchant vessel", "merchant ship",
    "cargo vessel", "newbuilding", "shipbuilding order",
    "freight rate", "shipping rate", "charter rate",
    "bunker fuel", "shipping cost",
    "port congestion", "port closure", "port blockade",
    "channel closure", "waterway closure",
    "UKMTO alert", "UKMTO incident", "IMB piracy",
    "maritime piracy", "ship hijacking", "vessel hijacking",
    "armed robbery at sea",
    "crew kidnapped", "seafarer kidnapped", "crew hostage",
    "maritime security incident", "maritime security alert",
    "shadow fleet tanker", "dark fleet vessel",
    "sanctioned vessel", "sanctioned tanker",
    "shipping sanctions", "tanker sanctions",
    "Iran oil sanctions", "Iran shipping sanctions",
    "naval escort shipping", "Operation Prosperity Guardian",
    "CTF-151", "Combined Maritime Forces",
    "Black Sea shipping", "Black Sea tanker",
    "Suez Canal closure", "Suez Canal transit",
    "Panama Canal closure", "Panama Canal transit",
    "Persian Gulf shipping", "Persian Gulf tanker",
    "Gulf of Oman shipping",
    "油輪", "成品油輪", "化學品船", "貨櫃船", "散裝船",
    "液化天然氣船", "液化石油氣船", "商船", "貨輪",
    "超大型油輪", "新造船",
    "商船停航", "貨輪停航", "航運停航",
    "運費上漲", "運價", "航運市場", "造船訂單",
    "港口封閉", "港口擁堵",
    "海盜攻擊", "海盜劫船", "武裝登船",
    "船員被劫", "船員被扣押",
    "影子船隊", "黑名單船舶",
    "制裁油輪", "制裁船隊",
    "護航艦隊", "繁榮衛士行動",
    "戰爭險", "航運保險",
    "黑海航運", "蘇伊士運河封鎖", "蘇伊士運河通行",
    "巴拿馬運河封鎖", "巴拿馬運河關閉",
    "波斯灣航運", "波斯灣油輪",
    "伊朗石油制裁", "伊朗航運制裁",
    "油船", "成品油船", "化学品船", "集装箱船", "散装船",
    "液化天然气船", "货轮", "超大型油轮",
    "商船停航", "货轮停航", "航运停航",
    "运费上涨", "运价", "航运市场", "造船订单",
    "港口封闭", "港口拥堵",
    "海盗攻击", "海盗劫船", "武装登船",
    "船员被劫", "船员被扣押",
    "影子船队", "黑名单船舶",
    "制裁油轮", "制裁船队",
    "护航舰队", "繁荣卫士行动",
    "战争险", "航运保险",
    "黑海航运", "苏伊士运河封锁", "苏伊士运河通行",
    "巴拿马运河封锁", "巴拿马运河关闭",
    "波斯湾航运", "波斯湾油轮",
    "伊朗石油制裁", "伊朗航运制裁",
]

# ── 建立情境關鍵字對照表 ──
INCIDENT_KEYWORD_MAP: dict[str, str] = {}
for _kw in CAT1_KEYWORDS:
    INCIDENT_KEYWORD_MAP[_kw.lower()] = "CAT1"
for _kw in CAT2_KEYWORDS:
    INCIDENT_KEYWORD_MAP.setdefault(_kw.lower(), "CAT2")
for _kw in CAT3_KEYWORDS:
    INCIDENT_KEYWORD_MAP.setdefault(_kw.lower(), "CAT3")
for _kw in CAT4_KEYWORDS:
    INCIDENT_KEYWORD_MAP.setdefault(_kw.lower(), "CAT4")
for _kw in CAT5_KEYWORDS:
    INCIDENT_KEYWORD_MAP.setdefault(_kw.lower(), "CAT5")
for _kw in OTHER_KEYWORDS:
    INCIDENT_KEYWORD_MAP.setdefault(_kw.lower(), "OTHER")

_ALL_RAW = (
    CAT1_KEYWORDS + CAT2_KEYWORDS + CAT3_KEYWORDS +
    CAT4_KEYWORDS + CAT5_KEYWORDS + OTHER_KEYWORDS
)
_seen_kw: set = set()
ALL_KEYWORDS: list = []
for _kw in _ALL_RAW:
    if _kw.lower() not in _seen_kw:
        ALL_KEYWORDS.append(_kw)
        _seen_kw.add(_kw.lower())

logger.info(
    f"📚 關鍵字載入 | "
    f"CAT1: {len(CAT1_KEYWORDS)} | CAT2: {len(CAT2_KEYWORDS)} | "
    f"CAT3: {len(CAT3_KEYWORDS)} | CAT4: {len(CAT4_KEYWORDS)} | "
    f"CAT5: {len(CAT5_KEYWORDS)} | 其他: {len(OTHER_KEYWORDS)} | "
    f"去重後: {len(ALL_KEYWORDS)} 個"
)


# ══════════════════════════════════════════════════════════════
# 語境驗證詞集
# ══════════════════════════════════════════════════════════════
TITLE_SHIPPING_TERMS = {
    "tanker", "vessel", "ship", "shipping", "maritime",
    "fleet", "cargo", "freight", "port", "canal",
    "strait", "suez", "hormuz", "panama",
    "vlcc", "lng", "lpg", "bunker", "charter",
    "seafarer", "crew", "piracy", "hijack",
    "red sea", "gulf of aden", "persian gulf",
    "bab el-mandeb", "container ship", "bulk carrier",
    "houthi", "irgc", "mine", "blockade",
    # ── 新增：荷莫茲（台灣常見異體）──
    "荷莫茲", "荷姆茲", "霍爾木茲",
    "油輪", "貨輪", "商船", "貨櫃船", "散裝船",
    "航運", "海運", "港口", "運河", "海峽",
    "紅海", "波斯灣", "亞丁灣", "海盜", "劫船",
    "護航", "戰爭險", "運費", "船舶",
    "水雷", "布雷", "掃雷", "胡塞",
    "油船", "货轮", "集装箱船", "散装船",
    "航运", "海运", "港口", "运河", "海峡",
    "红海", "波斯湾", "亚丁湾", "海盗", "劫船",
    "护航", "战争险", "运费", "船舶",
    "水雷", "布雷", "扫雷", "胡塞",
    # ── 新增：台灣財經媒體常用詞 ──
    "航運股", "航運ETF", "航運族群", "運價",
}

BODY_SHIPPING_TERMS = {
    "tanker", "vessel", "ship", "shipping", "maritime",
    "fleet", "cargo", "freight", "port", "canal",
    "strait", "hormuz", "suez", "panama",
    "vlcc", "lng", "lpg", "bunker", "charter",
    "seafarer", "crew", "piracy", "red sea",
    "gulf of aden", "persian gulf", "houthi",
    "mine", "irgc",
    "荷莫茲", "荷姆茲", "霍爾木茲",
    "油輪", "貨輪", "商船", "貨櫃船",
    "航運", "海運", "港口", "運河", "海峽",
    "紅海", "波斯灣", "亞丁灣", "海盜",
    "護航", "運費", "船舶", "水雷", "胡塞",
    "油船", "货轮", "集装箱船",
    "航运", "海运", "港口", "运河", "海峡",
    "红海", "波斯湾", "亚丁湾", "海盗",
    "护航", "运费", "船舶", "水雷", "胡塞",
}
# ══════════════════════════════════════════════════════════════
# 財經噪音過濾：標題含任一詞 → 直接排除（即使有航運關鍵字）
# 邏輯：這類文章是財經分析，不是船舶安全事件報導
# ══════════════════════════════════════════════════════════════
FINANCE_NOISE_TITLE_TERMS = {
    # 股市 / 指數
    "台股", "股市", "股價", "漲停", "跌停", "大盤", "指數",
    "外資", "法人", "投信", "自營商", "主力", "籌碼",
    "加權指數", "櫃買指數", "ETF", "基金", "投資組合",
    "選股", "存股", "殖利率", "本益比", "市值",
    "台積電", "聯發科", "鴻海", "台塑", "中鋼",
    # 財經分析
    "油價", "能源股", "航運股", "航運ETF", "航運族群",
    "漲幅", "跌幅", "漲價", "降價", "價格戰",
    "通膨", "升息", "降息", "央行", "聯準會", "Fed",
    "GDP", "CPI", "PPI", "PMI",
    "財報", "營收", "獲利", "EPS", "股息",
    "大洗牌", "資金輪動", "板塊輪動", "避險情緒",
    "恐慌指數", "VIX", "風險溢價", "風險資產",
    "石油危機", "能源危機", "供應鏈風險",   # 分析類標題
    "為何", "為什麼", "解析", "分析師", "預測",
    "看好", "看壞", "買進", "賣出", "目標價",
    "焦點股", "熱門股", "強勢股", "弱勢股",
    "亮燈", "攻上", "衝關", "守住", "失守",
    # 英文財經
    "stock market", "equity", "share price", "investor",
    "hedge fund", "portfolio", "dividend", "earnings",
    "oil price", "crude price", "energy stock",
    "analyst", "forecast", "outlook", "rally", "selloff",
    "inflation", "interest rate", "fed rate",
}

# 摘要財經噪音（摘要含 ≥2 個 → 排除）
FINANCE_NOISE_BODY_TERMS = {
    "台股", "股市", "股價", "漲停", "跌停", "外資賣超",
    "ETF", "基金", "投資", "法人", "籌碼",
    "油價", "能源股", "航運股", "通膨", "升息",
    "財報", "營收", "獲利", "EPS",
    "oil price", "crude price", "stock", "equity",
    "investor", "analyst", "forecast",
}

# ══════════════════════════════════════════════════════════════
# RSS 來源設定（已修正：移除重複的 Yahoo新聞）
# ══════════════════════════════════════════════════════════════
RSS_SOURCES = [
    # ── 中文媒體（台灣）──
    {
        "name": "自由時報", "url": "https://news.ltn.com.tw/rss/world.xml",
        "backup_url": "https://news.ltn.com.tw/rss/all.xml", "extra_urls": [],
        "lang": "zh-TW", "icon": "🇹🇼", "category": "中文媒體",
    },
    {
        "name": "聯合新聞網", "url": "https://udn.com/rssfeed/news/2/6638?ch=news",
        "backup_url": "https://udn.com/rssfeed/news/2/6638", "extra_urls": [],
        "lang": "zh-TW", "icon": "📰", "category": "中文媒體",
    },
    {
        "name": "中央社",
        "url":        "https://rsshub.app/cna/aall",
        "backup_url": "https://rsshub.rssforever.com/cna/aall",
        "extra_urls": [
            "https://rsshub.app/cna/aopl",
            "https://rsshub.rssforever.com/cna/aopl",
        ],
        "lang": "zh-TW", "icon": "🏛️", "category": "中文媒體", "need_clean": True,
    },
    {
        # ✅ Bug 1 修正：移除重複定義，只保留一個 Yahoo新聞
        "name": "Yahoo新聞", "url": "https://tw.news.yahoo.com/rss/world",
        "backup_url": "https://tw.news.yahoo.com/rss/", "extra_urls": [],
        "lang": "zh-TW", "icon": "🟣", "category": "中文媒體",
    },
    {
        "name": "風傳媒",
        "url":        "https://rsshub.app/storm/latest",
        "backup_url": "https://rsshub.rssforever.com/storm/latest",
        "extra_urls": [],
        "lang": "zh-TW", "icon": "🌪️", "category": "中文媒體", "need_clean": True,
    },
    # ── 中文媒體（大陸）──
    {
        "name": "海事服務網 CNSS",
        "url":        "https://rsshub.app/cnss/news",
        "backup_url": "https://rsshub.rssforever.com/cnss/news",
        "extra_urls": [
            "https://rsshub2.rssforever.com/cnss/news",
            "https://rss.fatpandadev.com/cnss/news",
        ],
        "lang": "zh-CN", "icon": "⚓", "category": "中文媒體", "need_clean": True,
    },
    {
        "name": "壹航運",
        "url":        "__oneshipping_html__",
        "backup_url": None, "extra_urls": [],
        "lang": "zh-CN", "icon": "🚢", "category": "中文媒體",
        "_html_scraper": True,
    },
    {
        "name": "人民網 國際",
        "url":        "https://rsshub.app/people/world",
        "backup_url": "https://rsshub.rssforever.com/people/world",
        "extra_urls": [
            "https://rsshub2.rssforever.com/people/world",
            "http://www.people.com.cn/rss/world.xml",
        ],
        "lang": "zh-CN", "icon": "🏮", "category": "中文媒體", "need_clean": True,
    },
    {
        "name": "環球時報",
        "url":        "https://rsshub.app/huanqiu/world",
        "backup_url": "https://rsshub.rssforever.com/huanqiu/world",
        "extra_urls": [
            "https://rsshub.app/huanqiu/mil",
            "https://rsshub2.rssforever.com/huanqiu/world",
        ],
        "lang": "zh-CN", "icon": "🌏", "category": "中文媒體", "need_clean": True,
    },
    {
        "name": "新華社 國際",
        "url":        "https://rsshub.app/xinhua/world",
        "backup_url": "https://rsshub.rssforever.com/xinhua/world",
        "extra_urls": [
            "https://rsshub2.rssforever.com/xinhua/world",
            "https://rss.fatpandadev.com/xinhua/world",
        ],
        "lang": "zh-CN", "icon": "📻", "category": "中文媒體", "need_clean": True,
    },
    {
        "name": "澎湃新聞 國際",
        "url":        "https://rsshub.app/thepaper/channel/25950",
        "backup_url": "https://rsshub.rssforever.com/thepaper/channel/25950",
        "extra_urls": [
            "https://rsshub.app/thepaper/channel/121811",
            "https://rsshub2.rssforever.com/thepaper/channel/25950",
        ],
        "lang": "zh-CN", "icon": "🗞️", "category": "中文媒體", "need_clean": True,
    },
    {
        "name": "財新網 國際",
        "url":        "https://rsshub.app/caixin/international",
        "backup_url": "https://rsshub.rssforever.com/caixin/international",
        "extra_urls": [
            "https://rsshub.app/caixin/economy",
            "https://rsshub2.rssforever.com/caixin/international",
        ],
        "lang": "zh-CN", "icon": "💹", "category": "中文媒體", "need_clean": True,
    },
    # ── 航運專業媒體 ──
    {
        "name": "TradeWinds",
        "url":        "https://rss.app/feeds/tvCHOGHBWmcHkBKM.xml",
        "backup_url": "https://rsshub.app/tradewindsnews/latest",
        "extra_urls": ["https://rsshub.rssforever.com/tradewindsnews/latest"],
        "lang": "en", "icon": "🚢", "category": "航運專業", "need_clean": True,
    },
    {
        "name": "Splash247", "url": "https://splash247.com/feed/",
        "backup_url": None, "extra_urls": [],
        "lang": "en", "icon": "⚓", "category": "航運專業",
    },
    {
        "name": "gCaptain", "url": "https://gcaptain.com/feed/",
        "backup_url": "https://gcaptain.com/feed/rss/", "extra_urls": [],
        "lang": "en", "icon": "🧭", "category": "航運專業",
    },
    {
        "name": "Maritime Exec",
        "url":        "https://maritime-executive.com/feed",
        "backup_url": "https://rsshub.app/maritime-executive/article",
        "extra_urls": ["https://rsshub.rssforever.com/maritime-executive/article"],
        "lang": "en", "icon": "⛴️", "category": "航運專業", "need_clean": True,
    },
    {
        "name": "Hellenic Ship",
        "url":        "https://www.hellenicshippingnews.com/feed/",
        "backup_url": "https://www.hellenicshippingnews.com/feed/rss/",
        "extra_urls": [],
        "lang": "en", "icon": "🏛️", "category": "航運專業", "need_clean": True,
    },
    {
        "name": "Safety4Sea",
        "url":        "https://safety4sea.com/feed/",
        "backup_url": "https://safety4sea.com/feed/rss/",
        "extra_urls": [],
        "lang": "en", "icon": "🛡️", "category": "航運專業", "need_clean": True,
    },
    {
        "name": "Container News", "url": "https://container-news.com/feed/",
        "backup_url": None, "extra_urls": [],
        "lang": "en", "icon": "📦", "category": "航運專業",
    },
    {
        "name": "Freightwaves", "url": "https://www.freightwaves.com/news/feed",
        "backup_url": "https://www.freightwaves.com/feed", "extra_urls": [],
        "lang": "en", "icon": "📊", "category": "航運專業",
    },
    {
        "name": "Offshore Energy", "url": "https://www.offshore-energy.biz/feed/",
        "backup_url": None, "extra_urls": [],
        "lang": "en", "icon": "⚡", "category": "航運專業",
    },
    {
        "name": "NewsBase",
        "url":        "https://newsbase.com/rss",
        "backup_url": "https://newsbase.com/feed",
        "extra_urls": [],
        "lang": "en", "icon": "🛢️", "category": "航運專業", "need_clean": True,
    },
    # ── 國際媒體 ──
    {
        "name": "Reuters",
        "url":        "https://feeds.reuters.com/reuters/worldNews",
        "backup_url": "https://news.yahoo.com/rss/world",
        "extra_urls": [],
        "lang": "en", "icon": "🌐", "category": "國際媒體",
    },
    {
        "name": "BBC News", "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "backup_url": "https://feeds.bbci.co.uk/news/rss.xml",
        "extra_urls": [],
        "lang": "en", "icon": "🇬🇧", "category": "國際媒體",
    },
    {
        "name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "backup_url": None, "extra_urls": [],
        "lang": "en", "icon": "🌍", "category": "國際媒體",
    },
    {
        "name": "The Guardian", "url": "https://www.theguardian.com/world/rss",
        "backup_url": None, "extra_urls": [],
        "lang": "en", "icon": "🗞️", "category": "國際媒體",
    },
    {
        "name": "AP News",
        "url":        "https://rsshub.app/apnews/topics/world-news",
        "backup_url": "https://rsshub.rssforever.com/apnews/topics/world-news",
        "extra_urls": ["https://feeds.apnews.com/rss/apf-topnews"],
        "lang": "en", "icon": "📡", "category": "國際媒體", "need_clean": True,
    },
]

# ══════════════════════════════════════════════════════════════
# 鉅亨網 JSON API 來源
# ══════════════════════════════════════════════════════════════
CNYES_SOURCES = [
    {
        "name": "鉅亨網 頭條",
        "api_url": "https://news.cnyes.com/api/v3/news/category/headline?limit=30",
        "icon": "💹", "category": "中文媒體", "lang": "zh-TW",
    },
    {
        "name": "鉅亨網 國際政經",
        "api_url": "https://news.cnyes.com/api/v3/news/category/wd_macro?limit=30",
        "icon": "💹", "category": "中文媒體", "lang": "zh-TW",
    },
    {
        "name": "鉅亨網 能源",
        "api_url": "https://news.cnyes.com/api/v3/news/category/energy?limit=30",
        "icon": "💹", "category": "中文媒體", "lang": "zh-TW",
    },
]


# ══════════════════════════════════════════════════════════════
# XML 清洗工具
# ══════════════════════════════════════════════════════════════
def clean_xml_content(raw) -> str:
    """接受 bytes 或 str，統一清洗為合法 XML 字串。"""
    import gzip as _gzip

    if isinstance(raw, str):
        text = raw
    else:
        if isinstance(raw, bytes) and raw[:2] == b'\x1f\x8b':
            try:
                raw = _gzip.decompress(raw)
            except Exception:
                pass
        try:
            text = raw.decode('utf-8', errors='replace')
        except Exception:
            text = raw.decode('latin-1', errors='replace')

    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    text = re.sub(r'&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', text)
    return text


# ══════════════════════════════════════════════════════════════
# 壹航運 HTML 爬蟲
# ══════════════════════════════════════════════════════════════
class OneShippingScraper:
    BASE_URL    = "https://www.oneshipping.info"
    SITEMAP_URL = "https://www.oneshipping.info/sitemap.xml"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer":         "https://www.oneshipping.info/",
    }
    SOURCE_META = {
        "name":     "壹航運",
        "icon":     "🚢",
        "lang":     "zh-CN",
        "category": "中文媒體",
    }

    def __init__(self, keywords: list, hours_back: int = 2):
        self.keywords   = keywords
        self.hours_back = hours_back
        self.seen_urls: set = set()

    def _get_article_urls_from_sitemap(self) -> list[dict]:
        try:
            resp = requests.get(
                self.SITEMAP_URL, headers=self.HEADERS, timeout=20, verify=False
            )
            resp.raise_for_status()
            xml_text = resp.text
            pattern = re.compile(
                r'<url>\s*<loc>(https?://[^<]+/newsinfo/\d+\.html)</loc>'
                r'(?:\s*<lastmod>([^<]+)</lastmod>)?',
                re.IGNORECASE | re.DOTALL,
            )
            cutoff  = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back * 3)
            results = []
            for m in pattern.finditer(xml_text):
                url_str  = m.group(1).strip()
                lastmod  = m.group(2).strip() if m.group(2) else ''
                pub_time = None
                if lastmod:
                    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%d'):
                        try:
                            pub_time = datetime.strptime(lastmod[:19], fmt[:len(lastmod[:19])])
                            if pub_time.tzinfo is None:
                                pub_time = pub_time.replace(
                                    tzinfo=timezone(timedelta(hours=8))
                                ).astimezone(timezone.utc)
                            break
                        except ValueError:
                            continue
                if pub_time and pub_time < cutoff:
                    continue
                results.append({"url": url_str, "lastmod": pub_time})
            results.sort(
                key=lambda x: x["lastmod"] or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            return results[:60]
        except Exception as e:
            logger.warning(f"    ⚠️  壹航運 sitemap 失敗: {e}")
            return []

    def _fetch_article(self, url: str) -> dict | None:
        try:
            resp = requests.get(
                url, headers=self.HEADERS, timeout=15, verify=False, allow_redirects=True
            )
            resp.raise_for_status()
            html = resp.text
            title = ''
            for pat in [
                r'<title[^>]*>([^<]{5,200})</title>',
                r'<h1[^>]*>([^<]{5,200})</h1>',
            ]:
                m = re.search(pat, html, re.IGNORECASE)
                if m:
                    title = re.sub(r'\s+', ' ', m.group(1)).strip()
                    title = re.sub(r'[_\-–|]\s*壹航運.*$', '', title).strip()
                    if len(title) >= 8:
                        break
            if not title:
                return None
            time_match = re.search(
                r'(\d{4}[-/]\d{2}[-/]\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)', html
            )
            pub_str    = time_match.group(1).replace('/', '-') if time_match else ''
            paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html, re.IGNORECASE | re.DOTALL)
            summary_parts = []
            for p in paragraphs:
                clean = re.sub(r'<[^>]+>', '', p).strip()
                clean = re.sub(r'\s+', ' ', clean)
                if len(clean) > 20:
                    summary_parts.append(clean)
                if sum(len(s) for s in summary_parts) >= 300:
                    break
            summary = ' '.join(summary_parts)[:300]
            if len(summary) == 300:
                summary += '...'
            return {"title": title, "pub_str": pub_str, "summary": summary}
        except Exception as e:
            logger.debug(f"      壹航運文章失敗: {url} → {e}")
            return None

    def _parse_pub_time(self, pub_str: str) -> datetime | None:
        if not pub_str:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(pub_str.strip(), fmt)
                return dt.replace(
                    tzinfo=timezone(timedelta(hours=8))
                ).astimezone(timezone.utc)
            except ValueError:
                continue
        return None

    def fetch(self, scraper_ref) -> list[dict]:
        results = []
        cutoff  = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back)
        logger.info("\n  📡 [中文媒體][zh-CN] 壹航運（sitemap 爬蟲）")
        candidates = self._get_article_urls_from_sitemap()
        logger.info(f"    📊 共發現 {len(candidates)} 篇候選文章")
        matched_count = skipped_kw = skipped_time = skipped_dup = 0
        for cand in candidates:
            url = cand["url"]
            if url in self.seen_urls:
                skipped_dup += 1
                continue
            detail = self._fetch_article(url)
            if not detail:
                skipped_kw += 1
                continue
            title   = detail["title"]
            summary = detail["summary"]
            pub_str = detail["pub_str"]
            pub_time = cand["lastmod"] or self._parse_pub_time(pub_str)
            if pub_time and pub_time < cutoff:
                skipped_time += 1
                continue
            matched = scraper_ref._match_keywords(title, summary)
            if not matched:
                skipped_kw += 1
                continue
            self.seen_urls.add(url)
            incident_cat = scraper_ref._classify_incident(title, summary)
            pub_display  = (
                pub_time.strftime('%Y-%m-%d %H:%M UTC') if pub_time else '時間未知'
            )
            results.append({
                'source_name':     self.SOURCE_META['name'],
                'source_icon':     self.SOURCE_META['icon'],
                'source_lang':     self.SOURCE_META['lang'],
                'source_category': self.SOURCE_META['category'],
                'title':           title,
                'summary':         summary,
                'link':            url,
                'published':       pub_display,
                'matched':         matched,
                'incident_cat':    incident_cat,
            })
            matched_count += 1
        logger.info(
            f"  📋 壹航運 | 候選 {len(candidates)} | "
            f"命中 {matched_count} | 無關鍵字 {skipped_kw} | "
            f"時間 {skipped_time} | 重複 {skipped_dup}"
        )
        return results


# ══════════════════════════════════════════════════════════════
# 新聞爬取器
# ══════════════════════════════════════════════════════════════
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
    HEADERS_CNYES = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "zh-TW,zh;q=0.9",
        "Referer": "https://news.cnyes.com/",
    }

    def __init__(self, keywords: list, sources: list,
                 cnyes_sources: list, hours_back: int = 2):
        self.keywords      = keywords
        self.sources       = sources
        self.cnyes_sources = cnyes_sources
        self.hours_back    = hours_back
        self.seen_urls: set = set()

    # ── 語境驗證 ──
    def _validate_shipping_context(self, title: str, summary: str) -> bool:
        """
        語境驗證：
        Step 1：標題含財經噪音詞 → 直接排除
        Step 2：摘要含 ≥2 個財經噪音詞 → 排除
        Step 3：標題含航運詞 → 通過
        Step 4：摘要含 ≥2 個航運詞 → 通過
        """
        title_clean   = _html_module.unescape(title)
        summary_clean = _html_module.unescape(summary)
        title_lower   = title_clean.lower()
        full_lower    = (title_clean + " " + summary_clean).lower()

        # ── Step 1：標題財經黑名單（直接排除）──
        for term in FINANCE_NOISE_TITLE_TERMS:
            if term.lower() in title_lower:
                return False

        # ── Step 2：摘要財經噪音計數（≥2 個排除）──
        body_finance_hits = sum(
            1 for term in FINANCE_NOISE_BODY_TERMS
            if term.lower() in full_lower
        )
        if body_finance_hits >= 2:
            return False

        # ── Step 3：標題含航運詞 → 通過 ──
        for term in TITLE_SHIPPING_TERMS:
            if term.lower() in title_lower:
                return True

        # ── Step 4：摘要含 ≥2 個航運詞 → 通過 ──
        body_shipping_hits = sum(
            1 for term in BODY_SHIPPING_TERMS
            if term.lower() in full_lower
        )
        return body_shipping_hits >= 2


    # ── 情境分類 ──
    def _classify_incident(self, title: str, summary: str) -> str:
        title_clean   = _html_module.unescape(title)
        summary_clean = _html_module.unescape(summary)
        full_lower    = (title_clean + " " + summary_clean).lower()
        best_cat = "GEN"
        best_pri = INCIDENT_CATEGORIES["GEN"]["priority"]
        for kw_lower, cat in INCIDENT_KEYWORD_MAP.items():
            if kw_lower in full_lower:
                pri = INCIDENT_CATEGORIES[cat]["priority"]
                if pri < best_pri:
                    best_pri = pri
                    best_cat = cat
        return best_cat

    # ── 關鍵字比對 ──
    def _match_keywords(self, title: str, summary: str) -> list[tuple]:
        title_clean   = _html_module.unescape(title)
        summary_clean = _html_module.unescape(summary)
        if not self._validate_shipping_context(title_clean, summary_clean):
            return []
        full_lower = (title_clean + " " + summary_clean).lower()
        matched, seen_kw = [], set()
        for kw in self.keywords:
            kw_lower = kw.lower()
            if kw_lower in full_lower and kw not in seen_kw:
                cat = INCIDENT_KEYWORD_MAP.get(kw_lower, "GEN")
                cfg = INCIDENT_CATEGORIES[cat]
                matched.append((kw, cfg["label"], cfg["color"]))
                seen_kw.add(kw)
        return matched

    # ── 時間解析 ──
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
            raw_clean = (raw_time
                         .replace(' CST', ' +0800')
                         .replace(' +0800 (CST)', ' +0800'))
            for fmt in formats:
                try:
                    dt = datetime.strptime(raw_clean.strip(), fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                    return dt.astimezone(timezone.utc)
                except ValueError:
                    continue
        return None

    # ── RSS 下載 ──
    def _download_rss(self, url: str, need_clean: bool = False, is_cn: bool = False):
        headers = self.HEADERS_CN if is_cn else self.HEADERS_DEFAULT
        headers = {**headers, "Accept-Encoding": "gzip, deflate, br"}
        try:
            resp = requests.get(url, headers=headers,
                                timeout=20, verify=False, allow_redirects=True)
            resp.raise_for_status()
            if len(resp.content) < 100:
                logger.warning(f"    ⚠️  回應過短 ({len(resp.content)} bytes)")
                return None
            if need_clean:
                cleaned = clean_xml_content(resp.text)
                parsed  = feedparser.parse(io.StringIO(cleaned))
            else:
                try:
                    parsed = feedparser.parse(io.StringIO(resp.text))
                except Exception:
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

    # ── 建立新聞物件 ──
    def _build_item(self, source: dict, title: str, summary: str,
                    link: str, pub_time: datetime | None,
                    matched: list) -> dict:
        incident_cat = self._classify_incident(title, summary)
        pub_str = (pub_time.strftime('%Y-%m-%d %H:%M UTC')
                   if pub_time else '時間未知')
        return {
            'source_name':     source['name'],
            'source_icon':     source['icon'],
            'source_lang':     source.get('lang', 'en'),
            'source_category': source.get('category', ''),
            'title':           title.strip(),
            'summary':         summary,
            'link':            link,
            'published':       pub_str,
            'matched':         matched,
            'incident_cat':    incident_cat,
        }

    # ── 單一 RSS 來源爬取 ──
    def fetch_from_source(self, source: dict) -> list:
        if source.get("_html_scraper"):
            return []
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
                # ✅ 先清洗 HTML tag + 解碼實體，再做關鍵字比對
                summary_clean = _html_module.unescape(
                    re.sub(r'<[^>]+>', '', summary)
                ).strip()
                                # ── 鉅亨網特別過濾：排除純財經分析文章 ──
                # 標題含「為何」「分析」「預測」「焦點股」等 → 跳過
                CNYES_SKIP_PATTERNS = [
                    r'為何', r'為什麼', r'焦點股', r'熱門股',
                    r'漲停', r'跌停', r'外資', r'法人',
                    r'ETF', r'基金', r'股息', r'財報',
                    r'油價.*美元', r'美元.*油價',
                    r'石油危機', r'能源危機',
                    r'大洗牌', r'資金輪動',
                    r'恐慌指數', r'VIX',
                    r'台股', r'股市',
                ]
                if any(re.search(p, title) for p in CNYES_SKIP_PATTERNS):
                    skipped_ctx += 1
                    continue

                matched = self._match_keywords(title, summary_clean)
                if not matched:
                    if not self._validate_shipping_context(title, summary_clean):
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

    # ── 鉅亨網 JSON API 爬取 ──
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
                content_raw = item.get("content", "") or item.get("summary", "") or ""
                # ✅ Bug 2+3 修正：統一用 summary_clean 做比對與儲存
                summary_clean = _html_module.unescape(
                    re.sub(r'<[^>]+>', '', content_raw)
                ).strip()
                if len(summary_clean) > 300:
                    summary_clean = summary_clean[:300] + "..."
                link = f"https://news.cnyes.com/news/id/{news_id}" if news_id else ""
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
                # ✅ 傳 summary_clean（已清洗），不再傳原始 summary
                matched = self._match_keywords(title, summary_clean)
                if not matched:
                    if not self._validate_shipping_context(title, summary_clean):
                        skipped_ctx += 1
                    continue
                if link:
                    self.seen_urls.add(link)
                results.append(
                    self._build_item(source, title, summary_clean, link, pub_time, matched)
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

    # ── 全部來源彙整 ──
    def fetch_all(self) -> dict:
        all_news = []
        for source in self.sources:
            all_news.extend(self.fetch_from_source(source))
        oneshipping_scraper = OneShippingScraper(
            keywords   = self.keywords,
            hours_back = self.hours_back,
        )
        all_news.extend(oneshipping_scraper.fetch(self))
        for cnyes_source in self.cnyes_sources:
            all_news.extend(self.fetch_from_cnyes(cnyes_source))

        all_news.sort(
            key=lambda x: x['published'] if x['published'] != '時間未知' else '0000',
            reverse=True
        )

        zh_tw_news    = [n for n in all_news if n['source_category'] == '中文媒體' and n['source_lang'] == 'zh-TW']
        zh_cn_news    = [n for n in all_news if n['source_category'] == '中文媒體' and n['source_lang'] == 'zh-CN']
        shipping_news = [n for n in all_news if n['source_category'] == '航運專業']
        intl_news     = [n for n in all_news if n['source_category'] == '國際媒體']
        cat1_news = [n for n in all_news if n['incident_cat'] == 'CAT1']
        cat2_news = [n for n in all_news if n['incident_cat'] == 'CAT2']
        cat3_news = [n for n in all_news if n['incident_cat'] == 'CAT3']
        cat4_news = [n for n in all_news if n['incident_cat'] == 'CAT4']
        cat5_news = [n for n in all_news if n['incident_cat'] == 'CAT5']
        other_news  = [n for n in all_news if n['incident_cat'] == 'OTHER']

        logger.info(
            f"\n{'='*60}\n"
            f"📊 最終結果（媒體分類）:\n"
            f"   🇹🇼 台灣新聞媒體: {len(zh_tw_news)} 筆\n"
            f"   🇨🇳 大陸新聞媒體: {len(zh_cn_news)} 筆\n"
            f"   🚢 航運專業: {len(shipping_news)} 筆\n"
            f"   🌐 國際新聞媒體: {len(intl_news)} 筆\n"
            f"   📰 總計:     {len(all_news)} 筆\n"
            f"\n📊 最終結果（情境分類）:\n"
            f"   💥 CAT1: {len(cat1_news)} 筆\n"
            f"   🎯 CAT2: {len(cat2_news)} 筆\n"
            f"   💣 CAT3: {len(cat3_news)} 筆\n"
            f"   🚀 CAT4: {len(cat4_news)} 筆\n"
            f"   🔀 CAT5: {len(cat5_news)} 筆\n"
            f"   🚢 其他:  {len(other_news)} 筆\n"
            f"{'='*60}"
        )
        return {
            'all':      all_news,
            'zh_tw':    zh_tw_news,
            'zh_cn':    zh_cn_news,
            'shipping': shipping_news,
            'intl':     intl_news,
            'cat1':     cat1_news,
            'cat2':     cat2_news,
            'cat3':     cat3_news,
            'cat4':     cat4_news,
            'cat5':     cat5_news,
            'other':    other_news,
        }


# ══════════════════════════════════════════════════════════════
# Email 發送器  v5.2
# ══════════════════════════════════════════════════════════════
class NewsEmailSender:

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
        if len(news_data.get('all', [])) == 0:
            logger.info("ℹ️  無相關新聞，跳過發送")
            return False
        try:
            tpe_time = run_time.astimezone(timezone(timedelta(hours=8)))
            subject  = (
                f"11GITHUB_Maritime Intel News Alert "
                f"({tpe_time.strftime('%m/%d %H:%M')}) "
                f"— {len(news_data['all'])} 則"
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

    # ──────────────────────────────────────────────────────────
    # 單張新聞卡片 (優化留白與卡片質感)
    # ──────────────────────────────────────────────────────────
    @staticmethod
    def _render_card(item: dict) -> str:
        cat_cfg      = INCIDENT_CATEGORIES.get(item.get('incident_cat', 'GEN'),
                                               INCIDENT_CATEGORIES['GEN'])
        border_color = cat_cfg['color']
        pub = item['published']
        if pub != '時間未知':
            try:
                dt  = datetime.strptime(pub, '%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc)
                pub = dt.astimezone(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')
            except Exception:
                pass
        lang      = item.get('source_lang', 'en')
        lang_bg   = "#dbeafe" if lang == "en" else "#dcfce7"
        lang_fg   = "#1d4ed8" if lang == "en" else "#15803d"
        lang_text = "EN" if lang == "en" else "中文"
        safe_title   = (item['title']
                        .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
        safe_summary = (item['summary']
                        .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
        kw_map = {
            "#dc2626": ("#fef2f2", "#dc2626"),
            "#b45309": ("#fffbeb", "#b45309"),
            "#7c3aed": ("#f5f3ff", "#7c3aed"),
            "#0369a1": ("#eff6ff", "#0369a1"),
            "#047857": ("#ecfdf5", "#047857"),
            "#475569": ("#f1f5f9", "#475569"),
        }
        kw_cells = ""
        for kw, _label, color in item['matched'][:3]:
            kw_bg_c, kw_fg_c = kw_map.get(color, ("#f1f5f9", "#475569"))
            kw_cells += (
                f'<td bgcolor="{kw_bg_c}" style="padding:4px 10px; border-radius:4px; border:1px solid {kw_fg_c};">'
                f'<font face="Arial,Microsoft JhengHei,sans-serif" size="1" color="{kw_fg_c}">'
                f'<b>{kw}</b></font></td><td width="6"></td>'
            )
        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#ffffff" style="margin-bottom:14px; border:1px solid #cbd5e1; border-radius:6px; overflow:hidden;">
<tr>
  <td width="5" bgcolor="{border_color}" style="padding:0;">&nbsp;</td>
  <td style="padding:16px 18px;">
    <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
      <td align="left" valign="middle">
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
          {item['source_icon']}&nbsp;{item['source_name']}
        </font>
        &nbsp;
        <table border="0" cellpadding="0" cellspacing="0" style="display:inline-table;"><tr>
          <td bgcolor="{lang_bg}" style="padding:3px 8px; border-radius:3px;">
            <font face="Arial,sans-serif" size="1" color="{lang_fg}"><b>{lang_text}</b></font>
          </td>
        </tr></table>
      </td>
      <td align="right" valign="middle">
        <font face="Arial,sans-serif" size="2" color="#94a3b8">🕐&nbsp;{pub}</font>
      </td>
    </tr></table>
    <table width="100%" border="0" cellpadding="0" cellspacing="0"
           style="margin-top:10px;"><tr><td>
      <a href="{item['link']}" target="_blank" style="text-decoration:none;">
        <font face="Microsoft JhengHei,Arial,sans-serif" size="4" color="#0f172a">
          <b>{safe_title}</b>
        </font>
      </a>
    </td></tr></table>
    <table width="100%" border="0" cellpadding="10" cellspacing="0"
           bgcolor="#f8fafc" style="margin-top:10px; border-left:3px solid {border_color}; border-radius:0 4px 4px 0;"><tr><td>
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569" style="line-height:1.5;">
        {safe_summary or '（無摘要）'}
      </font>
    </td></tr></table>
    <table width="100%" border="0" cellpadding="0" cellspacing="0"
           style="margin-top:12px;"><tr>
      <td align="left" valign="middle">
        <table border="0" cellpadding="0" cellspacing="0"><tr>
          {kw_cells}
        </tr></table>
      </td>
      <td align="right" valign="middle">
        <table border="0" cellpadding="8" cellspacing="0"
               bgcolor="{border_color}" style="border-radius:4px;"><tr><td>
          <a href="{item['link']}" target="_blank" style="text-decoration:none;">
            <font face="Arial,sans-serif" size="2" color="#ffffff">
              <b>閱讀原文 &rarr;</b>
            </font>
          </a>
        </td></tr></table>
      </td>
    </tr></table>
  </td>
</tr>
</table>"""

    # ──────────────────────────────────────────────────────────
    # 情境區塊 (優化標題帶狀設計)
    # ──────────────────────────────────────────────────────────
    @staticmethod
    def _render_incident_section(cat_key: str, news_list: list) -> str:
        cfg = INCIDENT_CATEGORIES[cat_key]
        if not news_list:
            return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:10px; border:1px solid #e2e8f0; border-radius:6px; overflow:hidden;">
  <tr>
    <td width="5" bgcolor="{cfg['color']}">&nbsp;</td>
    <td bgcolor="#ffffff" style="padding:12px 16px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif"
                size="3" color="{cfg['color']}">
            <b>{cfg['icon']}&nbsp;{cfg['label']}</b>
          </font>
        </td>
        <td align="right" valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#94a3b8">
            本期無相關新聞
          </font>
        </td>
      </tr></table>
    </td>
  </tr>
</table>"""

        cards = "".join(NewsEmailSender._render_card(item) for item in news_list)
        darker = {
            "#dc2626": "#b91c1c", "#b45309": "#92400e",
            "#7c3aed": "#6d28d9", "#0369a1": "#075985",
            "#047857": "#065f46", "#475569": "#334155",
        }
        count_bg = darker.get(cfg['color'], "#334155")
        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:20px; border-radius:6px; overflow:hidden; border:1px solid #e2e8f0;">
  <tr>
    <td bgcolor="{cfg['color']}" style="padding:12px 18px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td align="left" valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif"
                size="4" color="#ffffff">
            <b>{cfg['icon']}&nbsp;{cfg['label']}</b>
          </font>
        </td>
        <td align="right" valign="middle" width="60">
          <table border="0" cellpadding="6" cellspacing="0"
                 bgcolor="{count_bg}" style="border-radius:4px;"><tr><td align="center">
            <font face="Arial,sans-serif" size="2" color="#ffffff">
              <b>{len(news_list)} 則</b>
            </font>
          </td></tr></table>
        </td>
      </tr></table>
    </td>
  </tr>
  <tr>
    <td bgcolor="{cfg['bg']}" style="padding:16px 16px 2px 16px;">
      {cards}
    </td>
  </tr>
</table>"""

    # ──────────────────────────────────────────────────────────
    # 新聞來源網格
    # ──────────────────────────────────────────────────────────
    @staticmethod
    def _render_source_grid() -> str:
        SOURCE_GROUPS = [
            {
                "title":   "中文媒體（台灣）", "icon": "🇹🇼", "color": "#059669", "bg": "#f0fdf4", "border": "#bbf7d0",
                "sources": [s for s in RSS_SOURCES if s.get("lang") == "zh-TW"] + [s for s in CNYES_SOURCES if s.get("lang") == "zh-TW"],
            },
            {
                "title":   "中文媒體（大陸）", "icon": "🇨🇳", "color": "#dc2626", "bg": "#fff5f5", "border": "#fecaca",
                "sources": [s for s in RSS_SOURCES if s.get("lang") == "zh-CN"],
            },
            {
                "title":   "航運專業媒體", "icon": "🚢", "color": "#2563eb", "bg": "#f0f7ff", "border": "#bfdbfe",
                "sources": [s for s in RSS_SOURCES if s.get("category") == "航運專業"],
            },
            {
                "title":   "國際媒體", "icon": "🌐", "color": "#ea580c", "bg": "#fff7ed", "border": "#fed7aa",
                "sources": [s for s in RSS_SOURCES if s.get("category") == "國際媒體"],
            },
        ]
        groups_html = ""
        for grp in SOURCE_GROUPS:
            sources   = grp["sources"]
            rows_html = ""
            for i in range(0, len(sources), 3):
                chunk = sources[i:i+3]
                while len(chunk) < 3:
                    chunk.append(None)
                cells = ""
                for src in chunk:
                    if src is None:
                        cells += (
                            f'<td width="33%" bgcolor="{grp["bg"]}" '
                            f'style="padding:8px 10px; border-right:1px solid {grp["border"]};"></td>'
                        )
                    else:
                        name   = src.get("name", "")
                        icon   = src.get("icon", "📰")
                        url    = src.get("url") or src.get("api_url", "")
                        if url == "__oneshipping_html__":
                            url = "https://www.oneshipping.info"
                        domain = re.sub(r'^https?://(www\.)?', '', url).split('/')[0]
                        cells += f"""
<td width="33%" bgcolor="{grp['bg']}"
    style="padding:10px; border-right:1px solid {grp['border']};">
  <table border="0" cellpadding="0" cellspacing="0" width="100%"><tr>
    <td width="28" valign="middle" align="center"><font size="3">{icon}</font></td>
    <td valign="middle" style="padding-left:4px;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#1e293b"><b>{name}</b></font><br>
      <font face="Arial,sans-serif" size="1" color="#64748b">{domain}</font>
    </td>
  </tr></table>
</td>"""
                rows_html += f"""
<tr>{cells}</tr>
<tr><td colspan="3" bgcolor="{grp['border']}" height="1"></td></tr>"""
            groups_html += f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:16px; border:1px solid {grp['border']}; border-radius:6px; overflow:hidden;">
  <tr>
    <td colspan="3" bgcolor="{grp['color']}" style="padding:10px 16px;">
      <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#ffffff">
        <b>{grp['icon']}&nbsp;{grp['title']}&nbsp;({len(sources)} 個)</b>
      </font>
    </td>
  </tr>
  {rows_html}
</table>"""
        return groups_html

    # ──────────────────────────────────────────────────────────
    # 主 HTML 生成 (整合所有模塊，替換清爽版主題)
    # ──────────────────────────────────────────────────────────
    def _generate_html(self, news_data: dict, run_time: datetime) -> str:
        tpe_str       = run_time.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
        total_sources = len(RSS_SOURCES) + len(CNYES_SOURCES)
        total_news    = len(news_data['all'])
        cat_order     = ['CAT1', 'CAT2', 'CAT3', 'CAT4', 'CAT5', 'OTHER']
        cat_sections  = "".join(
            self._render_incident_section(k, news_data.get(k.lower(), []))
            for k in cat_order
        )

        def _stat_cell(cat_key: str) -> str:
            cfg   = INCIDENT_CATEGORIES[cat_key]
            count = len(news_data.get(cat_key.lower(), []))
            short_labels = {
                "CAT1": "波斯灣/荷姆茲海峽",
                "CAT2": "海灣國家與美軍",
                "CAT3": "伊朗水雷封鎖",
                "CAT4": "紅海/胡塞攻擊",
                "CAT5": "繞航與避難點",
                "OTHER":  "其他航運動態",
            }
            short = short_labels.get(cat_key, cat_key)
            if count > 0:
                return f"""
<td align="center" bgcolor="{cfg['color']}"
    style="padding:16px 4px; width:14%; border-right:1px solid #ffffff;">
  <font face="Arial,sans-serif" size="6" color="#ffffff"><b>{count}</b></font><br><br>
  <font face="Arial,sans-serif" size="3" color="#ffffff">{cfg['icon']}</font><br>
  <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#ffffff">{short}</font>
</td>"""
            else:
                return f"""
<td align="center" bgcolor="#f8fafc"
    style="padding:16px 4px; width:14%; border-right:1px solid #e2e8f0;">
  <font face="Arial,sans-serif" size="6" color="#cbd5e1"><b>0</b></font><br><br>
  <font face="Arial,sans-serif" size="3" color="#cbd5e1">{cfg['icon']}</font><br>
  <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">{short}</font>
</td>"""

        stat_cells  = "".join(_stat_cell(k) for k in cat_order)
        source_grid = self._render_source_grid()

        source_stats: dict = {}
        for item in news_data.get('all', []):
            key = f"{item['source_icon']} {item['source_name']}"
            source_stats[key] = source_stats.get(key, 0) + 1

        hit_rows = "".join(
            f'<tr>'
            f'<td bgcolor="#ffffff" style="padding:12px 18px; border-bottom:1px solid #f1f5f9;">'
            f'<font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#334155">{s}</font>'
            f'</td>'
            f'<td bgcolor="#ffffff" style="padding:12px 18px; border-bottom:1px solid #f1f5f9;" align="right" width="60">'
            f'<table border="0" cellpadding="4" cellspacing="0" bgcolor="#dbeafe" style="border-radius:4px;"><tr><td align="center" width="30">'
            f'<font face="Arial,sans-serif" size="3" color="#1d4ed8"><b>{c}</b></font>'
            f'</td></tr></table>'
            f'</td></tr>'
            for s, c in sorted(source_stats.items(), key=lambda x: -x[1])
        ) or (
            '<tr><td colspan="2" style="padding:16px 18px;">'
            '<font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#94a3b8">'
            '本次無命中來源</font></td></tr>'
        )

        legend_rows = ""
        legend_data = [
            ("CAT1", "#dc2626", "#fef2f2", "船舶於波斯灣/荷姆茲海峽週遭被攻擊事件"),
            ("CAT2", "#b45309", "#fffbeb", "海灣國家及美軍基地被攻擊事件"),
            ("CAT3", "#7c3aed", "#f5f3ff", "伊朗已採取水雷封鎖"),
            ("CAT4", "#0369a1", "#eff6ff", "紅海/曼德海峽胡塞含伊朗攻擊事件"),
            ("CAT5", "#047857", "#ecfdf5", "航商宣佈採取繞航措施及波斯灣內避難點"),
            ("OTHER",  "#475569", "#f8fafc", "其他航運新聞動態"),
        ]
        for cat_key, bar_color, row_bg, label_text in legend_data:
            cfg = INCIDENT_CATEGORIES[cat_key]
            legend_rows += f"""
<tr>
  <td width="5" bgcolor="{bar_color}">&nbsp;</td>
  <td bgcolor="{row_bg}" style="padding:10px 16px;">
    <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="{bar_color}">
      <b>{cfg['icon']}&nbsp;{cat_key}</b>
    </font>
    &nbsp;&nbsp;
    <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#334155">
      {label_text}
    </font>
  </td>
</tr>
<tr><td colspan="2" bgcolor="#ffffff" height="2"></td></tr>"""

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>WHL_FRM Maritime Intel News</title></head>
<body bgcolor="#f1f5f9" style="margin:0;padding:0;">
<table width="100%" border="0" cellpadding="20" cellspacing="0" bgcolor="#f1f5f9">
<tr><td align="center" valign="top">
<table width="720" border="0" cellpadding="0" cellspacing="0" bgcolor="#ffffff"
       style="border:1px solid #cbd5e1; border-radius:8px; overflow:hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">

  <tr>
    <td bgcolor="#f8fafc" style="padding:24px; border-bottom:1px solid #e2e8f0;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0">
        <tr>
          <td valign="middle">
            <font face="Microsoft JhengHei,Arial,sans-serif" size="5" color="#0f172a">
              <b>🚢&nbsp;WHL Tech_Frm_Maritime Intel News </b>
            </font><br><br>
            <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#475569">
              <b>美伊戰爭-波斯灣航運安全情報快報</b>
            </font>
          </td>
          <td align="right" valign="middle">
            <font face="Arial,sans-serif" size="2" color="#64748b">
              <b>{tpe_str}&nbsp;台北時間</b>
            </font><br><br>
            <table border="0" cellpadding="6" cellspacing="0" bgcolor="#e2e8f0" style="border-radius:4px;">
              <tr><td>
                <font face="Arial,sans-serif" size="2" color="#334155">
                  <b>新聞來源&nbsp;{total_sources}&nbsp;個</b>
                </font>
              </td></tr>
            </table>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <tr><td style="padding:0; border-bottom:1px solid #cbd5e1;">
    <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
      <td align="center" bgcolor="#2563eb" style="padding:16px 6px; width:16%; border-right:1px solid #ffffff;">
        <font face="Arial,sans-serif" size="6" color="#ffffff"><b>{total_news}</b></font><br><br>
        <font face="Arial,sans-serif" size="3" color="#ffffff">📰</font><br>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#dbeafe"><b>總計</b></font>
      </td>
      {stat_cells}
    </tr></table>
  </td></tr>

  <tr><td bgcolor="#ffffff" style="padding:24px 24px 8px 24px;">
    {cat_sections}
  </td></tr>

  <tr><td bgcolor="#ffffff" style="padding:0 24px 24px 24px;">
    <table width="100%" border="0" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0; border-radius:6px; overflow:hidden;">
      <tr>
        <td bgcolor="#f8fafc" style="padding:14px 18px; border-bottom:1px solid #cbd5e1;">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#0f172a">
            <b>📊&nbsp;本次命中新聞來源</b>
          </font>
          &nbsp;&nbsp;
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
            （依命中篇數排序）
          </font>
        </td>
      </tr>
      <tr><td>
        <table width="100%" border="0" cellpadding="0" cellspacing="0">
          {hit_rows}
        </table>
      </td></tr>
    </table>
  </td></tr>

  <tr><td bgcolor="#ffffff" style="padding:0 24px 24px 24px;">
    <table width="100%" border="0" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0; border-radius:6px; overflow:hidden;">
      <tr><td bgcolor="#f8fafc" style="padding:14px 18px; border-bottom:1px solid #cbd5e1;">
        <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#0f172a">
          <b>📡&nbsp;監控來源清單</b>
        </font>
        &nbsp;&nbsp;
        <font face="Arial,sans-serif" size="2" color="#64748b">
          共&nbsp;{total_sources}&nbsp;個&nbsp;·&nbsp;RSS&nbsp;+&nbsp;JSON&nbsp;API
        </font>
      </td></tr>
      <tr><td bgcolor="#ffffff" style="padding:16px 16px 0 16px;">
        {source_grid}
      </td></tr>
    </table>
  </td></tr>

  <tr><td bgcolor="#ffffff" style="padding:0 24px 24px 24px;">
    <table width="100%" border="0" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0; border-radius:6px; overflow:hidden;">
      <tr><td bgcolor="#f8fafc" style="padding:14px 18px; border-bottom:1px solid #cbd5e1;">
        <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#0f172a">
          <b>📌&nbsp;情境分類圖例</b>
        </font>
      </td></tr>
      <tr><td>
        <table width="100%" border="0" cellpadding="0" cellspacing="0">
          {legend_rows}
        </table>
      </td></tr>
    </table>
  </td></tr>

  <tr><td bgcolor="#f8fafc" align="center"
          style="padding:24px 16px; border-top:1px solid #cbd5e1;">
    <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
      此內容為系統自動發送，請勿直接回覆。
    </font><br><br>
    <font face="Arial,sans-serif" size="2" color="#94a3b8">
      <b>Maritime Intel News System</b> &nbsp;·&nbsp; Powered by WHL Fleet Risk Management
    </font>
  </td></tr>

</table>
</td></tr></table>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logger.info("\n" + "=" * 60)
    logger.info("🚢 航運安全監控系統 v5.2")
    logger.info("=" * 60)

    run_time   = datetime.now(tz=timezone.utc)
    hours_back = int(os.environ.get("NEWS_HOURS_BACK", "2"))

    scraper = NewsRssScraper(
        keywords      = ALL_KEYWORDS,
        sources       = RSS_SOURCES,
        cnyes_sources = CNYES_SOURCES,
        hours_back    = hours_back,
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

