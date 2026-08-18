# Final Architecture Audit — Phase 8

執行方式：純唯讀盤點（`find`/`grep`/`pip list`/`git`-free），本文件之後才開始 Phase 8 的實際整理工作。目的是在動手之前，先看清楚專案現況，避免「以為沒用就刪」。

---

## 1. Entry Points

| 檔案 | 角色 | 狀態 |
|---|---|---|
| `maritime_news.py` | **唯一 production intelligence pipeline entry point**。`run.bat` 最終呼叫 `python maritime_news.py`。 | ✅ 現役 |
| `news_scraper.py`（專案根目錄，2434 行） | Phase 1 之前的獨立單檔爬蟲＋SMTP 寄送實作（含自己的 `smtplib`/`MIMEText` 邏輯，不依賴 Phase 2+ 任何模組）。全專案（含 `run.bat`、任何 `.py`）**沒有任何 `import news_scraper`**，只有 `claude.md`／`PHASE2_REPORT.md` 的敘述文字提到它。 | ⚠️ Legacy，未被引用，安全可移至 `legacy/`（見第 8 節） |
| `SPARE/news_scraper_2024.04.16.py`（2182 行） | 比 `news_scraper.py` 更早的日期備份版本，放在使用者自建的 `SPARE/` 資料夾。沒有任何程式引用。 | ⚠️ Legacy，安全可移至 `legacy/` |
| `dashboard/app.py` | Management Dashboard 的 FastAPI entry point（`python dashboard/app.py` 或 `uvicorn dashboard.app:app`）。 | ✅ 現役（Phase 7） |

**結論**：Production 只有一個新聞情報 entry point（`maritime_news.py`）+ 一個 Dashboard entry point（`dashboard/app.py`），符合 Phase 8 §四要求。`run.bat` 已經正確指向 `maritime_news.py`（非 `news_scraper.py`）。

---

## 2. Core Modules（依 Domain 分類，供 SYSTEM_ARCHITECTURE.md 使用）

| Domain | 模組 |
|---|---|
| **COLLECTION** | `maritime_news.py`（`NewsRssScraper`/`RedditShippingScraper`/`OneShippingScraper`/`LloydsListScraper`/`Amz123Scraper`/`XindeScraper`） |
| **INTELLIGENCE** | `models.py`、`event_extractor.py`、`carrier_news_filter.py`、`risk_scorer.py`、`event_clusterer.py`、`source_provenance.py`、`risk_config.py` |
| **MEMORY** | `event_store.py`、`event_identity.py`、`persistent_matcher.py`、`status_extractor.py`、`material_change_detector.py`、`event_lifecycle.py`、`notification_policy.py`、`memory_pipeline.py`、`memory_config.py` |
| **LLM ENHANCEMENT（Optional）** | `llm_config.py`、`llm_provider.py`、`source_grounding.py`、`analysis_validator.py`、`ai_cache.py`、`intelligence_analyzer.py` |
| **OPERATIONAL RELEVANCE** | `operational_models.py`、`operational_config.py`、`fleet_provider.py`、`schedule_provider.py`、`route_provider.py`、`port_normalizer.py`、`fleet_relevance.py`、`port_relevance.py`、`route_relevance.py`、`geographic_relevance.py`、`operational_relevance.py`、`operational_history.py` |
| **DELIVERY** | `delivery_models.py`、`delivery_config.py`、`delivery_history.py`、`delivery_orchestrator.py`、`teams_config.py`、`teams_notifier.py`、`teams_renderer.py` |
| **PRESENTATION（Email）** | `email_config.py`、`email_view_model.py`、`management_summary.py`、`briefing_selector.py`、`executive_email_renderer.py`、`email_sender.py` |
| **PRESENTATION（Dashboard）** | `dashboard/app.py`、`dashboard/service.py`、`dashboard/view_models.py`、`dashboard/templates/*.html`、`dashboard/static/style.css` |
| **SYSTEM HEALTH** | `source_health.py`、`system_health.py` |

---

## 3. Configuration

### 3.1 規則設定檔（專案根目錄，`_config.py` loader + `lru_cache`）

| 檔案 | Loader | 用途 |
|---|---|---|
| `keywords_config.json` | `maritime_news.load_keywords_config()` | 關鍵字分類、航商別名 |
| `risk_rules.json` | `risk_config.load_risk_rules()` | Severity/Priority/Confidence 評分規則 |
| `memory_rules.json` | `memory_config.load_memory_rules()` | Persistent Memory 比對/Material Change 門檻 |
| `email_rules.json` | `email_config.load_email_rules()` | BriefingSelector 分桶門檻、no_risk 政策 |
| `llm_rules.json` | `llm_config.load_llm_rules()` | LLM Enhancement 規則（Optional） |
| `operational_rules.json` | `operational_config.load_operational_rules()` | Operational Relevance 門檻/權重 |
| `delivery_rules.json` | `delivery_config.load_delivery_rules()` | Delivery Urgency/Cooldown/Teams 門檻 |

### 3.2 參考資料設定檔（`config/`）

| 檔案 | 用途 |
|---|---|
| `config/ports_config.json` | 港口別名表（22 個主要港口） |
| `config/fleet_config.json` | Fleet Provider 本機資料來源（目前為空陣列佔位） |
| `config/schedules_config.json` | Schedule Provider 本機資料來源（目前為空陣列佔位） |
| `config/services_config.json` | Route Provider 本機資料來源（目前為空陣列佔位） |

### 3.3 環境變數

見獨立文件 `CONFIGURATION_REFERENCE.md`（第 16 節文件套件的一部分）——完整列出 Required / Optional / Default，本審計不重複列出以避免 Single Source of Truth 分裂。

---

## 4. Database

| DB 檔案（皆位於 `data/`，見第 5 節） | 建立於 | 用途 | 失敗行為 |
|---|---|---|---|
| `data/maritime_intelligence.db` | Phase 3 `event_store.py` | 事件持久化主記憶體（events/event_articles/event_history/system_runs） | **Fatal**（`EventStoreError`，`__main__` exit(1)） |
| `data/ai_analysis.db` | Phase 5 `ai_cache.py` | LLM 分析結果快取 | Non-critical，開啟失敗 fallback 回 rule-based |
| `data/operational_relevance.db` | Phase 6 `operational_history.py` | Operational Exposure 歷史快照 | Non-critical，`NullOperationalHistoryStore` 安全退化 |
| `data/delivery_history.db` | Phase 7 `delivery_history.py` | 各 channel 送達歷史（dedup/cooldown 查詢用） | Non-critical，`NullDeliveryHistoryStore` 安全退化 |
| `data/source_health.db` | Phase 7 `source_health.py` | 各新聞來源健康狀態 | Non-critical，`NullSourceHealthStore` 安全退化 |

**Audit 當下發現、後續已於 Phase 8.7 處理**：

```
data/event_history_test.db          ← 0 bytes，無任何程式引用 → 已刪除
data/event_history_test.db-journal  ← 同上附屬檔 → 已刪除
data/test123.db                     ← 無任何程式引用 → 已刪除
data/test123.db-journal             ← 同上附屬檔 → 已刪除
```

上述 4 個檔案皆已用 `grep -rn` 確認沒有任何 `.py` 引用其檔名（非 `DEFAULT_*_DB_PATH` 常數指向的正式路徑），判定為開發期間互動測試殘留物，已於 Phase 8.7 清除。

**後續更新（Phase 8.13 驗收測試階段）**：原本保留觀察的 `data/operational_relevance.db` 在後續 Phase 8 手動驗證 `maritime_news.py` 啟動流程／`scripts/backup_data.py` 時被再次寫入（確認其 schema 為空、無實質資料），連同因手動測試 Critical Config 驗證而產生的 `data/maritime_intelligence.db`、`data/delivery_history.db` 一併確認為開發期間測試殘留（無 production 資料），已於本輪 Finalization 清除。目前 `data/` 目錄為空，等待使用者第一次執行 `run.bat` 時由 production 程式碼自動建立正式資料庫。

**Schema Version**：目前只有 `event_store.py` 實作 `schema_meta` 表與 `SCHEMA_VERSION` 常數（見 Phase 3 設計）；其餘 4 個 DB 目前無版本欄位——這是已知限制，記錄於 `PHASE_8_FINAL_COMPLETION_REPORT.md` 第 17 節，不在本輪新增 migration 框架。

---

## 5. 統一 Data Directory 現況

好消息：**所有 5 個 DB 的預設路徑本來就已經是 `data/*.db`**（`DEFAULT_DB_PATH`/`DEFAULT_AI_CACHE_DB_PATH`/`DEFAULT_OPERATIONAL_HISTORY_DB_PATH`/`DEFAULT_DELIVERY_HISTORY_DB_PATH`/`DEFAULT_SOURCE_HEALTH_DB_PATH` 皆定義為 `"data/xxx.db"`），Phase 8 §十六「統一 Data Directory」**不需要搬動任何檔案**，只需要在文件中明確記錄這個事實，並清除第 4 節列出的殘留測試檔案。這是低風險項目。

---

## 6. Tests

| 檔案 | 涵蓋範圍 | 測試數 |
|---|---|---|
| `tests/conftest.py` | Phase 2 共用 fixture（`fixture_articles`/`rules`/`extractor`/`scorer`/`clusterer`） | — |
| `tests/test_carrier_alias_audit.py` | 航商別名 false-positive | 8 |
| `tests/test_carrier_filter.py` | Carrier PR Filter | 2 |
| `tests/test_clustering.py` | Event Clustering | 4 |
| `tests/test_clustering_hardening.py` | Clustering Hard Reject | 4 |
| `tests/test_confidence_priority.py` | Priority/Confidence 解耦 | 2 |
| `tests/test_extraction.py` | Event Extraction | 4 |
| `tests/test_memory_lifecycle.py` | Persistent Memory Lifecycle | 12 |
| `tests/test_memory_store.py` | EventStore SQLite | 10 |
| `tests/test_phase4_email.py` | Executive Email | 24 |
| `tests/test_phase5_llm.py` | LLM Enhancement | 22 |
| `tests/test_phase6_operational.py` | Operational Relevance | 23 |
| `tests/test_phase7_delivery.py` | Delivery Orchestrator | 12 |
| `tests/test_phase7_teams.py` | Teams Integration | 9 |
| `tests/test_phase7_dashboard.py` | Management Dashboard | 13 |
| `tests/test_phase7_health.py` | System Health | 5 |
| `tests/test_pipeline.py` | End-to-end CASE scenario | 8 |
| `tests/test_risk_scoring.py` | Risk Scoring | 7 |
| `tests/test_source_independence.py` | 獨立來源計算 | 2 |
| `tests/test_ssl_config.py` | SSL/TLS 設定 | 3 |
| `tests/fixtures/articles.json` / `fleet.json` / `schedules.json` / `services.json` | Mock fixture，供上述測試使用，非 runtime config | — |

**總計 175 項測試，全數通過**（見 `PHASE_7_COMPLETION_REPORT.md` 第 14 節）。全部使用 tmp_path 暫存 SQLite / Fake Provider / FastAPI TestClient，無任何 live 依賴。

---

## 7. Preview / Simulation Scripts（開發驗證用，非 production 一部分）

| 檔案 | 用途 |
|---|---|
| `preview_email.py` | 產生 8 個 Email HTML Preview 到 `output/` |
| `preview_teams.py` | 產生 5 個 Teams 訊息 Preview 到 `output/` |
| `phase3_simulation.py` | Phase 3 Event Lifecycle 模擬 |
| `phase6_simulation.py` | Phase 6 Three-Run Exposure Simulation |
| `phase7_simulation.py` | Phase 7 Management Simulation（Event A/B/C/D）+ System Health Simulation |

以上全部**不連任何真實 SMTP/Teams webhook/Internet**，使用暫存 SQLite 與 Fake Provider。保留供未來開發/除錯使用，不刪除。

---

## 8. Legacy / Deprecated / Temporary / Generated Files

| 分類 | 檔案 | 處理建議 |
|---|---|---|
| **Legacy（未被引用，已安全移動）** | `news_scraper.py`（原根目錄）、`SPARE/news_scraper_2024.04.16.py` | ✅ 已移至 `legacy/`（Phase 8.2），`SPARE/` 目錄已移除，`DEPRECATED.md` 已記錄 |
| **Deprecated in-place（有 production fallback 價值，不可移動/刪除）** | `email_sender.py` 內的 `EmailRenderer` class 與 `NewsEmailSender.send()` 方法 | 保留原位，`DEPRECATED.md` 記錄「Do Not Use / Replacement」 |
| **Generated（開發期間互動測試殘留，已清除）** | `data/event_history_test.db*`、`data/test123.db*` | ✅ 已刪除（Phase 8.7） |
| **Generated（位於正式路徑，保留待使用者確認）** | `data/operational_relevance.db*`（見第 4 節說明） | 保留，未刪除 |
| **Generated（regenerable preview，已被 gitignore）** | `output/*.html`、`output/*.txt` | 保留現況（`.gitignore` 已排除 `output/`） |
| **Generated（快取）** | `__pycache__/`、`dashboard/__pycache__/`、`.pytest_cache/` | `.gitignore` 需補上 `.pytest_cache/`（目前缺漏，見 Phase 8.7） |
| **舊 log** | `news_scraper.log`（專案根目錄，2026-03-02 產生，硬連結數為 2） | 屬於 `news_scraper.py` 時代的 log 產物，`.gitignore` 已有 `*.log` 排除；本輪新增 `logs/` 目錄後，此檔案標記為過期產物，於 Phase 8.2 一併歸檔說明 |
| **命名不一致但非過期** | `PHASE2_REPORT.md`（其餘為 `PHASE_X_COMPLETION_REPORT.md`/`PHASE_X.Y_COMPLETION_REPORT.md` 命名） | 低風險：無程式引用檔名，僅文件命名不一致，可選擇性重新命名以統一風格，不影響任何 import/test |
| **開發環境目錄** | `venv/`（已存在的 Python 3.11.9 virtualenv） | 保留（使用者本機開發環境），`.gitignore` 已排除 `venv/` |
| **主要需求文件** | `claude.md`（1375 行，即本專案的 CLAUDE.md 主需求文件，Windows 檔案系統不分大小寫） | ✅ 現役，非 legacy，不動 |

---

## 9. Dashboard

見 `PHASE_7_COMPLETION_REPORT.md` 第 8-13 節，架構未變動。Phase 8 只新增 `run_dashboard.bat` 啟動器（Phase 8.10）。

---

## 10. 環境版本確認（實測，非猜測）

```
沙盒測試環境：Python 3.10.12
使用者實際 Windows venv（venv/pyvenv.cfg 記錄）：Python 3.11.9
```

當前已安裝且驗證可用的關鍵套件版本（`pip list` 實測）：

```
fastapi        0.141.1
starlette      1.6.0
jinja2         3.1.6
uvicorn        0.52.1
httpx          0.28.1
python-multipart 0.0.32
feedparser     6.0.14
beautifulsoup4 4.15.0
lxml           6.1.1
requests       2.34.2
python-dotenv  1.2.2
pytest         9.1.1
```

結論：正式支援版本定為 **Python 3.10 / 3.11+**（以使用者實際 venv 為準記錄 3.11.9 為目前開發/測試基準），寫入 `README.md`/新增 `PYTHON_VERSION.md`（Phase 8.6）。

---

## 11. 本審計不涵蓋（刻意）

- 不逐一驗證 60+ 新聞來源網址目前是否還能連線（Phase 8 §五十二：只做 code/config consistency audit，不做上網驗證）。
- 不檢查 Git 歷史（本專案在此環境未初始化為 git repository）。
- 不評估雲端部署可行性（Phase 8 §六十四明確排除）。

---

**下一步**：依本審計結果，按 Phase 8 任務清單逐項執行（Entry Point 確認 → Legacy 處理 → VERSION → Config/.env.example → Secret Audit → requirements → Database/gitignore 清理 → Backup/Health Check Script → Runners → Logging → Startup Validation → Acceptance Test → Finalization Tests → Regression → Documentation → TODO Audit → Checklist → Completion Report）。
