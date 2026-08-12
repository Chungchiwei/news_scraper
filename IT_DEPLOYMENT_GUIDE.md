# IT Deployment Guide

給負責部署／維護這台機器的 IT 人員看，不假設你熟悉這個專案的業務邏輯。

## Environment

- **作業系統**：Windows（開發與測試環境）。
- **Python**：建議 3.11.x；3.10.x 亦已測試通過。詳見 `PYTHON_VERSION.md`。不建議 3.9 以下或未經測試的 3.13+。
- **網路**：需要一般辦公網路的 outbound HTTPS 存取（見下方「所需對外連線」）。不需要對外開放任何 inbound port（Dashboard 預設只在本機監聽）。

## 所需對外連線（Outbound）

| 目的 | Port | 說明 |
|---|---|---|
| 海事新聞來源（RSS/HTML） | 443 (HTTPS) | 多個公開新聞網站，任一個連不上不影響整體（見 `OPERATIONS_RUNBOOK.md` Source Failure） |
| SMTP 伺服器 | 587（或貴公司 SMTP 設定的埠） | 寄送 Executive Email |
| Microsoft Teams Webhook | 443 (HTTPS) | 僅在 `TEAMS_ENABLED=true` 時需要 |
| LLM API（Anthropic/OpenAI） | 443 (HTTPS) | 僅在 `LLM_ENABLED=true` 時需要，預設停用 |

不需要假設或預先開通任何雲端服務（Azure／AWS／GCP）的連線——本系統目前是單機批次程式，不依賴任何雲端架構。

## Files

```
news_scrape/
├── maritime_news.py          程式主體（唯一情報流程入口）
├── dashboard/app.py          Dashboard 入口
├── config/                   參考資料設定（船隊/船期/航線/港口）
├── *_rules.json              規則設定（風險評分/記憶/Email/Teams/LLM/曝險）
├── data/                     SQLite 資料庫（自動建立，不要手動編輯）
├── logs/                     執行紀錄（自動 rotate，保留 14 天）
├── backup/                   資料庫備份（scripts/backup_data.py 產生）
├── output/                   本機 Email/Teams 預覽（開發用，可忽略）
├── scripts/                  維運工具（health_check / backup_data / 測試腳本）
├── venv/                     Python 虛擬環境（setup.bat 建立）
├── .env                      機密設定（不可提交版控，需自行建立）
└── requirements*.txt         套件相依清單
```

## Secrets

`.env` 檔案包含帳密與 API Key，**絕對不能**提交到版控系統（已在 `.gitignore` 排除）。部署到新機器時：

1. 執行 `setup.bat`，它會從 `.env.example` 複製出一份 `.env` 範本。
2. 手動編輯 `.env`，填入正確的帳密（不要用共用/測試帳號跑 production）。
3. 完整變數說明見 `CONFIGURATION_REFERENCE.md`。

如果貴公司有集中式密碼管理系統（例如 Vault、Azure Key Vault），可以在啟動排程時把密碼注入為環境變數，而不是寫進 `.env` 檔案——本系統只透過標準 `os.environ` 讀取，不限定一定要用 `.env` 檔案。

## Execution

正常執行一次情報循環：

```
run.bat
```

或直接：

```
venv\Scripts\activate
python maritime_news.py
```

## Dashboard

```
run_dashboard.bat
```

預設監聽 `127.0.0.1:8000`（僅本機存取）。若需要讓其他同事透過內網存取，**不要**直接改成 `0.0.0.0` 對外公開；建議：

- 設定 `DASHBOARD_AUTH_ENABLED=true` 並提供 `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD`，並
- 透過貴公司現有的反向代理／內網存取控制機制轉發，而不是直接把服務綁定到 `0.0.0.0`。

## Scheduling

本系統本身**不包含**排程引擎。請使用貴公司既有的排程機制：

- **Windows Task Scheduler**：建立一個工作，觸發器設定為每 N 小時執行一次，動作指向 `run.bat`（或 `python maritime_news.py`，工作目錄設為專案根目錄）。
- 若貴公司已有企業排程系統（例如某種 Job Scheduler），比照辦理，一樣是「固定時間執行 `run.bat`」。

不建議在本階段引入新的排程引擎或常駐服務（見 Phase 8 Feature Freeze 原則，`FUTURE_ROADMAP.md`）。

## 部署後驗證

1. `python scripts/health_check.py` — 確認所有元件狀態正常。
2. `run.bat` 手動跑一次，確認能收到 Executive Email。
3. `run_dashboard.bat` 開啟，確認能看到頁面。
4. 設定 Task Scheduler，讓系統定期自動執行。

更詳細的故障排除見 `OPERATIONS_RUNBOOK.md`。
