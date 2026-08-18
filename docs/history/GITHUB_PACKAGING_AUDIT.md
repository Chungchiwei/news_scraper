# GitHub Packaging Audit — v1.0.0-rc1

本文件為 GitHub Repository Packaging 前的完整盤點。判斷依據為實際檢查
imports / config 參照 / test 參照 / entry point 使用情況，**不依檔名猜測**。

分類：`INCLUDE` / `EXCLUDE` / `GENERATED` / `RUNTIME STATE` / `SECRET` / `LEGACY` / `OPTIONAL`

---

## A. Production Source Code — INCLUDE

以下 55 個根目錄 `.py` 生產模組經 Dependency Closure Audit（見
`GITHUB_PACKAGE_MANIFEST.md`）確認皆被 `maritime_news.py` 或
`dashboard/app.py` 直接或間接 import，且皆存在於專案中（無「本機獨有、
package 會漏掉」的檔案）：

```
ai_cache.py                 analysis_validator.py       briefing_selector.py
carrier_news_filter.py      delivery_config.py          delivery_history.py
delivery_models.py          delivery_orchestrator.py    email_config.py
email_sender.py             email_view_model.py         event_clusterer.py
event_extractor.py          event_identity.py           event_lifecycle.py
event_store.py              executive_email_renderer.py fleet_provider.py
fleet_relevance.py          geographic_relevance.py     intelligence_analyzer.py
llm_config.py               llm_provider.py             management_summary.py
maritime_news.py            material_change_detector.py memory_config.py
memory_pipeline.py          models.py                   notification_policy.py
operational_config.py       operational_history.py      operational_models.py
operational_relevance.py    persistent_matcher.py       port_normalizer.py
port_relevance.py           risk_config.py               risk_scorer.py
route_provider.py           route_relevance.py          schedule_provider.py
source_grounding.py         source_health.py            source_provenance.py
status_extractor.py         system_health.py            teams_config.py
teams_notifier.py           teams_renderer.py            version.py
```

`dashboard/`（`__init__.py`, `app.py`, `service.py`, `view_models.py`,
`static/style.css`, `templates/*.html`）— 正式 source code，Dashboard
Domain 的實作，即使 GitHub Actions 不啟動 Dashboard，仍須保留原始碼。

`config/`（`fleet_config.json`, `schedules_config.json`,
`services_config.json`, `ports_config.json`）— **已逐一開檔確認**：
`fleet_config.json` / `schedules_config.json` / `services_config.json`
內容為 `"vessels": []` / `"port_calls": []` / `"services": []`（空陣列 +
明確註解「實際使用前請以真實資料取代」），是 Phase 6 設計時就刻意保留
的 Placeholder，非真實內部船隊/船期資料。`ports_config.json` 為公開港口
別名對照表（UN/LOCODE），非機敏資料。四個檔案皆可安全 commit。

根目錄 Rule Config（皆為規則/字典，非機敏資料）：
```
risk_rules.json  memory_rules.json  delivery_rules.json
email_rules.json  llm_rules.json  operational_rules.json
keywords_config.json  delivery_rules.json
```

`prompts/maritime_intelligence_v5.txt` — LLM system prompt，不含金鑰。

`scripts/`（`backup_data.py`, `health_check.py`,
`final_acceptance_test.py`, `production_smoke_test.py`，本輪新增
`build_github_package.py`, `github_actions_smoke_test.py`）。

`tests/`（21+ 個測試檔 + `fixtures/`）— 全部使用 mock/fixture 資料，
不連真實網路或真實帳密。

`requirements.txt`, `requirements-dev.txt`, `VERSION`, `.env.example`,
`.gitignore`。

文件：`README.md`, `SYSTEM_ARCHITECTURE.md`, `DATA_FLOW.md`,
`CONFIGURATION_REFERENCE.md`, `OPERATIONS_RUNBOOK.md`,
`IT_DEPLOYMENT_GUIDE.md`, `PYTHON_VERSION.md`, `DEPRECATED.md`,
`FUTURE_ROADMAP.md`, `FINAL_ARCHITECTURE_AUDIT.md`,
`FINAL_ACCEPTANCE_CHECKLIST.md`（皆為文件，掃描確認無密碼/Webhook/金鑰）。
本輪新增：`GITHUB_PACKAGING_AUDIT.md`（本檔）、
`GITHUB_PACKAGE_MANIFEST.md`、`GITHUB_ACTIONS_SETUP.md`、
`GITHUB_ACTIONS_PACKAGING_REPORT.md`。

`.github/workflows/ci.yml`, `.github/workflows/maritime-intelligence.yml`
（本輪新增）。

---

## B. Local Windows Operation — OPTIONAL (INCLUDE)

`run.bat`, `run_dashboard.bat`, `run_tests.bat`, `setup.bat` — 保留供
本機 Windows 操作使用。GitHub Actions（`ubuntu-latest`）不會執行這些
`.bat`，Production Workflow 直接呼叫 `python maritime_news.py`。

---

## C. Legacy — OPTIONAL (INCLUDE, with DEPRECATED.md warning)

`legacy/news_scraper.py`, `legacy/news_scraper_2024.04.16.py` — 已於
Phase 8 確認零 import/runtime/test/config 依賴，且掃描確認**沒有硬編碼
密碼**（皆已用 `os.environ.get("MAIL_PASSWORD", "")` 形式）。保留作為
歷史參考，`DEPRECATED.md` 已明確標示「Do Not Use」。不影響 Package 安全性。

`PHASE2_REPORT.md` ~ `PHASE_8_FINAL_COMPLETION_REPORT.md`,
`claude.md` — Phase 1-8 開發歷程文件與原始需求規格。掃描確認無密碼/
Webhook/金鑰。保留作為架構決策歷史紀錄（Documentation 類別，符合
「Repository 只能保存 Documentation」原則）。

---

## D. GENERATED — EXCLUDE

```
__pycache__/               *.pyc
.pytest_cache/
logs/                      （Phase 8 TimedRotatingFileHandler 產生）
backup/                    （scripts/backup_data.py 產生）
output/                    （preview_email.py / preview_teams.py 產生，
                             本機開發用 HTML/TXT 預覽，非 production 產物）
dist/                       （本輪 build_github_package.py 產生的 package 本身）
```

`.gitignore` 已涵蓋 `venv/ __pycache__/ *.pyc output/ .pytest_cache/
logs/ backup/`；本輪新增 `dist/`。

---

## E. RUNTIME STATE — EXCLUDE（Repository 不保存，只由 GitHub Actions
Artifact 在 workflow runtime 產生/還原，見 §五、六、七）

```
data/maritime_intelligence.db     (Event Store — Phase 3)
data/ai_analysis.db               (LLM Cache — Phase 5)
data/operational_relevance.db     (Operational Exposure History — Phase 6)
data/delivery_history.db          (Delivery/Dedup History — Phase 7)
data/source_health.db             (Source Health — Phase 7)
data/*.db-wal  data/*.db-shm  data/*.db-journal
```

目前實際 `data/` 目錄為空（Phase 8 完成後已清空測試殘留檔案）。
Repository 中 `data/` 只放一個 `.gitkeep` 供資料夾結構存在。

**RUNTIME STATE CONTAINS INTERNAL OPERATIONAL DATA**：一旦系統實際執行，
上述 DB 會包含事件記憶、Fleet Exposure History、Delivery History 等
內部營運資訊。這些檔案「任何時候都不得」進入 Git 版控（見
`GITHUB_ACTIONS_PACKAGING_REPORT.md` §17）。

---

## F. SECRET — EXCLUDE（不得進入 Package／Git）

```
.env                        （真實 MAIL_USER/MAIL_PASSWORD/TARGET_EMAIL）
news_scraper.log            （舊版 legacy scraper 執行紀錄，內含真實
                              內部信箱 harry_chung@wanhai.com — 已被
                              .gitignore 的 *.log 規則排除，本輪額外
                              確認 Package Builder 會 fail-closed 偵測）
```

`.env.example` 已逐一確認：所有密碼/Token/Webhook/API Key 欄位皆為空值，
只有非機敏的預設值（SMTP Server/Port、`NEWS_HOURS_BACK=6` 等）有實際
內容。

---

## G. Dependency Closure 結論

從 `maritime_news.py` 與 `dashboard/app.py` 出發追蹤全部 import，
確認上述 A 類 55 個模組 + `dashboard/` 4 個模組 + 全部 config JSON +
`prompts/` 均在專案中實際存在，Package 不會發生「本機有、Package
漏」的情況（完整追蹤結果見 `GITHUB_PACKAGE_MANIFEST.md`）。

`phase3_simulation.py` / `phase6_simulation.py` / `phase7_simulation.py`
/ `preview_email.py` / `preview_teams.py` 為本機開發用 offline
simulation/preview 工具（不連真實網路，也不是 `maritime_news.py` 或
`dashboard/app.py` 的依賴），歸類 OPTIONAL — 予以保留（無安全疑慮，
方便未來開發除錯），但不在任何 workflow 中執行。
