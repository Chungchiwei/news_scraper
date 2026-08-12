# 海事航運新聞智慧監控系統 — 主管決策導向全面優化任務

你現在是一位同時具備以下專業能力的 Senior System Architect：

* Python Backend Engineer
* Maritime Safety / Marine Operations Specialist
* Shipping Intelligence Analyst
* Fleet Risk Management Specialist
* Maritime OSINT Analyst
* Corporate Executive Dashboard / Email UX Designer

你的任務不是重新製作一個普通新聞爬蟲，而是仔細閱讀並重構我目前既有的：

**「海事航運新聞監控系統 Maritime News Monitoring System」**

最終目標是將目前的：

「新聞抓取工具」

升級為：

# Maritime Intelligence & Fleet Risk Briefing System

海事情報暨船隊風險智慧快報系統

使用對象主要為：

* 船公司高階主管
* 海技／Marine Department
* Fleet Management
* Safety / Risk Management
* Marine Operations
* 相關航線及營運管理人員

---

# 一、第一階段：先完整 Audit，禁止直接重寫

請先完整閱讀專案內所有檔案，包括但不限於：

* maritime_news.py
* news_scraper.py
* email_sender.py
* keywords_config.json
* requirements.txt
* news_scraper.log
* GitHub Actions workflow（若專案中存在）
* 其他 config / helper / workflow

必須先理解：

1. 現有新聞來源
2. RSS / API / HTML scraper 架構
3. 中文／英文／簡體中文處理
4. 關鍵字比對
5. Shipping Context Validation
6. Finance Noise Filtering
7. Carrier Validation
8. Incident Classification
9. 時間篩選
10. URL Deduplication
11. Email HTML Rendering
12. SMTP 發信
13. GitHub Actions 執行方式
14. Logging
15. Error Handling
16. Backup URL / RSSHub fallback
17. Reddit / community source
18. 各新聞來源目前是否仍有作用

不要破壞目前可以正常使用的 crawler。

完成 Audit 後，先列出：

### A. 現有架構

### B. 可保留功能

### C. 技術債

### D. 資訊品質問題

### E. 主管閱讀體驗問題

### F. 建議重構架構

之後再開始修改。

---

# 二、核心設計理念

這套系統的核心問題不應再是：

> 「今天抓到了幾則新聞？」

而應該是：

> 「今天有哪幾件事情值得船公司主管注意？」

以及：

> 「這件事情跟船公司營運、船隊安全、航線與港口有什麼關係？」

最終系統必須做到：

NEWS

→ INFORMATION

→ EVENT

→ RISK

→ BUSINESS IMPACT

→ MANAGEMENT ATTENTION

---

# 三、禁止單純以發布時間排序

目前系統若主要依照 published time 由新至舊排序，請重新設計。

新增：

# Management Priority Score

每個 Event 計算 0–100 分。

建議構成：

## Severity — 30%

事件本身嚴重度。

例如：

30：

* Ship sinking / Total Loss
* Fatal casualty
* Major fire / explosion
* Vessel attack
* Major collision
* Strait / canal closure

20–25：

* Grounding
* Engine failure
* Cargo fire
* Piracy boarding
* Containers overboard
* Major port disruption

10–15：

* Port congestion
* Schedule disruption
* regulatory change

5：

* ordinary corporate news

---

## Fleet Relevance — 25%

判斷事件是否可能直接影響 liner shipping / WHL fleet。

考量：

* Container Ship
* Feeder / Liner Shipping
* WHL trading region
* Major port
* Major shipping lane
* Strait
* Canal
* Major liner competitor
* Container terminal
* Relevant regulation

若未來專案加入 WHL 船期、航線或港口資料，架構必須可以進一步支援：

VESSEL × ROUTE × PORT × EVENT

進行 relevance matching。

---

## Immediacy — 20%

依照：

* Breaking / ongoing
* < 6 hr
* < 24 hr
* < 72 hr
* historical/reference

判斷。

正在發生的事故、戰爭、航道封鎖必須高於一般產業新聞。

---

## Operational Impact — 15%

判斷是否可能影響：

* Navigation
* Berthing / Unberthing
* Port Call
* Cargo Operations
* Schedule Reliability
* Route Diversion
* Crew Safety
* Ship Security
* Insurance
* Cargo Acceptance
* Freight / Capacity
* Regulatory Compliance

---

## Source Confidence — 10%

建立來源可信度。

### Tier A — Official / Primary Source

例如：

* IMO
* Government
* Coast Guard
* Navy
* Port Authority
* UKMTO
* IMB
* Official Carrier Notice

### Tier B — Professional / Major Media

例如：

* Reuters
* Lloyd's List
* TradeWinds
* Maritime Executive
* gCaptain
* Safety4Sea

### Tier C

一般新聞媒體或產業網站。

### Tier D

* Reddit
* Social Media
* forum
* 未驗證內容

Tier D 不可直接當成正式 confirmed intelligence。

顯示：

EARLY SIGNAL
Unverified / Awaiting Independent Confirmation

若同一事件得到 Tier A/B 來源交叉證實，可提高 confidence。

---

# 四、建立 Management Priority

根據 Management Priority Score 分成：

## 🔴 P1 — IMMEDIATE ATTENTION

主管應立即知道。

例如：

* Major maritime casualty
* Vessel attack
* Strait / canal closure
* Major container ship fire
* Fatal accident
* Major port shutdown
* War-related shipping disruption
* Serious incident involving Wan Hai

---

## 🟠 P2 — MANAGEMENT WATCH

可能影響營運。

例如：

* Port congestion
* Terminal disruption
* Significant grounding
* Cargo safety issue
* Sanctions
* Regulatory change
* Major competitor operational changes

---

## 🟡 P3 — INDUSTRY WATCH

產業趨勢：

* Alliance
* Capacity
* Freight rates
* New services
* Blank sailing
* Carrier strategy

---

## ⚪ P4 — REFERENCE

一般參考資訊。

---

# 五、Incident Category 與 Risk Priority 必須拆開

不得把：

CAT1 = Priority 1

CAT2 = Priority 2

視為事故嚴重度。

Incident Category 只代表：

「發生什麼事情？」

Risk Priority 才代表：

「主管有多需要知道？」

重新建立以下分類：

SAFETY
海事事故與船舶安全

SECURITY
海盜、戰爭、武裝攻擊、劫持、偷渡、毒品及 Maritime Security

OPERATIONS
港口、碼頭、運河、航道、塞港、罷工、封港、靠泊及航線營運

REGULATORY
IMO、MARPOL、SOLAS、PSC、制裁、環保規定

CREW
Crew casualty / MOB / SAR / Medevac

ENVIRONMENT
Oil spill / Pollution / Dangerous cargo environmental events

MARKET
Freight rate / Capacity / Alliance / Charter / Container market

COMPETITOR
MSC / Maersk / CMA CGM / COSCO / Evergreen / ONE /
Yang Ming / HMM / Hapag-Lloyd / PIL / Wan Hai 等航商營運資訊

OTHER
其他航運資訊

保留原有 keyword config 能力，但重新整理資料結構，使：

category
severity
priority
relevance

互相獨立。

---

# 六、加入 Event Extraction

每篇有效新聞除了：

title
summary
source
published
link

新增：

event_type
severity
management_priority
management_score

vessel_name
vessel_type

carrier

location
country
region
port

sea_area
shipping_lane

incident_status

casualties
pollution
cargo_issue

operational_impact

fleet_relevance

source_tier
confidence

keywords

---

# 七、Event Clustering / Story Deduplication

目前 URL 去重不足。

不同媒體可能報導同一事件。

必須新增：

EventClusterer

可以使用：

* normalized title
* vessel name
* location
* event type
* publication time
* title token similarity

進行事件聚類。

例如：

Reuters
TradeWinds
Splash247
gCaptain

都報導：

MSC vessel collision Singapore Strait

Email 不應顯示四則新聞。

應顯示：

MSC Vessel Collision — Singapore Strait

Sources: 4

Reuters
TradeWinds
Splash247
gCaptain

並選擇一個 Primary Source。

其餘放：

Additional Sources (3)

---

# 八、中文主管摘要

對高 Priority Event 產生：

management_summary_zh

格式固定：

### 發生什麼事

1–2 句繁體中文。

### 為什麼值得注意

說明此事件對：

* 船舶
* 航線
* 港口
* 貨物
* 船員
* 航運市場

可能造成什麼影響。

### 船公司可能影響

例如：

* 航線繞航
* ETA 延誤
* 港口壅塞
* War Risk
* Cargo Risk
* Navigation Risk
* Crew Security
* Insurance
* Schedule Reliability

### 建議關注

只能使用：

Monitor
Review
Check
Confirm
Watch

等「情報監控建議」。

除非有足夠事實，不可捏造公司已採取的措施。

---

# 九、LLM 使用原則

不要讓 LLM 負責所有新聞篩選。

流程應採：

Crawler
↓
Rule Filter
↓
Context Validation
↓
Deduplication
↓
Event Clustering
↓
Risk Scoring
↓
Top Events
↓
LLM Management Summary

也就是：

先用 Python 規則大量過濾。

只將真正重要的 Top Events 送給 LLM。

原因：

* 降低 API 成本
* 提高執行速度
* 降低 hallucination
* 保留 deterministic behavior

若目前專案沒有 LLM API：

請先建立：

IntelligenceAnalyzer Interface

並提供：

RuleBasedAnalyzer

作為預設版本。

LLMAnalyzer 保留 optional adapter。

不得因沒有 API Key 使主系統無法執行。

---

# 十、Email 必須重新設計成 Executive Briefing

Email 不應再像新聞資料庫。

設計成：

# MARITIME INTELLIGENCE BRIEF

Fleet Risk Management

Updated:
YYYY-MM-DD HH:mm TPE

---

## 第一區：EXECUTIVE OVERVIEW

主管開 Email 第一眼必須知道：

TODAY'S RISK LEVEL

例如：

🔴 HIGH
🟠 ELEVATED
🟢 NORMAL

並顯示：

P1 Events
P2 Events
Monitored Events
Sources Checked

---

# TOP MANAGEMENT ATTENTION

最多顯示 3–5 件。

例如：

🔴 P1

RED SEA — CONTAINER VESSEL ATTACKED

發生什麼事：
……

營運影響：
Route / Security / War Risk

WHY IT MATTERS:
……

WHL RELEVANCE:
High

CONFIDENCE:
High

Sources:
Reuters + UKMTO + TradeWinds

[Read Primary Source]

---

## 第二區

# FLEET SAFETY & CASUALTY

---

## 第三區

# SECURITY & GEOPOLITICAL RISK

Red Sea
Gulf of Aden
Strait of Hormuz
Persian Gulf
Malacca Strait
etc.

---

## 第四區

# PORT & OPERATIONAL DISRUPTION

例如：

Port Closure
Terminal Accident
Port Strike
Congestion
Canal Restriction

---

## 第五區

# REGULATORY WATCH

IMO
SOLAS
MARPOL
PSC
Sanctions
Environmental Regulations

---

## 第六區

# LINER & COMPETITOR WATCH

MSC
Maersk
CMA CGM
COSCO
Evergreen
ONE
Yang Ming
Hapag-Lloyd
HMM
PIL
Wan Hai

但只顯示具有營運意義的內容。

不要把每一篇航商 PR News 都當成重要新聞。

---

## 第七區

# MARKET WATCH

只保留有決策價值的：

Freight Rate
Capacity
Alliance
Blank Sailing
Port Omission
Container Supply
Schedule Reliability

---

# 十一、30 秒閱讀原則

主管 Email 必須符合：

5 秒：
知道今天 Risk Level。

15 秒：
知道 Top 3 Events。

30 秒：
知道這些事件為什麼值得注意。

若需要更多內容，再向下閱讀。

禁止第一屏出現大量：

* Source list
* 技術資訊
* URL
* crawler detail
* 大量 keyword badge

這些移到 Email 最底部或 technical report。

---

# 十二、Email Card 顯示資訊

每張卡建議：

[Priority Badge]

Headline

中文 Management Summary

WHY IT MATTERS

Impact Tags：

NAVIGATION
PORT
SECURITY
CREW
CARGO
SCHEDULE
MARKET
REGULATORY

Location

Vessel / Carrier

Published

Sources × N

Confidence

Read More

不要一次顯示 300 字英文 RSS Summary。

原始 summary 可放在 collapsed/detail data，但主管 Email 只顯示簡潔內容。

---

# 十三、Email Subject 重新設計

一般狀態：

[Maritime Intelligence] Daily Brief | 08/10 | P1:0 P2:3

存在重大事件：

[🔴 Maritime Alert] P1 Event | Red Sea Vessel Attack

如果沒有高風險事件：

[Maritime Intelligence] No Major Fleet Risk | 08/10

避免單純：

Maritime News Alert — 27則

主管不在乎有幾則新聞，而在乎有沒有重大事件。

---

# 十四、系統健康度

新增：

SourceHealthManager

每個 source 保存：

source_name
status
last_success
last_failure
http_status
latency
consecutive_failures
success_rate
working_url

若主要 URL 失敗，自動 fallback：

Primary
→ Backup
→ Extra URL

系統 log 清楚記錄。

若來源連續失敗 3 次，標記：

DEGRADED

若失敗 5 次：

DOWN

但：

不要把 Source Health 詳細資料放在主管新聞 Email。

只有 Admin Email 或 log 顯示。

---

# 十五、Email 發送 Reliability

檢查 SMTP：

* timeout
* authentication failure
* network failure
* retry

加入有限次 retry，例如：

attempt 1
wait
attempt 2
wait
attempt 3

若最終失敗：

exit code 必須能讓 GitHub Actions 判斷 failure。

不可出現：

Email failed

但程式最後仍然回報整體 success。

---

# 十六、安全性

不得：

ssl._create_default_https_context = ssl._create_unverified_context

作為全域永久設定。

不得把：

MAIL_USER
MAIL_PASSWORD
SMTP PASSWORD

直接 hardcode 在 repository。

必須改回：

Environment Variables

或：

GitHub Secrets

例如：

MAIL_USER
MAIL_PASSWORD
TARGET_EMAIL

若沒有設定，明確顯示 configuration error。

不得把 credential 寫入 log。

---

# 十七、News Source 管理

將來源定義移至：

sources_config.json

或：

config/sources.json

每個 source：

{
"id": "",
"name": "",
"category": "",
"type": "rss/html/api/community",
"url": "",
"backup_urls": [],
"language": "",
"source_tier": "A/B/C/D",
"enabled": true
}

避免大量來源 hardcode 在主程式。

---

# 十八、Config 重構

建議結構：

config/

sources.json
keywords.json
risk_rules.json
carriers.json
regions.json
settings.json

---

# 十九、程式架構

建議重構：

src/

main.py

scrapers/
base.py
rss.py
html.py
cnyes.py
reddit.py

processing/
normalizer.py
validator.py
classifier.py
deduplicator.py
event_clusterer.py

intelligence/
extractor.py
risk_scorer.py
relevance.py
summarizer.py

output/
email_renderer.py
email_sender.py

monitoring/
source_health.py

config/

tests/

不得為重構而過度工程化。

優先：

可讀性
穩定性
容易維護
容易新增新聞來源

---

# 二十、建立 Persistent Event Memory

現在每次 GitHub Actions 執行不可只靠記憶體中的 seen_urls。

建立：

data/event_history.json

或 SQLite：

news_history.db

保存：

article hash
url
normalized title
event cluster
first_seen
last_seen
sent_at
priority

避免：

同一事件每 6 小時重複寄給主管。

規則：

NEW EVENT
→ 正常通知

EXISTING EVENT + NO MATERIAL CHANGE
→ 不再通知

EXISTING EVENT + MATERIAL UPDATE
→ 顯示：

UPDATE

例如：

08:00
Ship grounding

12:00
Tugs deployed

16:00
Vessel refloated

應視為同一事件的更新。

---

# 二十一、測試要求

建立最基本的 automated tests：

test_keyword_matching

test_shipping_context

test_finance_noise

test_classification

test_risk_scoring

test_duplicate_url

test_event_clustering

test_time_filter

test_source_tier

test_email_render

使用固定 mock data。

測試不能依賴真實網站才能通過。

---

# 二十二、UI / Email 視覺

整體採：

專業船公司企業風格。

主色：

Deep Navy
Port Blue
White
Light Gray

Risk Color：

RED = Immediate
ORANGE = Watch
YELLOW = Attention
GREEN = Normal

避免：

過多 emoji
彩虹色分類
過度花俏
像一般新聞電子報

應呈現：

Marine Operations Control Center
Fleet Risk Intelligence
Executive Briefing

的視覺感。

---

# 二十三、保留海事專業用語

使用正確 Maritime Terminology：

Collision
Allision
Grounding
Stranding
Foundering
Capsizing
Loss of Propulsion
Blackout
Engine Failure
Containers Overboard
Man Overboard
Medevac
Piracy
Armed Robbery
Stowaway
Drug Smuggling
Port Closure
Port Congestion
Canal Restriction
Blank Sailing
Port Omission
Schedule Reliability
War Risk
Navigation Warning

中文摘要使用繁體中文。

專業術語必要時：

中文（English）

例如：

失去推進能力（Loss of Propulsion）

---

# 二十四、不要捏造情報

這是一個 Risk Intelligence System。

任何摘要必須遵守：

Facts ≠ Assessment

清楚區分：

FACT
已被來源證實。

ASSESSMENT
系統根據資訊推估。

UNCONFIRMED
尚未交叉驗證。

不得因為新聞提及 Red Sea，就自行判定 Wan Hai vessel 正受到攻擊。

不得因為港口發生事件，就自行聲稱 Wan Hai schedule 已受到影響。

只能寫：

Potential impact
Possible disruption
Worth monitoring

除非資料來源有明確證據。

---

# 二十五、最終交付內容

修改完成後，請提供：

## 1. Current System Audit

說明原系統架構與問題。

## 2. New Architecture

說明新資料流程。

## 3. File Change List

每個檔案：

NEW
MODIFIED
REMOVED

並說明修改原因。

## 4. Risk Scoring Logic

完整說明。

## 5. Event Clustering Logic

完整說明。

## 6. Executive Email Design

完整說明 Email hierarchy。

## 7. Source Reliability

列出各來源 Tier。

## 8. Error Handling

說明 fallback / retry / health monitoring。

## 9. Security Review

檢查 credential、SSL、log。

## 10. Testing Results

提供測試結果。

## 11. Deployment Guide

包含：

Local Windows
GitHub Actions

需要設定哪些：

Environment Variables
Secrets

## 12. Before / After

說明主管收到的資訊從：

「新聞列表」

如何變成：

「管理情報」。

---

# 二十六、執行原則

非常重要：

1. 不要直接刪掉原本正常 crawler。
2. 先 Audit。
3. 一次逐模組重構。
4. 每階段都必須能執行。
5. 不可因單一新聞來源失敗讓整個 workflow failure。
6. 不可因 LLM API 不存在讓 crawler failure。
7. 所有新聞必須保留原始 Source URL。
8. 所有 AI summary 必須可以追溯原文。
9. 主管 Email 只顯示高價值資訊。
10. Technical detail 保留在 log/admin report。
11. 所有日期統一 UTC 儲存、TPE 顯示。
12. 不要為了視覺效果犧牲 Outlook / Gmail Email HTML 相容性。
13. 優先使用 table-based Email HTML，確保企業 Outlook 可正常顯示。
14. 修改前先建立 backup / git checkpoint。
15. 最終執行完整 regression test。

---

# 最終成功標準

這個專案完成後，主管不應該再看到：

「今天系統抓到 28 則新聞。」

而是看到：

MARITIME INTELLIGENCE BRIEF

Risk Level: ELEVATED

3 EVENTS REQUIRE ATTENTION

P1
Red Sea — Container Vessel Attacked

P2
Singapore — Terminal Operations Disrupted

P2
IMO — New Cargo Safety Requirement

並且每件事情都能在 20–30 秒內回答：

WHAT HAPPENED?

WHERE?

WHY DOES IT MATTER?

WHAT IS THE POTENTIAL OPERATIONAL IMPACT?

HOW RELEVANT IS IT TO OUR FLEET?

HOW RELIABLE IS THE INFORMATION?

WHAT SHOULD MANAGEMENT MONITOR NEXT?

這才是本次系統重構的最核心目標。
