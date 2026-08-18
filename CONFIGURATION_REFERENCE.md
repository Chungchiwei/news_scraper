# Configuration Reference

本文件是所有環境變數的 **Single Source of Truth**。`.env.example` 提供可複製的範本；本文件提供每個變數的完整說明。若本文件與程式碼有出入，以程式碼（各模組的 `_bool_env()`/`os.environ.get()` 呼叫）為準，並請回報此文件已過期。

---

## Email

| Name | Required? | Default | Description | Secret? |
|---|---|---|---|---|
| `MAIL_SMTP_SERVER` | Optional | `smtp.gmail.com` | SMTP 伺服器位址 | No |
| `MAIL_SMTP_PORT` | Optional | `587` | SMTP 連接埠 | No |
| `MAIL_USER` | **Required** | — | 寄件帳號；缺少會讓程式啟動時立即失敗（Fatal） | Yes |
| `MAIL_PASSWORD` | **Required** | — | 寄件密碼／App Password；缺少會讓程式啟動時立即失敗（Fatal） | Yes |
| `TARGET_EMAIL` | **Required** | — | 收件者信箱；缺少會讓程式啟動時立即失敗（Fatal） | Yes（視為個資） |
| `MAIL_SEND_MAX_ATTEMPTS` | Optional | `3` | SMTP 送信重試次數上限 | No |
| `MAIL_SEND_RETRY_WAIT_SEC` | Optional | `15` | 每次重試間隔秒數（會隨嘗試次數遞增） | No |
| `SEND_NO_RISK_BRIEF` | Optional | 讀取 `email_rules.json` 的 `no_risk.send`（預設 `true`） | 本次無重大風險事件時，是否仍寄出日報 | No |

## Teams（Optional，預設全部停用）

| Name | Required? | Default | Description | Secret? |
|---|---|---|---|---|
| `TEAMS_ENABLED` | Optional | `false` | 是否啟用 Teams 通知 | No |
| `TEAMS_MANAGEMENT_WEBHOOK_URL` | Optional（`TEAMS_ENABLED=true` 時建議設定） | — | 主管情報頻道 Webhook | Yes |
| `TEAMS_SYSTEM_WEBHOOK_URL` | Optional | — | 系統健康告警頻道 Webhook（與上者刻意分離，見 SYSTEM_ARCHITECTURE.md） | Yes |
| `TEAMS_WEBHOOK_URL` | Optional（相容用） | — | 舊版單一 webhook 設定，未設定 `TEAMS_MANAGEMENT_WEBHOOK_URL` 時視為 Management webhook | Yes |
| `TEAMS_MAX_RETRIES` | Optional | `3` | Teams 送出重試次數上限 | No |
| `TEAMS_RETRY_WAIT_SECONDS` | Optional | `5` | 重試間隔秒數 | No |
| `TEAMS_TIMEOUT_SECONDS` | Optional | `10` | 單次請求逾時秒數 | No |

## LLM Enhancement（Optional，預設停用）

| Name | Required? | Default | Description | Secret? |
|---|---|---|---|---|
| `LLM_ENABLED` | Optional | `false` | 是否啟用 LLM 摘要強化；停用時全部改用 Rule-Based Summary | No |
| `LLM_PROVIDER` | Optional | `disabled` | `claude` / `openai` / `disabled` | No |
| `LLM_MODEL` | Optional | — | 模型名稱（依 Provider 而定） | No |
| `ANTHROPIC_API_KEY` | Optional（`LLM_PROVIDER=claude` 時必填） | — | Anthropic API Key | Yes |
| `OPENAI_API_KEY` | Optional（`LLM_PROVIDER=openai` 時必填） | — | OpenAI API Key | Yes |
| `LLM_TIMEOUT_SECONDS` | Optional | `30`（或 `llm_rules.json` 設定） | 單次呼叫逾時秒數 | No |
| `LLM_MAX_RETRIES` | Optional | `2`（或 `llm_rules.json` 設定） | 重試次數上限 | No |
| `LLM_FAILURE_CIRCUIT_BREAKER` | Optional | `3`（或 `llm_rules.json` 設定） | 連續失敗幾次後本次 run 停止再呼叫 LLM | No |
| `LLM_ALLOW_INTERNAL_OPERATIONAL_DATA` | Optional | `false` | 是否允許把清洗過的 Fleet Exposure 摘要傳給外部 LLM API（更保守的預設） | No |

## Dashboard

| Name | Required? | Default | Description | Secret? |
|---|---|---|---|---|
| `DASHBOARD_HOST` | Optional | `127.0.0.1` | Dashboard 監聽位址；**不要**改成 `0.0.0.0` 對外公開，除非搭配額外的存取控制 | No |
| `DASHBOARD_PORT` | Optional | `8000` | Dashboard 監聽埠 | No |
| `DASHBOARD_AUTH_ENABLED` | Optional | `false` | 是否啟用簡易 Basic Auth | No |
| `DASHBOARD_USERNAME` | Optional（啟用 Auth 時必填） | — | Basic Auth 帳號 | Yes |
| `DASHBOARD_PASSWORD` | Optional（啟用 Auth 時必填） | — | Basic Auth 密碼 | Yes |
| `DASHBOARD_BASE_URL` | Optional | 空字串（不顯示連結） | Teams 訊息中「查看 Dashboard」連結使用的對外網址 | No |

## Database

| Name | Required? | Default | Description | Secret? |
|---|---|---|---|---|
| `MARITIME_DB_PATH` | Optional | `data/maritime_intelligence.db` | Event Store（Persistent Memory，唯一 Fatal 等級的資料庫） | No |
| `MARITIME_AI_CACHE_DB_PATH` | Optional | `data/ai_analysis.db` | LLM 分析快取 | No |
| `MARITIME_OPERATIONAL_HISTORY_DB_PATH` | Optional | `data/operational_relevance.db` | Fleet Exposure 歷史快照 | No |
| `MARITIME_DELIVERY_HISTORY_DB_PATH` | Optional | `data/delivery_history.db` | 各 channel 送達歷史 | No |
| `MARITIME_SOURCE_HEALTH_DB_PATH` | Optional | `data/source_health.db` | 新聞來源健康狀態 | No |
| `BACKUP_DIR` | Optional | `backup` | `scripts/backup_data.py` 備份輸出目錄 | No |
| `BACKUP_RETENTION_COUNT` | Optional | `14` | 保留最近 N 次備份 | No |

## Operational Data（Fleet / Schedule / Route Provider）

目前一律讀取本機設定檔，非環境變數：

- `config/fleet_config.json` — 船隊資料
- `config/schedules_config.json` — 船期／Port Call 資料
- `config/services_config.json` — 航線／Service 資料
- `config/ports_config.json` — 港口別名對照表

三個資料來源目前皆為空陣列佔位（見 `FUTURE_ROADMAP.md`／`docs/history/PHASE_8_FINAL_COMPLETION_REPORT.md` 的 Known Limitations）；正式使用前請以實際資料取代 `vessels`/`port_calls`/`services` 陣列。

## Logging

| Name | Required? | Default | Description | Secret? |
|---|---|---|---|---|
| `INTELLIGENCE_DEBUG` | Optional | `false` | `true` 時 console 也顯示 INFO 等級細節（預設 console 只顯示 WARNING 以上，詳細記錄在 `logs/maritime_intelligence.log`） | No |

## 其他

| Name | Required? | Default | Description | Secret? |
|---|---|---|---|---|
| `NEWS_HOURS_BACK` | Optional | `6` | 只抓取過去 N 小時內發布的新聞 | No |
| `SSL_VERIFY` | Optional | `true` | HTTPS 憑證驗證；正式環境務必維持 `true` | No |
| `MEMORY_BASELINE_MODE` | Optional | `notify`（或 `memory_rules.json` 設定） | 首次匯入既有新聞時的行為：`notify` 或 `silent` | No |

---

## 使用原則

1. `.env` 從不提交到版控（`.gitignore` 已排除）。
2. `.env.example` 是唯一應該被提交、被他人參考的範本，且**必須維持不含任何真實值**。
3. 新增任何環境變數時，請同步更新：程式碼、`.env.example`、本文件三處，避免文件與實作分裂（見 Phase 8 §四十八 Single Source of Truth 原則）。
