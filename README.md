# WHL Maritime Intelligence System

**Version 1.0.0-rc1**

## 1. Purpose

將公開海事新聞轉換為事件型風險情報，並結合船隊、船期及航線資料判斷 WHL Operational Exposure，透過 Email、Teams 與 Dashboard 提供主管使用。

## 2. System Architecture

```
PUBLIC MARITIME SOURCES → EVENT INTELLIGENCE → PERSISTENT MEMORY
   → WHL OPERATIONAL EXPOSURE → DELIVERY ORCHESTRATOR
   → EMAIL / TEAMS / DASHBOARD
```

完整架構說明見 [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md)；資料流向細節見 [`DATA_FLOW.md`](DATA_FLOW.md)。

## 3. Key Functions

- **Maritime Event Detection** — 從公開新聞辨識海事事故／安全／營運／法規事件，過濾航商公關稿與財經雜訊。
- **Risk Priority** — 依 Severity / Fleet Relevance / Immediacy / Source Confidence 計算 P1-P4 管理優先級。
- **Event Memory** — 跨執行記住事件，避免同一事件重複通知。
- **Material Update** — 只有「管理層級重要」的變化才會再次通知。
- **Fleet Exposure** — 獨立於事件本身嚴重度，另外評估與 WHL 船隊/船期/航線的關聯程度。
- **Executive Email** — 主管導向的精簡 Email Brief，而非新聞列表。
- **Teams Alert** — 高優先事件的即時 Teams 通知，與系統健康告警走不同頻道。
- **Management Dashboard** — 唯讀網頁介面，檢視目前活躍事件、船隊曝險、系統健康。

## 4. Quick Start

Windows 環境（見 [`PYTHON_VERSION.md`](PYTHON_VERSION.md) 建議版本）：

```
1. 雙擊 setup.bat        建立虛擬環境、安裝套件、準備 .env（僅一次）
2. 編輯 .env             填入 MAIL_USER / MAIL_PASSWORD / TARGET_EMAIL
3. 雙擊 run.bat           執行一次情報循環
4. 雙擊 run_dashboard.bat 開啟 Management Dashboard（http://127.0.0.1:8000）
```

## 5. Configuration

- `.env`（從 `.env.example` 複製）— 所有機密與環境相關設定，完整說明見 [`CONFIGURATION_REFERENCE.md`](CONFIGURATION_REFERENCE.md)。
- 主要規則設定檔（專案根目錄）：`keywords_config.json`、`risk_rules.json`、`memory_rules.json`、`email_rules.json`、`operational_rules.json`、`delivery_rules.json`、`llm_rules.json`。
- 參考資料設定檔：`config/fleet_config.json`、`config/schedules_config.json`、`config/services_config.json`、`config/ports_config.json`。

## 6. Database

5 個獨立 SQLite 資料庫，預設皆位於 `data/` 目錄，路徑可用環境變數覆寫（見 `CONFIGURATION_REFERENCE.md`）：

| 資料庫 | 用途 | 失敗時 |
|---|---|---|
| `data/maritime_intelligence.db` | 事件持久記憶（**唯一 Fatal 等級**） | 程式無法啟動 |
| `data/ai_analysis.db` | LLM 分析快取 | 自動退化為 Rule-Based |
| `data/operational_relevance.db` | 船隊曝險歷史 | 該區塊不顯示，Email 仍正常寄出 |
| `data/delivery_history.db` | 送達歷史／去重 | Teams/Email 仍正常運作，只是不記錄歷史 |
| `data/source_health.db` | 新聞來源健康狀態 | 不影響爬蟲本身 |

備份：`python scripts/backup_data.py`（使用 SQLite Online Backup API，預設保留最近 14 次，見 `OPERATIONS_RUNBOOK.md`）。

## 7. Testing

```
雙擊 run_tests.bat
```

或手動執行：`python -m pytest -q`（需要先 `pip install -r requirements-dev.txt`）。

驗收測試（完全離線，不連真實網路/SMTP/Teams/LLM）：`python scripts/final_acceptance_test.py`

## 8. Troubleshooting

1. 先執行 `python scripts/health_check.py`，確認每個元件狀態。
2. 查看 `logs/maritime_intelligence.log`（完整除錯細節；console 只顯示精簡摘要）。
3. 詳細故障排除步驟見 [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md)。
4. IT 部署相關問題見 [`IT_DEPLOYMENT_GUIDE.md`](IT_DEPLOYMENT_GUIDE.md)。

---

其他文件：[`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md) · [`DATA_FLOW.md`](DATA_FLOW.md) · [`CONFIGURATION_REFERENCE.md`](CONFIGURATION_REFERENCE.md) · [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md) · [`IT_DEPLOYMENT_GUIDE.md`](IT_DEPLOYMENT_GUIDE.md) · [`FUTURE_ROADMAP.md`](FUTURE_ROADMAP.md) · [`DEPRECATED.md`](DEPRECATED.md) · [`docs/history/FINAL_ARCHITECTURE_AUDIT.md`](docs/history/FINAL_ARCHITECTURE_AUDIT.md)
