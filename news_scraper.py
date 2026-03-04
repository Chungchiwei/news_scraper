#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
航運安全暨地緣政治新聞監控系統
GitHub Actions 版本 - 單次執行
功能說明：
"""

import os
import io
import re
import ssl
import json
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
# ║                                                              ║
# ║  CAT1：船舶於波斯灣/荷姆茲海峽週遭被攻擊事件                 ║
# ║  CAT2：海灣國家及美軍基地被攻擊事件                          ║
# ║  CAT3：伊朗已採取水雷封鎖                                    ║
# ║  CAT4：紅海/曼德海峽胡塞含伊朗攻擊事件                      ║
# ║  CAT5：航商宣佈採取繞航措施及波斯灣內避難點                  ║
# ╚══════════════════════════════════════════════════════════════╝

# ── 每個情境的顯示設定 ──
INCIDENT_CATEGORIES = {
    "CAT1": {
        "label":    "船舶於波斯灣/荷姆茲海峽週遭被攻擊事件",
        "icon":     "💥",
        "color":    "#dc2626",   # 深紅
        "bg":       "#fef2f2",
        "priority": 1,
    },
    "CAT2": {
        "label":    "海灣國家及美軍基地被攻擊事件",
        "icon":     "🎯",
        "color":    "#b45309",   # 深橙
        "bg":       "#fffbeb",
        "priority": 2,
    },
    "CAT3": {
        "label":    "伊朗已採取水雷封鎖",
        "icon":     "💣",
        "color":    "#7c3aed",   # 紫
        "bg":       "#f5f3ff",
        "priority": 3,
    },
    "CAT4": {
        "label":    "紅海/曼德海峽胡塞含伊朗攻擊事件",
        "icon":     "🚀",
        "color":    "#0369a1",   # 藍
        "bg":       "#eff6ff",
        "priority": 4,
    },
    "CAT5": {
        "label":    "航商宣佈採取繞航措施及波斯灣內避難點",
        "icon":     "🔀",
        "color":    "#047857",   # 綠
        "bg":       "#ecfdf5",
        "priority": 5,
    },
    "GEN": {
        "label":    "其他航運動態",
        "icon":     "🚢",
        "color":    "#475569",   # 灰
        "bg":       "#f8fafc",
        "priority": 6,
    },
}

# ══════════════════════════════════════════════════════════════
# CAT1：船舶於波斯灣/荷姆茲海峽週遭被攻擊
# ══════════════════════════════════════════════════════════════
CAT1_KEYWORDS = [
    # 英文
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
    # NewsBase 專用
    "tanker traffic halt", "tanker traffic stopped",
    "vessels struck Gulf", "tanker struck Hormuz",
    "shipping halt Hormuz", "tanker halt Persian Gulf",
    # 中文（繁體）
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
    # 中文（簡體）
    "波斯湾油轮遭攻击", "波斯湾商船遇袭", "波斯湾货轮被攻击",
    "霍尔木兹海峡油轮遭攻击", "霍尔木兹海峡商船遇袭",
    "阿曼湾油轮遭攻击", "阿曼湾商船遇袭",
    "波斯湾油轮被扣押", "波斯湾商船被扣押",
    "革命卫队扣押油轮", "革命卫队扣押商船",
    "伊朗海军扣押油轮", "伊朗海军扣押商船",
    "伊朗扣押船只", "伊朗扣押油轮",
    "波斯湾无人机攻船", "波斯湾导弹攻船",
]

# ══════════════════════════════════════════════════════════════
# CAT2：海灣國家及美軍基地被攻擊
# ══════════════════════════════════════════════════════════════
CAT2_KEYWORDS = [
    # 英文
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
    # 中文（繁體）
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
    # 中文（簡體）
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

# ══════════════════════════════════════════════════════════════
# CAT3：伊朗水雷封鎖
# ══════════════════════════════════════════════════════════════
CAT3_KEYWORDS = [
    # 英文
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
    # NewsBase 專用（長句分析型標題）
    "Hormuz closure", "Strait of Hormuz closure",
    "Hormuz blockade", "Persian Gulf blockade",
    "mine the strait", "mining the strait",
    "mining Hormuz", "mining Persian Gulf",
    "Iran mining campaign", "Iranian mining",
    "submarine minelaying", "mine laying submarine",
    "Hormuz oil flow", "Hormuz oil supply",
    "tanker traffic Hormuz", "tanker traffic halt",
    "oil flow disruption Hormuz",
    # 中文（繁體）
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
    # 中文（簡體）
    "水雷封锁霍尔木兹", "水雷封锁波斯湾",
    "水雷封锁阿曼湾",
    "伊朗布雷", "伊朗水雷威胁",
    "伊朗水雷攻击", "伊朗水雷封锁",
    "革命卫队布雷", "革命卫队水雷",
    "磁吸水雷油轮", "磁吸水雷商船",
    "水雷爆炸油轮", "水雷爆炸商船",
    "水雷击中油轮", "水雷击中商船",
    "霍尔木兹水雷", "波斯湾水雷",
    "阿曼湾水雷", "扫雷行动海湾",
    "水雷清除霍尔木兹", "水雷威胁航运",
    "水雷攻击船只", "水雷攻击油轮",
]

# ══════════════════════════════════════════════════════════════
# CAT4：紅海/曼德海峽胡塞含伊朗攻擊
# ══════════════════════════════════════════════════════════════
CAT4_KEYWORDS = [
    # 英文
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
    # 中文（繁體）
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
    # 中文（簡體）
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

# ══════════════════════════════════════════════════════════════
# CAT5：航商繞航措施及波斯灣內避難點
# ══════════════════════════════════════════════════════════════
CAT5_KEYWORDS = [
    # 英文
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
    # 中文（繁體）
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
    # 中文（簡體）
    "航商宣布绕航", "航商改道", "航线改道",
    "绕航好望角", "改走好望角",
    "避开红海", "避开苏伊士运河",
    "避开霍尔木兹海峡", "避开波斯湾",
    "避开亚丁湾", "避开曼德海峡",
    "马士基绕航", "地中海航运绕航",
    "达飞轮船绕航", "长荣海运绕航",
    "中远海运绕航", "阳明海运绕航",
    "暂停红海航线", "暂停波斯湾航线",
    "暂停霍尔木兹通行", "暂停苏伊士通行",
    "波斯湾避难锚地", "波斯湾锚泊等待",
    "富查伊拉锚地", "科尔法坎锚地",
    "马斯喀特避难", "萨拉拉避难",
    "战争附加费", "战争险保费上涨",
    "航运保险费率上涨", "绕航费用增加",
]

# ══════════════════════════════════════════════════════════════
# GEN：一般航運動態（不屬於以上五類）
# ══════════════════════════════════════════════════════════════
GEN_KEYWORDS = [
    # 英文：船型
    "oil tanker", "product tanker", "chemical tanker",
    "VLCC", "ULCC", "Aframax", "Suezmax",
    "LNG carrier", "LNG tanker", "LPG carrier",
    "container ship", "containership", "container vessel",
    "bulk carrier", "bulk vessel",
    "merchant vessel", "merchant ship",
    "cargo vessel", "newbuilding", "shipbuilding order",
    # 英文：市場
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
    # 中文（繁體）
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
    # 中文（簡體）
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
for _kw in GEN_KEYWORDS:
    INCIDENT_KEYWORD_MAP.setdefault(_kw.lower(), "GEN")

# ── 合併所有關鍵字並去重 ──
_ALL_RAW = (
    CAT1_KEYWORDS + CAT2_KEYWORDS + CAT3_KEYWORDS +
    CAT4_KEYWORDS + CAT5_KEYWORDS + GEN_KEYWORDS
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
    f"CAT5: {len(CAT5_KEYWORDS)} | GEN: {len(GEN_KEYWORDS)} | "
    f"去重後: {len(ALL_KEYWORDS)} 個"
)


# ══════════════════════════════════════════════════════════════
# 語境驗證詞集
# TITLE_SHIPPING_TERMS：標題含任一詞 → 直接通過
# BODY_SHIPPING_TERMS ：標題無航運詞 → 摘要需 ≥2 個才通過
# ══════════════════════════════════════════════════════════════
TITLE_SHIPPING_TERMS = {
    # 英文
    "tanker", "vessel", "ship", "shipping", "maritime",
    "fleet", "cargo", "freight", "port", "canal",
    "strait", "suez", "hormuz", "panama",
    "vlcc", "lng", "lpg", "bunker", "charter",
    "seafarer", "crew", "piracy", "hijack",
    "red sea", "gulf of aden", "persian gulf",
    "bab el-mandeb", "container ship", "bulk carrier",
    "houthi", "irgc", "mine", "blockade",
    # 中文（繁體）
    "油輪", "貨輪", "商船", "貨櫃船", "散裝船",
    "航運", "海運", "港口", "運河", "海峽",
    "紅海", "波斯灣", "亞丁灣", "海盜", "劫船",
    "護航", "戰爭險", "運費", "船舶",
    "水雷", "布雷", "掃雷", "胡塞",
    # 中文（簡體）
    "油船", "货轮", "集装箱船", "散装船",
    "航运", "海运", "港口", "运河", "海峡",
    "红海", "波斯湾", "亚丁湾", "海盗", "劫船",
    "护航", "战争险", "运费", "船舶",
    "水雷", "布雷", "扫雷", "胡塞",
}

BODY_SHIPPING_TERMS = {
    # 英文
    "tanker", "vessel", "ship", "shipping", "maritime",
    "fleet", "cargo", "freight", "port", "canal",
    "strait", "hormuz", "suez", "panama",
    "vlcc", "lng", "lpg", "bunker", "charter",
    "seafarer", "crew", "piracy", "red sea",
    "gulf of aden", "persian gulf", "houthi",
    "mine", "irgc",
    # 中文（繁體）
    "油輪", "貨輪", "商船", "貨櫃船",
    "航運", "海運", "港口", "運河", "海峽",
    "紅海", "波斯灣", "亞丁灣", "海盜",
    "護航", "運費", "船舶", "水雷", "胡塞",
    # 中文（簡體）
    "油船", "货轮", "集装箱船",
    "航运", "海运", "港口", "运河", "海峡",
    "红海", "波斯湾", "亚丁湾", "海盗",
    "护航", "运费", "船舶", "水雷", "胡塞",
}


# ══════════════════════════════════════════════════════════════
# RSS 來源設定
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
        "name": "中央社", "url": "https://www.cna.com.tw/rss/fnall.aspx",
        "backup_url": "https://www.cna.com.tw/rss/aie.aspx", "extra_urls": [],
        "lang": "zh-TW", "icon": "🏛️", "category": "中文媒體", "need_clean": True,
    },
    {
        "name": "Yahoo新聞", "url": "https://tw.news.yahoo.com/rss/world",
        "backup_url": "https://tw.news.yahoo.com/rss/", "extra_urls": [],
        "lang": "zh-TW", "icon": "🟣", "category": "中文媒體",
    },
    {
        "name": "風傳媒", "url": "https://www.storm.mg/feeds/rss",
        "backup_url": None, "extra_urls": [],
        "lang": "zh-TW", "icon": "🌪️", "category": "中文媒體",
    },
    # ── 中文媒體（大陸）── 修復版
    {
        "name": "海事服務網 CNSS",
        # 原 /rss/news.xml 已 404，改用 RSSHub 多備援
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
        # 無 RSS，由 OneShippingScraper 處理，此條目僅供來源清單顯示用
        # fetch_from_source 會因 URL 失敗而跳過，實際由 fetch_all 呼叫 HTML 爬蟲
        "url":        "__oneshipping_html__",   # 特殊標記，跳過 RSS 爬取
        "backup_url": None,
        "extra_urls": [],
        "lang": "zh-CN", "icon": "🚢", "category": "中文媒體",
        "_html_scraper": True,                  # 標記為 HTML 爬蟲
    },
    {
        "name": "人民網 國際",
        "url":        "https://rsshub.app/people/world",
        "backup_url": "https://rsshub.rssforever.com/people/world",
        "extra_urls": [
            "https://rsshub2.rssforever.com/people/world",
            # 直連備援（部分環境可用）
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
        "name": "TradeWinds", "url": "https://rss.app/feeds/tvCHOGHBWmcHkBKM.xml",
        "backup_url": "https://www.tradewindsnews.com/latest", "extra_urls": [],
        "lang": "en", "icon": "🚢", "category": "航運專業",
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
        "name": "Maritime Exec", "url": "https://maritime-executive.com/magazine/rss",
        "backup_url": "https://maritime-executive.com/rss", "extra_urls": [],
        "lang": "en", "icon": "⛴️", "category": "航運專業",
    },
    {
        "name": "Hellenic Ship", "url": "https://www.hellenicshippingnews.com/feed/",
        "backup_url": "https://www.hellenicshippingnews.com/feed/rss/", "extra_urls": [],
        "lang": "en", "icon": "🏛️", "category": "航運專業",
    },
    {
        "name": "Safety4Sea", "url": "https://safety4sea.com/feed/",
        "backup_url": "https://safety4sea.com/feed/rss/", "extra_urls": [],
        "lang": "en", "icon": "🛡️", "category": "航運專業",
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
        "lang": "en", "icon": "🛢️", "category": "航運專業",
        "need_clean": True,
    },
    # ── 國際媒體 ──
    {
        "name": "Reuters", "url": "https://feeds.reuters.com/reuters/worldNews",
        "backup_url": "https://news.yahoo.com/rss/world", "extra_urls": [],
        "lang": "en", "icon": "🌐", "category": "國際媒體",
    },
    {
        "name": "BBC News", "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "backup_url": "https://feeds.bbci.co.uk/news/rss.xml",
        "extra_urls": ["https://rsshub.app/bbc/world",
                       "https://rsshub.app/bbc/chinese/world"],
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
        "name": "AP News", "url": "https://rsshub.app/apnews/topics/world-news",
        "backup_url": "https://feeds.apnews.com/rss/apf-topnews", "extra_urls": [],
        "lang": "en", "icon": "📡", "category": "國際媒體",
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
def clean_xml_content(raw_bytes: bytes) -> str:
    try:
        text = raw_bytes.decode('utf-8', errors='replace')
    except Exception:
        text = raw_bytes.decode('latin-1', errors='replace')
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    text = re.sub(r'&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', text)
    return text

# ══════════════════════════════════════════════════════════════
# 壹航運 HTML 爬蟲（無 RSS，直接解析首頁文章列表）
# ══════════════════════════════════════════════════════════════
class OneShippingScraper:
    BASE_URL = "https://www.oneshipping.info"
    # 首頁上各新聞區塊的入口頁
    SECTION_URLS = [
        "https://www.oneshipping.info/",          # 首頁（含最新）
        "https://www.oneshipping.info/hyrd",       # 航運熱點
        "https://www.oneshipping.info/hysj",       # 航運數據
    ]
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.oneshipping.info/",
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

    def _extract_articles(self, html: str) -> list[dict]:
        """
        從 HTML 中提取所有 /newsinfo/{id}.html 連結與標題。
        壹航運文章 URL 格式固定為 /newsinfo/數字.html
        """
        articles = []
        # 匹配 <a href="/newsinfo/123456.html">標題文字</a>
        pattern = re.compile(
            r'<a[^>]+href=["\'](/newsinfo/(\d+)\.html)["\'][^>]*>\s*([^<]{4,200})\s*</a>',
            re.IGNORECASE | re.DOTALL,
        )
        seen_ids: set = set()
        for m in pattern.finditer(html):
            path, art_id, raw_title = m.group(1), m.group(2), m.group(3)
            if art_id in seen_ids:
                continue
            seen_ids.add(art_id)
            title = re.sub(r'\s+', ' ', raw_title).strip()
            # 過濾掉太短或明顯是導覽列的文字
            if len(title) < 8:
                continue
            articles.append({
                "url":   f"{self.BASE_URL}{path}",
                "title": title,
            })
        return articles

    def _fetch_article_detail(self, url: str) -> tuple[str, str]:
        """
        抓取文章頁面，取得發佈時間與摘要。
        回傳 (pub_time_str, summary)，失敗時回傳 ('', '')
        """
        try:
            resp = requests.get(url, headers=self.HEADERS,
                                timeout=15, verify=False, allow_redirects=True)
            resp.raise_for_status()
            html = resp.text

            # ── 發佈時間：常見格式 2026-03-04 或 2026/03/04
            time_match = re.search(
                r'(\d{4}[-/]\d{2}[-/]\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)',
                html
            )
            pub_str = time_match.group(1).replace('/', '-') if time_match else ''

            # ── 摘要：抓 <p> 標籤內文，取前 300 字
            paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html,
                                    re.IGNORECASE | re.DOTALL)
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

            return pub_str, summary
        except Exception as e:
            logger.debug(f"      壹航運文章抓取失敗: {url} → {e}")
            return '', ''

    def _parse_pub_time(self, pub_str: str) -> datetime | None:
        if not pub_str:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(pub_str.strip(), fmt)
                return dt.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
            except ValueError:
                continue
        return None

    def fetch(self, scraper_ref) -> list[dict]:
        """
        主爬取入口。scraper_ref 傳入 NewsRssScraper 實例，
        借用其 _match_keywords / _classify_incident 方法。
        """
        results = []
        cutoff  = datetime.now(tz=timezone.utc) - timedelta(hours=self.hours_back)

        logger.info("\n  📡 [中文媒體][zh-CN] 壹航運（HTML 爬蟲）")

        # Step 1：從各入口頁收集文章連結
        all_articles: list[dict] = []
        seen_urls_local: set     = set()

        for section_url in self.SECTION_URLS:
            logger.info(f"    🔗 {section_url}")
            try:
                resp = requests.get(section_url, headers=self.HEADERS,
                                    timeout=20, verify=False)
                resp.raise_for_status()
                for art in self._extract_articles(resp.text):
                    if art['url'] not in seen_urls_local:
                        seen_urls_local.add(art['url'])
                        all_articles.append(art)
            except Exception as e:
                logger.warning(f"    ⚠️  壹航運入口頁失敗: {section_url} → {e}")

        logger.info(f"    📊 共發現 {len(all_articles)} 篇候選文章")

        # Step 2：逐篇過濾關鍵字（先用標題快篩，減少不必要的 HTTP 請求）
        matched_count = skipped_kw = skipped_time = skipped_dup = 0

        for art in all_articles:
            url   = art['url']
            title = art['title']

            if url in self.seen_urls:
                skipped_dup += 1
                continue

            # 標題快篩（不符合直接跳過，不發 HTTP）
            title_matched = scraper_ref._match_keywords(title, '')
            if not title_matched:
                skipped_kw += 1
                continue

            # 抓文章詳情（時間 + 摘要）
            pub_str, summary = self._fetch_article_detail(url)
            pub_time         = self._parse_pub_time(pub_str)

            if pub_time is not None and pub_time < cutoff:
                skipped_time += 1
                continue

            # 用完整內容再比對一次關鍵字
            matched = scraper_ref._match_keywords(title, summary)
            if not matched:
                skipped_kw += 1
                continue

            self.seen_urls.add(url)
            incident_cat = scraper_ref._classify_incident(title, summary)
            pub_display  = (
                pub_time.strftime('%Y-%m-%d %H:%M UTC')
                if pub_time else '時間未知'
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
            f"  📋 壹航運 | 候選 {len(all_articles)} | "
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
        title_lower = title.lower()
        for term in TITLE_SHIPPING_TERMS:
            if term in title_lower:
                return True
        body_hits = sum(
            1 for term in BODY_SHIPPING_TERMS
            if term in (title + " " + summary).lower()
        )
        return body_hits >= 2

    # ── 情境分類：依最高優先級的命中關鍵字決定 ──
    def _classify_incident(self, title: str, summary: str) -> str:
        full_lower = (title + " " + summary).lower()
        best_cat   = "GEN"
        best_pri   = INCIDENT_CATEGORIES["GEN"]["priority"]
        for kw in self.keywords:
            if kw.lower() in full_lower:
                cat = INCIDENT_KEYWORD_MAP.get(kw.lower(), "GEN")
                pri = INCIDENT_CATEGORIES[cat]["priority"]
                if pri < best_pri:
                    best_pri = pri
                    best_cat = cat
        return best_cat

    # ── 關鍵字比對（含語境驗證）──
    def _match_keywords(self, title: str, summary: str) -> list[tuple]:
        if not self._validate_shipping_context(title, summary):
            return []
        full_lower = (title + " " + summary).lower()
        matched, seen_kw = [], set()
        for kw in self.keywords:
            if kw.lower() in full_lower and kw not in seen_kw:
                cat = INCIDENT_KEYWORD_MAP.get(kw.lower(), "GEN")
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
        try:
            resp = requests.get(url, headers=headers,
                                timeout=20, verify=False, allow_redirects=True)
            resp.raise_for_status()
            if len(resp.content) < 100:
                logger.warning(f"    ⚠️  回應過短 ({len(resp.content)} bytes)")
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
        # ── HTML 爬蟲來源跳過（由 fetch_all 另行處理）──
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

                matched = self._match_keywords(title, summary)
                if not matched:
                    if not self._validate_shipping_context(title, summary):
                        skipped_ctx += 1
                    else:
                        skipped_kw += 1
                    continue

                if link:
                    self.seen_urls.add(link)

                summary_clean = re.sub(r'<[^>]+>', '', summary).strip()[:300]
                if len(summary_clean) == 300:
                    summary_clean += "..."

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
                summary     = re.sub(r'<[^>]+>', '', content_raw).strip()[:300]
                if len(summary) == 300:
                    summary += "..."

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

                matched = self._match_keywords(title, summary)
                if not matched:
                    if not self._validate_shipping_context(title, summary):
                        skipped_ctx += 1
                    continue

                if link:
                    self.seen_urls.add(link)

                results.append(
                    self._build_item(source, title, summary, link, pub_time, matched)
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

        # ── RSS 來源（跳過 _html_scraper 標記的）──
        for source in self.sources:
            all_news.extend(self.fetch_from_source(source))

        # ── 壹航運 HTML 爬蟲 ──
        oneshipping_scraper = OneShippingScraper(
            keywords   = self.keywords,
            hours_back = self.hours_back,
        )
        all_news.extend(oneshipping_scraper.fetch(self))

        # ── 鉅亨網 JSON API ──
        for cnyes_source in self.cnyes_sources:
            all_news.extend(self.fetch_from_cnyes(cnyes_source))

        # 依時間排序
        all_news.sort(
            key=lambda x: x['published'] if x['published'] != '時間未知' else '0000',
            reverse=True
        )

        # ── 依來源媒體分類 ──
        zh_tw_news    = [n for n in all_news if n['source_category'] == '中文媒體' and n['source_lang'] == 'zh-TW']
        zh_cn_news    = [n for n in all_news if n['source_category'] == '中文媒體' and n['source_lang'] == 'zh-CN']
        shipping_news = [n for n in all_news if n['source_category'] == '航運專業']
        intl_news     = [n for n in all_news if n['source_category'] == '國際媒體']

        # ── 依五大情境分類 ──
        cat1_news = [n for n in all_news if n['incident_cat'] == 'CAT1']
        cat2_news = [n for n in all_news if n['incident_cat'] == 'CAT2']
        cat3_news = [n for n in all_news if n['incident_cat'] == 'CAT3']
        cat4_news = [n for n in all_news if n['incident_cat'] == 'CAT4']
        cat5_news = [n for n in all_news if n['incident_cat'] == 'CAT5']
        gen_news  = [n for n in all_news if n['incident_cat'] == 'GEN']

        logger.info(
            f"\n{'='*60}\n"
            f"📊 最終結果（媒體分類）:\n"
            f"   🇹🇼 台灣媒體: {len(zh_tw_news)} 筆\n"
            f"   🇨🇳 大陸媒體: {len(zh_cn_news)} 筆\n"
            f"   🚢 航運專業: {len(shipping_news)} 筆\n"
            f"   🌐 國際媒體: {len(intl_news)} 筆\n"
            f"   📰 總計:     {len(all_news)} 筆\n"
            f"\n📊 最終結果（情境分類）:\n"
            f"   💥 CAT1 船舶於波斯灣含荷姆茲海峽週遭被攻擊事件: {len(cat1_news)} 筆\n"
            f"   🎯 CAT2 海灣國家及美軍基地被攻擊事件:     {len(cat2_news)} 筆\n"
            f"   💣 CAT3 伊朗已採取水雷封鎖:          {len(cat3_news)} 筆\n"
            f"   🚀 CAT4 紅海/曼德海峽胡塞含伊朗攻擊事件:         {len(cat4_news)} 筆\n"
            f"   🔀 CAT5 航商宣佈採取繞航措施及波斯灣內避難點:         {len(cat5_news)} 筆\n"
            f"   🚢 GEN  一般航運新聞動態:           {len(gen_news)} 筆\n"
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
            'gen':      gen_news,
        }


# ══════════════════════════════════════════════════════════════
# Email 發送器  v5.1  —  純情境版面
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

    # ──────────────────────────────────────────────────────────
    # 發送
    # ──────────────────────────────────────────────────────────
    def send(self, news_data: dict, run_time: datetime) -> bool:
        if not self.enabled:
            return False
        if len(news_data.get('all', [])) == 0:
            logger.info("ℹ️  無相關新聞，跳過發送")
            return False
        try:
            tpe_time = run_time.astimezone(timezone(timedelta(hours=8)))
            subject  = (
                f"Maritime Intel Alert "
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
    # 單張新聞卡片（精簡版，突出標題）
    # ──────────────────────────────────────────────────────────
    @staticmethod
    def _render_card(item: dict) -> str:
        cat_cfg      = INCIDENT_CATEGORIES.get(item.get('incident_cat', 'GEN'),
                                               INCIDENT_CATEGORIES['GEN'])
        border_color = cat_cfg['color']

        # 時間格式化
        pub = item['published']
        if pub != '時間未知':
            try:
                dt  = datetime.strptime(pub, '%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc)
                pub = dt.astimezone(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')
            except Exception:
                pass

        # 語言標籤
        lang      = item.get('source_lang', 'en')
        lang_bg   = "#0369a1" if lang == "en" else "#047857"
        lang_text = "EN" if lang == "en" else "中文"

        safe_title   = (item['title']
                        .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
        safe_summary = (item['summary']
                        .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

        # 命中關鍵字（最多 3 個，只顯示關鍵字文字）
        kw_cells = ""
        for kw, _label, color in item['matched'][:3]:
            kw_cells += (
                f'<td bgcolor="{color}" style="padding:2px 8px;">'
                f'<font face="Arial,Microsoft JhengHei,sans-serif" size="1" color="#fff">'
                f'{kw}</font></td><td width="4"></td>'
            )

        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#ffffff" style="margin-bottom:8px;border-bottom:1px solid #e2e8f0;">
<tr>
  <!-- 左側色條 -->
  <td width="4" bgcolor="{border_color}" style="padding:0;">&nbsp;</td>
  <td style="padding:12px 14px;">

    <!-- 頂列：來源 + 語言 + 時間 -->
    <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
      <td align="left" valign="middle">
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
          {item['source_icon']} {item['source_name']}
        </font>
        &nbsp;
        <table border="0" cellpadding="0" cellspacing="0" style="display:inline-table;"><tr>
          <td bgcolor="{lang_bg}" style="padding:1px 6px;">
            <font face="Arial,sans-serif" size="1" color="#fff"><b>{lang_text}</b></font>
          </td>
        </tr></table>
      </td>
      <td align="right" valign="middle">
        <font face="Arial,sans-serif" size="1" color="#94a3b8">🕐 {pub}</font>
      </td>
    </tr></table>

    <!-- 標題 -->
    <table width="100%" border="0" cellpadding="0" cellspacing="0"
           style="margin-top:6px;"><tr><td>
      <a href="{item['link']}" target="_blank" style="text-decoration:none;">
        <font face="Microsoft JhengHei,Arial,sans-serif" size="3" color="#0f172a">
          <b>{safe_title}</b>
        </font>
      </a>
    </td></tr></table>

    <!-- 摘要 -->
    <table width="100%" border="0" cellpadding="8" cellspacing="0"
           bgcolor="#f8fafc" style="margin-top:8px;"><tr><td>
      <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
        {safe_summary or '（無摘要）'}
      </font>
    </td></tr></table>

    <!-- 底列：關鍵字 + 閱讀按鈕 -->
    <table width="100%" border="0" cellpadding="0" cellspacing="0"
           style="margin-top:8px;"><tr>
      <td align="left" valign="middle">
        <table border="0" cellpadding="0" cellspacing="0"><tr>
          {kw_cells}
        </tr></table>
      </td>
      <td align="right" valign="middle">
        <table border="0" cellpadding="6" cellspacing="0"
               bgcolor="{border_color}"><tr><td>
          <a href="{item['link']}" target="_blank" style="text-decoration:none;">
            <font face="Arial,sans-serif" size="1" color="#ffffff">
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
    # 情境區塊（含標題列 + 所有卡片）
    # ──────────────────────────────────────────────────────────
    @staticmethod
    def _render_incident_section(cat_key: str, news_list: list) -> str:
        cfg = INCIDENT_CATEGORIES[cat_key]

        if not news_list:
            return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:6px;">
  <tr>
    <td width="5" bgcolor="{cfg['color']}">&nbsp;</td>
    <td bgcolor="{cfg['bg']}" style="padding:11px 16px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif"
                size="2" color="{cfg['color']}">
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

        # 篇數標籤用深色實色背景（避免 rgba 在 email 失效）
        darker = {
            "#dc2626": "#991b1b",
            "#b45309": "#78350f",
            "#7c3aed": "#4c1d95",
            "#0369a1": "#0c4a6e",
            "#047857": "#064e3b",
            "#475569": "#1e293b",
        }
        count_bg = darker.get(cfg['color'], "#1e293b")

        return f"""
<table width="100%" border="0" cellpadding="0" cellspacing="0"
       style="margin-bottom:14px;">
  <!-- 情境標題列 -->
  <tr>
    <td bgcolor="{cfg['color']}" style="padding:11px 16px;">
      <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
        <td align="left" valign="middle">
          <font face="Microsoft JhengHei,Arial,sans-serif"
                size="3" color="#ffffff">
            <b>{cfg['icon']}&nbsp;{cfg['label']}</b>
          </font>
        </td>
        <td align="right" valign="middle" width="60">
          <table border="0" cellpadding="5" cellspacing="0"
                 bgcolor="{count_bg}"><tr><td align="center">
            <font face="Arial,sans-serif" size="2" color="#ffffff">
              <b>{len(news_list)} 則</b>
            </font>
          </td></tr></table>
        </td>
      </tr></table>
    </td>
  </tr>
  <!-- 新聞卡片區 -->
  <tr>
    <td bgcolor="{cfg['bg']}" style="padding:10px 12px;">
      {cards}
    </td>
  </tr>
</table>"""

    # ──────────────────────────────────────────────────────────
    # 主 HTML 生成
    # ──────────────────────────────────────────────────────────
    def _generate_html(self, news_data: dict, run_time: datetime) -> str:
        tpe_str       = run_time.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
        total_sources = len(RSS_SOURCES) + len(CNYES_SOURCES)
        total_news    = len(news_data['all'])

        # ── 五大情境 + GEN 區塊 ──
        cat_order    = ['CAT1', 'CAT2', 'CAT3', 'CAT4', 'CAT5', 'GEN']
        cat_sections = "".join(
            self._render_incident_section(k, news_data.get(k.lower(), []))
            for k in cat_order
        )

        # ── 統計列：數字 + 文字分兩行，格子夠高 ──
        def _stat_cell(cat_key: str, width_pct: str) -> str:
            cfg   = INCIDENT_CATEGORIES[cat_key]
            count = len(news_data.get(cat_key.lower(), []))
            if count > 0:
                cell_bg  = cfg['color']
                num_fg   = "#ffffff"
                label_fg = "#ffffff"
                num_html = f"<b>{count}</b>"
            else:
                cell_bg  = "#334155"
                num_fg   = "#64748b"
                label_fg = "#475569"
                num_html = "0"
            # 截短標籤避免換行
            short_labels = {
                "CAT1": "船舶於波斯灣含荷姆茲海峽週遭被攻擊事件",
                "CAT2": "海灣國家及美軍基地被攻擊事件",
                "CAT3": "伊朗已採取水雷封鎖",
                "CAT4": "紅海/曼德海峽胡塞含伊朗攻擊事件",
                "CAT5": "航商宣佈採取繞航措施及波斯灣內避難點",
                "GEN":  "其他航運新聞",
            }
            short = short_labels.get(cat_key, cat_key)
            return f"""
<td align="center" bgcolor="{cell_bg}"
    style="padding:14px 6px;width:{width_pct};border-right:1px solid #1e293b;">
  <font face="Arial,sans-serif" size="5" color="{num_fg}">{num_html}</font><br>
  <font face="Arial,sans-serif" size="1" color="{num_fg}">{cfg['icon']}</font><br>
  <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="{label_fg}">{short}</font>
</td>"""

        stat_cells = "".join(
            _stat_cell(k, "14%") for k in cat_order
        )

        # ── 監控來源網格 ──
        source_grid = self._render_source_grid()

        # ── 本次命中來源 ──
        source_stats: dict = {}
        for item in news_data.get('all', []):
            key = f"{item['source_icon']} {item['source_name']}"
            source_stats[key] = source_stats.get(key, 0) + 1
        hit_rows = "".join(
            f'<tr>'
            f'<td bgcolor="#f8fafc" style="padding:7px 14px;border-bottom:1px solid #e2e8f0;">'
            f'<font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#475569">{s}</font>'
            f'</td>'
            f'<td bgcolor="#f8fafc" style="padding:7px 14px;border-bottom:1px solid #e2e8f0;" align="right" width="50">'
            f'<font face="Arial,sans-serif" size="2" color="#3b82f6"><b>{c}</b></font>'
            f'</td></tr>'
            for s, c in sorted(source_stats.items(), key=lambda x: -x[1])
        ) or (
            '<tr><td colspan="2" style="padding:12px 14px;">'
            '<font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#94a3b8">'
            '本次無命中來源</font></td></tr>'
        )

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Maritime Intel Alert</title></head>
<body bgcolor="#cbd5e1" style="margin:0;padding:0;">
<table width="100%" border="0" cellpadding="16" cellspacing="0" bgcolor="#cbd5e1">
<tr><td align="center" valign="top">
<table width="700" border="0" cellpadding="0" cellspacing="0" bgcolor="#ffffff">

  <!-- ══ HEADER ══ -->
  <tr><td bgcolor="#0f172a">
    <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
      <td style="padding:24px 24px 20px 24px;" valign="middle">
        <font face="Microsoft JhengHei,Arial,sans-serif" size="5" color="#f8fafc">
          <b>🚢&nbsp;Maritime Intel</b>
        </font><br>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#64748b">
          航運安全情報快報
        </font>
      </td>
      <td style="padding:24px 24px 20px 0;" align="right" valign="middle">
        <font face="Arial,sans-serif" size="2" color="#94a3b8">
          {tpe_str}&nbsp;台北時間
        </font><br><br>
        <table border="0" cellpadding="6" cellspacing="0" bgcolor="#1e293b"><tr><td>
          <font face="Arial,sans-serif" size="1" color="#64748b">
            來源&nbsp;{total_sources}&nbsp;個&nbsp;&nbsp;|&nbsp;&nbsp;關鍵字&nbsp;{len(ALL_KEYWORDS)}&nbsp;個
          </font>
        </td></tr></table>
      </td>
    </tr></table>
  </td></tr>

  <!-- ══ 監控來源清單 ══ -->
  <tr><td bgcolor="#f1f5f9" style="padding:0;">
    <table width="100%" border="0" cellpadding="0" cellspacing="0">
      <tr><td bgcolor="#334155" style="padding:11px 18px;">
        <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#f1f5f9">
          <b>📡&nbsp;新聞來源清單</b>
        </font>
        &nbsp;&nbsp;
        <font face="Arial,sans-serif" size="1" color="#94a3b8">
          共&nbsp;{total_sources}&nbsp;個&nbsp;·&nbsp;RSS&nbsp;+&nbsp;JSON&nbsp;API
        </font>
      </td></tr>
      <tr><td style="padding:14px 16px;">
        {source_grid}
      </td></tr>
    </table>
  </td></tr>

  <!-- ══ 快速統計列（數字 + icon + 短標籤）══ -->
  <tr><td bgcolor="#0f172a" style="padding:0;">
    <table width="100%" border="0" cellpadding="0" cellspacing="0"><tr>
      <!-- TOTAL -->
      <td align="center" bgcolor="#1e293b"
          style="padding:14px 6px;width:16%;border-right:1px solid #0f172a;">
        <font face="Arial,sans-serif" size="5" color="#f8fafc"><b>{total_news}</b></font><br>
        <font face="Arial,sans-serif" size="1" color="#64748b">📰</font><br>
        <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#64748b">總計</font>
      </td>
      {stat_cells}
    </tr></table>
  </td></tr>

  <!-- ══ 情境說明帶 ══ -->
  <tr><td bgcolor="#1e293b" style="padding:12px 16px;">
    <table width="100%" border="0" cellpadding="0" cellspacing="0">
      <tr>
        <td width="5" bgcolor="#dc2626">&nbsp;</td>
        <td bgcolor="#1e2d3d" style="padding:7px 12px;">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#fca5a5">
            <b>💥&nbsp;CAT1</b>
          </font>
          &nbsp;
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#e2e8f0">
            船舶於波斯灣/荷姆茲海峽週遭被攻擊事件
          </font>
        </td>
      </tr>
      <tr><td colspan="2" height="3"></td></tr>
      <tr>
        <td width="5" bgcolor="#b45309">&nbsp;</td>
        <td bgcolor="#1e2d3d" style="padding:7px 12px;">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#fcd34d">
            <b>🎯&nbsp;CAT2</b>
          </font>
          &nbsp;
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#e2e8f0">
            海灣國家及美軍基地被攻擊事件
          </font>
        </td>
      </tr>
      <tr><td colspan="2" height="3"></td></tr>
      <tr>
        <td width="5" bgcolor="#7c3aed">&nbsp;</td>
        <td bgcolor="#1e2d3d" style="padding:7px 12px;">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#c4b5fd">
            <b>💣&nbsp;CAT3</b>
          </font>
          &nbsp;
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#e2e8f0">
            伊朗已採取水雷封鎖
          </font>
        </td>
      </tr>
      <tr><td colspan="2" height="3"></td></tr>
      <tr>
        <td width="5" bgcolor="#0369a1">&nbsp;</td>
        <td bgcolor="#1e2d3d" style="padding:7px 12px;">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#7dd3fc">
            <b>🚀&nbsp;CAT4</b>
          </font>
          &nbsp;
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#e2e8f0">
            紅海/曼德海峽胡塞含伊朗攻擊事件
          </font>
        </td>
      </tr>
      <tr><td colspan="2" height="3"></td></tr>
      <tr>
        <td width="5" bgcolor="#047857">&nbsp;</td>
        <td bgcolor="#1e2d3d" style="padding:7px 12px;">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#6ee7b7">
            <b>🔀&nbsp;CAT5</b>
          </font>
          &nbsp;
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#e2e8f0">
            航商宣佈採取繞航措施及波斯灣內避難點
          </font>
        </td>
      </tr>
      <tr><td colspan="2" height="3"></td></tr>
      <tr>
        <td width="5" bgcolor="#475569">&nbsp;</td>
        <td bgcolor="#1e2d3d" style="padding:7px 12px;">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#94a3b8">
            <b>🚢&nbsp;GEN</b>
          </font>
          &nbsp;
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#e2e8f0">
            其他航運新聞動態
          </font>
        </td>
      </tr>
    </table>
  </td></tr>

  <!-- ══ 五大情境新聞主體 ══ -->
  <tr><td bgcolor="#f1f5f9" style="padding:16px;">
    {cat_sections}
  </td></tr>

  <!-- ══ 本次命中來源 ══ -->
  <tr><td bgcolor="#ffffff" style="padding:16px;">
    <table width="100%" border="0" cellpadding="0" cellspacing="0">
      <tr>
        <td bgcolor="#334155" style="padding:10px 14px;">
          <font face="Microsoft JhengHei,Arial,sans-serif" size="2" color="#f1f5f9">
            <b>📊&nbsp;本次新聞來源</b>
          </font>
          &nbsp;
          <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#94a3b8">
            （僅列出有新增新聞的來源）
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

  <!-- ══ FOOTER ══ -->
  <tr><td bgcolor="#0f172a" align="center" style="padding:20px 16px;">
    <font face="Microsoft JhengHei,Arial,sans-serif" size="1" color="#475569">
      此新聞為自動發送&nbsp;·&nbsp;請勿直接回覆
    </font><br><br>
    <font face="Arial,sans-serif" size="1" color="#334155">
      Maritime Intel System v5.1&nbsp;·&nbsp;Powered by Python &amp; GitHub Actions
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
    logger.info("🚢 航運安全監控系統 v5.0")
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

    
