# System Architecture

WHL Maritime Intelligence System 依 **Domain**（功能領域）組織，不是依開發階段（Phase 1-8）組織——過去的 Phase 只是開發順序，不是系統的心智模型。下面依資料流動順序介紹七個 Domain。

```
PUBLIC MARITIME SOURCES
        │
        ▼
   COLLECTION            (RSS / HTML 爬蟲)
        │
        ▼
   INTELLIGENCE          (事件抽取 / 分類 / 聚類 / 風險評分)
        │
        ▼
   MEMORY                (跨執行持久記憶 / 異動偵測)
        │
        ▼
   OPERATIONAL RELEVANCE (船隊／船期／航線曝險評估，獨立於上面的風險評分)
        │
        ▼
   DELIVERY              (該不該通知？該用哪個管道？)
        │
        ▼
   PRESENTATION          (Email / Teams / Dashboard)

   SYSTEM HEALTH（橫向，監控以上所有環節，走獨立管道，不與情報告警混用）
```

---

## 1. COLLECTION — 新聞蒐集

**職責**：從公開海事新聞來源（RSS / HTML / Reddit）抓取文章，做基本正規化。

**主要模組**：`maritime_news.py`（`NewsRssScraper`、`RedditShippingScraper`、`OneShippingScraper`、`LloydsListScraper`、`Amz123Scraper`、`XindeScraper`）

**設計原則**：單一來源失敗不影響其他來源；每個來源的健康狀態記錄於 SYSTEM HEALTH domain，不記錄在情報本身。

---

## 2. INTELLIGENCE — 事件情報核心

**職責**：把「文章」轉成「事件」——正規化、Context Validation、事件抽取、分類、聚類（去重）、風險評分。

**主要模組**：
- `models.py` — `NewsArticle` / `MaritimeEvent` 資料結構
- `event_extractor.py` — 從文字抽取 vessel_name / event_type / incident_subtype / location 等結構化欄位
- `carrier_news_filter.py` — 濾除航商 PR 稿
- `risk_scorer.py` — Severity / Fleet Relevance / Immediacy / Source Confidence → Management Priority
- `event_clusterer.py` — 多篇報導同一事件時合併成一個 Event
- `source_provenance.py` — 判斷來源是否真的獨立（避免同一篇稿件被多家媒體轉載就誤判為多來源交叉證實）
- `risk_config.py` / `risk_rules.json` — 規則設定

**核心原則**：Category（發生什麼事）與 Priority（主管有多需要知道）互相獨立，見 `risk_rules.json` 設計。

---

## 3. MEMORY — 持久事件記憶

**職責**：讓系統記得「昨天看過的事件」，今天再看到同一事件時判斷是 NEW / MATERIAL_UPDATE / MINOR_UPDATE / UNCHANGED / RESOLVED_UPDATE，而不是每次執行都當成新事件重複通知。

**主要模組**：
- `event_store.py` — SQLite 持久化（`data/maritime_intelligence.db`，**系統唯一 Fatal 等級的資料庫**）
- `event_identity.py` / `persistent_matcher.py` — 跨執行事件身份比對
- `status_extractor.py` — 從文字抽取事件目前狀態（例如 GROUNDING → REFLOATED）
- `material_change_detector.py` — 判斷狀態變化是否「管理層級重要」
- `event_lifecycle.py` — ACTIVE / MONITORING / RESOLVED / EXPIRED 狀態機
- `notification_policy.py` — 是否要通知的規則
- `memory_pipeline.py` — 整合入口（`apply_persistent_memory()`）

**核心原則**：Incident Category、Risk Priority、Notification State 三者完全獨立計算，不互相覆蓋。

---

## 4. OPERATIONAL RELEVANCE — 船隊曝險評估

**職責**：獨立於上面的「這件事有多嚴重」，另外回答「這件事跟我們公司船隊有沒有關係、關係多大」。

**主要模組**：
- `operational_models.py` / `operational_config.py` / `operational_rules.json`
- `fleet_provider.py` / `schedule_provider.py` / `route_provider.py` — Provider Adapter（目前皆為讀本機 `config/*.json` 的 `Config*Provider`，未來可替換成內部系統 API 而不影響其他模組）
- `port_normalizer.py` — 港口別名正規化
- `fleet_relevance.py` / `port_relevance.py` / `route_relevance.py` / `geographic_relevance.py` — 各種比對維度
- `operational_relevance.py` — Engine 主體（`OperationalRelevanceEngine.assess()`）
- `operational_history.py` — 獨立 SQLite（`data/operational_relevance.db`），紀錄曝險隨時間變化

**核心原則**（Phase 6 最重要主張）：**EVENT RISK ≠ COMPANY EXPOSURE**。同一事件在 Persistent Memory 軸上可能完全 UNCHANGED，但因為船期逼近，曝險可以獨立從 MODERATE 升到 HIGH——兩條時間軸互相獨立。

---

## 5. DELIVERY — 通知決策

**職責**：綜合 Event Axis（MEMORY 的判斷）與 Operational Axis（OPERATIONAL RELEVANCE 的判斷），決定這次要不要通知、通知到哪個管道、用什麼急迫程度。

**主要模組**：
- `delivery_models.py` / `delivery_config.py` / `delivery_rules.json`
- `delivery_history.py` — 獨立 SQLite（`data/delivery_history.db`），per-channel 送達歷史與去重
- `delivery_orchestrator.py` — **Dual-Axis Trigger** 核心邏輯（`DeliveryOrchestrator.decide()`）

**核心原則**（Phase 7 最重要主張）：`EVENT UNCHANGED` 不代表 `DELIVERY 什麼都不做`——如果 Operational Exposure 獨立升高，Delivery 仍必須觸發 PROMPT 通知（見 `scripts/final_acceptance_test.py` Scenario C，或本文件旁的 `DATA_FLOW.md`）。Dashboard 永遠可見，與 Push（Email/Teams）決策分離。

---

## 6. PRESENTATION — 呈現層

### Email
- `email_config.py` / `email_view_model.py` / `management_summary.py` / `briefing_selector.py` / `executive_email_renderer.py` — 產生主管 Executive Brief
- `email_sender.py` — 純 SMTP 傳輸層（`send_html()` 為現行路徑；`EmailRenderer`/`send()` 為 Phase 1-3 舊版，保留為緊急備援，見 `DEPRECATED.md`）

### Teams
- `teams_config.py` / `teams_notifier.py` / `teams_renderer.py`

### Dashboard
- `dashboard/app.py` — FastAPI entry point（`python dashboard/app.py` 或 `run_dashboard.bat`）
- `dashboard/service.py` — 唯讀資料服務（`DashboardService`），只讀取上述各 SQLite，不觸碰 scraper
- `dashboard/view_models.py` / `dashboard/templates/*.html` / `dashboard/static/style.css`

---

## 7. SYSTEM HEALTH — 系統健康監控（橫向）

**職責**：監控「系統本身」是否正常運作（新聞來源、資料庫、Email、Teams、LLM、Provider），與「海事情報本身有多嚴重」完全是兩件事，走獨立的通知管道與獨立的 Webhook（`TEAMS_SYSTEM_WEBHOOK_URL` vs. `TEAMS_MANAGEMENT_WEBHOOK_URL`）。

**主要模組**：
- `source_health.py` — 各新聞來源健康狀態（獨立 SQLite，`data/source_health.db`）
- `system_health.py` — 整合報告（`SystemHealthService`）

---

## 入口點（Entry Points）

| 檔案 | 用途 |
|---|---|
| `maritime_news.py` | **唯一** production 情報流程入口。`run.bat` 呼叫它。 |
| `dashboard/app.py` | Dashboard entry point。`run_dashboard.bat` 呼叫它。 |
| `scripts/health_check.py` | 系統健康檢查（不寄信、不送 Teams、不呼叫 LLM）。 |
| `scripts/backup_data.py` | 資料庫備份。 |
| `scripts/final_acceptance_test.py` | 完全離線的端對端驗收測試。 |

詳細檔案清單見 `FINAL_ARCHITECTURE_AUDIT.md`；環境變數詳見 `CONFIGURATION_REFERENCE.md`；資料流向詳見 `DATA_FLOW.md`。
