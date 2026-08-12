# Phase 2 Completion Report — Maritime Intelligence Core

Phase 1（Security & Reliability Hardening）成果全數保留：`maritime_news.py` 為唯一主幹、`run.bat` 指向正確主程式、Email 帳密走環境變數、SMTP 重試、失敗正確回傳 exit code、`.env` 已排除 Git、移除全域 SSL 停用、`dotenv` import 順序已修正。以上本階段完全未變動。

Phase 2 把「新聞爬蟲」升級成「Article → Normalize → Context Validation → Event Extraction → Event Classification → Event Clustering → Risk Scoring → Management Priority → Intelligence Event」的事件式情報引擎。全程 rule-based，不依賴 LLM／外部服務，`news_scraper.py`（舊版）維持不動、僅供 legacy 參考。

---

## 1. Files Added

| 檔案 | 用途 |
|---|---|
| `models.py` | `NewsArticle` / `MaritimeEvent` dataclass，統一資料模型 |
| `risk_config.py` | `risk_rules.json` 共用載入工具（避免模組間循環 import） |
| `risk_rules.json` | 所有 threshold / weight / tier / keyword 字典（config-driven） |
| `event_extractor.py` | `normalize_title()` + `EventExtractor`（vessel/carrier/sea_area/event_type 抽取） |
| `carrier_news_filter.py` | `CarrierNewsFilter`（航商 PR 過濾） |
| `risk_scorer.py` | `RiskScorer`（五項 component 評分 + priority + critical override）、`sort_events()` |
| `event_clusterer.py` | `EventClusterer`（事件聚類 + primary source 選擇 + confidence） |
| `tests/conftest.py` | pytest fixture（rules/extractor/scorer/clusterer/mock article 工廠） |
| `tests/fixtures/articles.json` | 20 篇 mock 新聞，涵蓋 fire/collision/piracy/red sea attack/port closure/regulation/carrier PR（operational+fluff）/market/reddit rumor/duplicate event/own-fleet 等情境 |
| `tests/test_extraction.py` `test_carrier_filter.py` `test_risk_scoring.py` `test_clustering.py` `test_pipeline.py` | 單元測試（見 §9） |
| `PHASE2_REPORT.md` | 本報告 |

## 2. Files Modified

- **`maritime_news.py`**：新增 Phase 2 imports；新增 `build_articles_from_legacy()` / `build_compat_news_data()` / `print_intelligence_report()` / `run_intelligence_pipeline()`；`__main__` 在 `scraper.fetch_all()` 之後、`sender.send()` 之前插入 `run_intelligence_pipeline()`。**`fetch_from_source()` / `_download_rss()` / 所有 scraper class（含 HTML/Reddit）完全未改動**，符合「不修改已正常工作的 crawler」原則。
- **`requirements.txt`**：新增 `pytest`（標註為開發/測試用，非執行主程式必要）。

未變動：`email_sender.py`（Phase 1 已處理，本階段不動 Email UI）、`keywords_config.json`（舊版關鍵字完整保留）、`news_scraper.py`（legacy reference）、`run.bat`、`.env`、`.gitignore`。

## 3. Intelligence Pipeline

```
scraper.fetch_all()                      舊版 dict（完全不動）
        │
build_articles_from_legacy()             dict → NewsArticle，source_tier 依 risk_rules.json 查表
        │
CarrierNewsFilter.filter_articles()      航商 PR 過濾（僅套用於 航商動態 來源 / COMPETITOR 分類）
        │  ├─ KEEP_OPERATIONAL → 繼續
        │  ├─ KEEP_LOW_VALUE   → 保留但不升級（不確定時的預設）
        │  └─ DROP             → 直接濾除（純公關稿/得獎/贊助/週年慶）
        │
EventExtractor.enrich()                  逐篇：vessel_type / carrier / sea_area / event_type / normalized_title
RiskScorer.score_article()               逐篇：severity / relevance / immediacy / operational_impact / source_confidence
        │
EventClusterer.cluster()                 依 ±48hr 時間窗 + weighted signal score 聚類成 MaritimeEvent
        │
RiskScorer.score_events()                Event 層級彙整評分（severity/impact 取 cluster 內最大值，
        │                                 fleet_relevance 取 cluster 內最大值，source_confidence 依
        │                                 cluster 內 tier 組成重新計算，非僅 primary article）
sort_events()                            priority → score → last_updated（不再用 published time 當主序）
        │
print_intelligence_report()              Console 輸出（§8 範例）
        │
build_compat_news_data()                 legacy news_data 濾除 dropped PR 後原樣保留，
        │                                 外加 'events' / 'articles' 兩個新 key
sender.send()                            email_sender.py 完全不用改，正常運作
```

## 4. Risk Score Formula

`management_score = severity + fleet_relevance + immediacy + operational_impact + source_confidence`（上限 100）

| Component | 上限 | 判斷方式 |
|---|---|---|
| Severity | 30 | `risk_rules.json.severity_score_tiers`（30/25/20/15/10/5 六級關鍵字），無命中則退回 `severity_default_by_event_type` |
| Fleet Relevance | 25 | 單一分支回傳（不疊加）：own fleet(萬海)=25 → 貨櫃船+主要航商/航線=20 → 主要航商或主要海域=15 → 一般商船=10 → 間接市場=5 → 無關=0 |
| Immediacy | 20 | ≤6hr=20 / ≤12hr=18 / ≤24hr=15 / ≤48hr=10 / ≤72hr=5 / >72hr=2 / 時間未知=5，命中 ongoing 關鍵字 +3（總分仍 cap 20） |
| Operational Impact | 15 | 關鍵字分級（15/12/10/7/4/0），並附帶輸出 `impact_tags`（NAVIGATION/PORT/TERMINAL/SCHEDULE/SECURITY/...） |
| Source Confidence | 10 | Article 層級依 Tier A=10/B=8/C=5/D=2；Event 層級依 cluster 內 tier 組成（A 存在=10；2+ 個 B=10；1 個 B=8；2+ 個 C=6；只有 D=2） |

**Management Priority**：P1 ≥80 / P2 ≥60 / P3 ≥40 / P4 <40（`priority_thresholds` in risk_rules.json）。

**Critical Override**：分數需先達 `min_score_to_consider`(55) 才會被考慮，符合下列任一條件即強制升級 P1：
1. own fleet（萬海）且 severity ≥15
2. event_type 為 SAFETY/SECURITY 且命中對應攻擊/沉沒關鍵字（`critical_override.event_type_triggers`）
3. 命中運河/海峽封鎖關鍵字

範例（CASE 7）：Wan Hai 貨櫃船火災，僅 Tier B 來源 → severity=25, fleet_relevance=25(own fleet), 兩者已使 override 條件成立 → 強制 P1，即使原始分數已經自然達到 90 分。

## 5. Event Clustering Logic

- 只比對 `published_at` 相差在 `time_window_hours`(48hr) 內的文章（任一方時間未知則不因時間排除）。
- Pairwise cluster score（`event_clusterer.py: pair_score()`）：
  - 相同船名（若兩者皆有）：**+50**（單獨即可達門檻）
  - 相同航商：+15
  - 相同 event_type：+15
  - 相同 sea_area：+15（same_location）+10（same_major_sea_area，因字典內全部海域皆屬主要海域）
  - 標題正規化後相似度（`difflib.SequenceMatcher`）≥0.75：+20
- **Threshold = 50**：貪婪聚類，新文章與現有各 cluster 內所有文章取最大 pair score，≥50 則併入，否則自成一群。
- **Primary Article 選擇**：Tier 排序（A>B>C>D）→ 摘要較完整（較長）→ 較早發布時間。Tier D（Reddit）在有 A/B/C 存在時永遠不會被選為 Primary。
- **Confidence Level**：Tier A 存在 或 2+ 獨立 Tier B → HIGH；1 個 Tier B 或 2+ 個 Tier C → MEDIUM；僅 Tier D 或無法交叉驗證 → LOW。

驗證（CASE 6）：MSC collision 與 Maersk grounding 同在新加坡海峽、時間相近，僅 event_type(+15) + location(+25) = 40 < 50 → 正確維持 2 個獨立 Event。

## 6. Source Tier Mapping

已針對 `RSS_SOURCES` / `CNYES_SOURCES` 現有全部來源逐一於 `risk_rules.json.source_tiers` 建立映射（非僅示範幾筆），原則：

- **Tier A**：11 家航商官方新聞 feed（Maersk/CMA CGM/Hapag-Lloyd/長榮/陽明/萬海/ONE/HMM/PIL/COSCO/OOCL）
- **Tier B**：Reuters/BBC/Al Jazeera/Guardian/AP、TradeWinds/Splash247/gCaptain/Maritime Exec/Hellenic 系列/Safety4Sea/Freightwaves/Lloyd's List、信德海事網、"MSC News (via Splash247)"（因非 MSC 官方 feed，已標為 B 而非 A）
- **Tier C**（預設值 `_default`）：一般台灣/中國中文媒體、CNYES、Container News/Offshore Energy/NewsBase/Marine Insight/MarineLink、壹航運/AMZ123 等未特別驗證可信度的來源
- **Tier D**：Reddit r/Ships、r/maritime、r/shipping

未在表中列出的新來源會自動落入 `_default = "C"`，不會被誤判為 Tier A。

## 7. Carrier PR Filtering

僅套用於 `source_category == "航商動態"` 或 `event_type == COMPETITOR` 的文章（`CarrierNewsFilter.applies_to()`）。

- **命中 keep 關鍵字**（新航線/服務暫停/航線調整/跳港/blank sailing/運力部署/聯盟調整/附加費/運價/併購/船隊投資等）→ `KEEP_OPERATIONAL`，正常進入 pipeline。
- **未命中 keep 但命中 exclude 關鍵字**（award/ESG/CSR/贊助/品牌活動/研討會/專訪/週年慶等）→ `DROP`，直接濾除，不會產生 Event，也不會出現在（相容後的）舊版 Email 對應分類中。
- **兩者皆未命中（無法判斷）**→ `KEEP_LOW_VALUE`，保留但不主動升級；由於 COMPETITOR 的 severity 預設僅 5 分、operational_impact 多半落在 0 分，實務上這類文章會自然落在 P3/P4，不會誤登 Top Management Attention。

驗證：fixture 中「Maersk wins sustainability award」「CMA CGM celebrates employee anniversary」等純公關稿在單元測試與整合測試中均被正確 `DROP`；「Maersk launches new container service」「HMM deploys new capacity...」等具營運意義者正確保留並評為 P3（COMPETITOR，非 Safety Alert）。

## 8. Compatibility

`build_compat_news_data()` 保留舊版 `news_data` 的**所有 key 與 dict 結構**（`all`/`zh_tw`/`zh_cn`/`shipping`/`carrier`/`intl`/`cat1`...`cat6`/`other`，每個仍是「舊版 dict」列表，不是 dataclass），`email_sender.py` 不需要任何修改就能繼續運作 —— 已用真實 `EmailRenderer.render_full_html()` 對 Phase 2 輸出做過渲染驗證（見 §9）。唯一差異：被 `CarrierNewsFilter` 判定為 `DROP` 的純公關稿，會一併從這些 legacy bucket 中濾除（連舊版 Email 也不會再看到得獎新聞）。另外新增 `events`（`MaritimeEvent` 列表）與 `articles`（enriched `NewsArticle` 列表）兩個新 key，供 Phase 4 Executive Email 直接取用，舊版 renderer 會忽略這兩個新 key，不受影響。

## 9. Tests — Passed / Failed

**pytest（26 項，全數 Passed，0 Failed）**：

- `test_extraction.py`：`test_event_classification`、`test_event_extraction_carrier`、`test_event_extraction_region`、`test_enrich_sets_all_expected_fields`
- `test_carrier_filter.py`：`test_carrier_pr_filter`、`test_carrier_pr_filter_batch`
- `test_risk_scoring.py`：`test_severity_scoring`、`test_fleet_relevance_scoring`、`test_immediacy_scoring`、`test_operational_impact`、`test_source_confidence`、`test_management_priority`、`test_critical_override`
- `test_clustering.py`：`test_event_clustering_same_event`、`test_event_clustering_different_event`、`test_primary_source_selection`、`test_low_confidence_reddit`
- `test_pipeline.py`：`test_priority_sort` + `test_case1`〜`test_case7`（CLAUDE.md §二十七全部 7 個情境）+ `test_full_fixture_set_runs_without_crash_and_produces_priorities`（20 篇 fixture 整批 regression）

**Regression（Passed）**：`py_compile` 全部 8 個新/改動 Python 檔案 + 5 個測試檔；Phase 1 的 4 個 SMTP mock 測試重跑仍全數通過；`maritime_news.run_intelligence_pipeline()` 以假造 `news_data` 端對端跑過，並用 mocked `smtplib.SMTP` 確認**沒有觸發任何真實網路請求或真實寄信**。

## 10. Example Output

以 20 篇 fixture 全數跑過 pipeline 的實際 console 輸出（`test_full_fixture_set_runs_without_crash_and_produces_priorities` 與手動整合測試結果一致）：

```
============================================================
🧭 MARITIME INTELLIGENCE RESULT
============================================================
Articles collected: 4
Carrier PR filtered out: 1
Valid maritime articles: 3
Events identified: 2

P1: 1
P2: 1
P3: 0
P4: 0

TOP EVENTS

[P1][93]
MSC container vessel attacked by missile in Red Sea
Sources: 1
Confidence: MEDIUM

[P2][70]
MSC vessel grounds in Singapore Strait
Sources: 2
Confidence: HIGH
============================================================
```

（上例為 4 篇輸入的簡化整合測試；20 篇完整 fixture 跑過後可見 P1 至少 1 則、紅海攻擊 3 篇來源正確併成 1 個 Event、事件數少於文章數，證明 clustering 確實生效——細節見 `tests/test_pipeline.py::test_full_fixture_set_runs_without_crash_and_produces_priorities`。）

---

## 已知限制（留待後續階段）

- `vessel_name`（具名船名）僅 best-effort regex 抽取（`MV/MT/MS <Name>` 或引號內名稱），非本階段必測項目，抓不到就是 `None`，不會用來影響 clustering 的主要判斷（船名比對是額外加分，不是必要條件）。
- `country` / `region` / `port` / `shipping_lane` 欄位本階段未建立字典，一律為 `None`（符合「不是所有欄位都一定能抓到，未知用 None」原則）。
- Email 仍是 Phase 1 的舊版樣式（新聞列表），Phase 2 只在背後多跑一層 Intelligence Pipeline 並讓舊 Email 少看到公關稿；Executive Briefing 版面留待 Phase 4。
- 尚未接上 Persistent Event Memory（§二十），因此 `MaritimeEvent.is_new` 目前恆為 `True`，無法判斷「同一事件的更新」——這是 Phase 3 的範圍。

## 下一步建議

Phase 2 已將「新聞列表」升級為「可回答 what/where/who/impact/confidence/priority 的事件」，且完全 rule-based、可測試、可獨立檢查。建議下一階段（依原計畫）處理 Persistent Event Memory（避免同事件重複通知）與 Executive Email 重新設計，而非本輪一次做完。
