# GitHub Actions Setup Guide — WHL Maritime Intelligence System v1.0.0-rc1

給實際要把系統部署到 GitHub Actions 的人看的 Step-by-Step 指南。跟著做完
Step 1-10，系統就會開始按排程自動執行。

> **重要限制**：GitHub Actions 這裡只負責「排程執行 `maritime_news.py`」，
> 不負責 Dashboard Hosting（見本文件最下方「GitHub Actions 的限制」）。

---

## Step 1 — 建立 Private Repository

到 GitHub 建立一個新的 **Private** Repository（例如
`whl-maritime-intelligence`）。務必選 Private——即使 Package 本身不含
任何機敏資料，Runtime State Artifact 執行後會包含內部事件記憶/Fleet
Exposure History，見下方「Runtime State 是機敏資訊」。

## Step 2 — Upload `github_package/` 內容

把 `dist/github_package/`（或解壓 `WHL_Maritime_Intelligence_GitHub_v1.0.0-rc1.zip`
後的內容）的**所有檔案**上傳到剛建立的 Repository 根目錄。解壓/上傳後，
Repository 根目錄應該直接看到 `maritime_news.py`、`requirements.txt`、
`.github/` 等檔案，不能有多一層資料夾包起來。

可以用 GitHub 網頁介面拖曳上傳，或用 `git init` + `git add` + `git commit`
+ `git push`（本輪不會替你執行這步，見 docs/history/GITHUB_ACTIONS_PACKAGING_REPORT.md
「完成後不要 Push」）。

## Step 3 — 到 Secrets and variables 設定頁

Repository 上方 **Settings** → 左側 **Secrets and variables** → **Actions**。

## Step 4 — 新增 Required Secrets

點 **New repository secret**，逐一新增以下項目（名稱必須完全一致）：

| Secret 名稱 | 必填 | 說明 |
|---|---|---|
| `MAIL_USER` | ✅ 是 | 寄件 Email 帳號 |
| `MAIL_PASSWORD` | ✅ 是 | Email 密碼／App Password（Gmail 請用應用程式密碼，不是登入密碼） |
| `TARGET_EMAIL` | ✅ 是 | 收件人（主管）信箱，多人可用逗號分隔（依 email_sender.py 實際支援格式） |
| `TEAMS_MANAGEMENT_WEBHOOK_URL` | 只有 `TEAMS_ENABLED=true` 時才需要 | Teams 頻道 Webhook URL |
| `TEAMS_SYSTEM_WEBHOOK_URL` | 只有 `TEAMS_ENABLED=true` 時才需要 | 系統/Admin 通知用 Teams Webhook URL |
| `ANTHROPIC_API_KEY` | 只有 `LLM_ENABLED=true` 且 `LLM_PROVIDER=anthropic` 時才需要 | Claude API Key |
| `OPENAI_API_KEY` | 只有 `LLM_ENABLED=true` 且 `LLM_PROVIDER=openai` 時才需要 | OpenAI API Key |

未設定 `MAIL_USER`/`MAIL_PASSWORD`/`TARGET_EMAIL` 三者中任何一項，
`scripts/health_check.py`（workflow 的 Preflight 步驟）會直接讓這次
Run 失敗，並清楚標示缺少哪個變數——不會執行到一半才失敗。

## Step 5 — 新增 Variables（非機敏，可選）

同一個頁面切到 **Variables** 分頁，點 **New repository variable**。
以下全部有安全的預設值（不設定也能跑），只有需要偏離預設行為時才需要新增：

| Variable 名稱 | 預設值 | 說明 |
|---|---|---|
| `TEAMS_ENABLED` | `false` | 設為 `true` 才會啟用 Teams 通知（同時需要上面兩個 Webhook Secret） |
| `LLM_ENABLED` | `false` | 設為 `true` 才會啟用 LLM Enhanced Summary（同時需要對應 API Key Secret）。**本輪 GitHub Migration 刻意不順便開啟**，維持 Phase 8 的預設關閉狀態 |
| `LLM_PROVIDER` | `disabled` | `anthropic` 或 `openai`（只有 `LLM_ENABLED=true` 時有意義） |
| `LLM_MODEL` | （空） | 依 Provider 指定模型名稱 |
| `LLM_ALLOW_INTERNAL_OPERATIONAL_DATA` | `false` | 是否允許把清洗過的內部營運資料傳給外部 LLM API |
| `SEND_NO_RISK_BRIEF` | `true` | 沒有重大風險事件時是否仍寄出日報 |
| `MAIL_SMTP_SERVER` | `smtp.gmail.com` | SMTP 伺服器 |
| `MAIL_SMTP_PORT` | `587` | SMTP 埠號 |
| `MAIL_SEND_MAX_ATTEMPTS` | `3` | Email 寄送失敗重試次數 |
| `MAIL_SEND_RETRY_WAIT_SEC` | `15` | 重試間隔秒數 |
| `NEWS_HOURS_BACK` | `6` | 只抓取過去 N 小時內發布的新聞（建議與排程間隔一致） |
| `SSL_VERIFY` | `true` | HTTPS 憑證驗證，正式環境務必維持 `true` |
| `INTELLIGENCE_DEBUG` | `false` | 除錯用，正式環境維持 `false` |

## Step 6 — 啟用 Actions

Repository → **Actions** 分頁。如果看到「Workflows aren't being run on
this forked repository」或類似的停用提示，點擊啟用（一般新建的 Private
Repository 預設就是啟用的，不用特別做什麼）。

## Step 7 — 先執行 CI，確認 Tests Passed

Actions 分頁應該會看到兩個 workflow：`CI` 與 `Maritime Intelligence`。
`CI` 在你剛剛的 Upload/Push 之後應該已經自動跑過一次（因為 `ci.yml` 的
trigger 包含 `push`）。點進去確認：

- Python 3.11.9 安裝成功
- `pytest -q` 全部通過（201 項）
- `py_compile` 全部通過
- 這個 workflow **完全沒有用到任何 Secret**（點開 log 也看不到任何密碼/Webhook）

## Step 8 — 手動執行 Maritime Intelligence（第一次，Manual）

Actions 分頁 → 左側選 **Maritime Intelligence** → 右上 **Run workflow**
→ 選分支（通常是 `main`）→ **Run workflow**。

這是 `workflow_dispatch` trigger，讓你在正式排程開始前先手動確認一次。

## Step 9 — 第一次執行必須確認的事

點進剛剛觸發的 Run，檢查：

- **Job Summary**（Run 頁面最上方）顯示 `Runtime State Restored: FIRST RUN
  (none to restore)` —— 這是正常的，不是錯誤。
- Console log 或 Job Summary 顯示 baseline import 相關訊息（
  `MEMORY_BASELINE_MODE=silent`），代表第一次執行把當下抓到的新聞當作
  「建立基準」，**不會**寄出一封塞滿所有既有新聞的轟炸信。
- `Runtime State Saved: SAVED`（除非這次 Run 本身失敗）。
- Email 有沒有正確寄出（依你的 `SEND_NO_RISK_BRIEF` 設定，如果當下沒有
  重大事件、又設為 `false`，這次可能刻意不寄信，這也是正常的）。

## Step 10 — 第二次手動執行，確認 State 有正確還原

再次 **Run workflow**（Step 8 的按鈕）。這是整個 GitHub Migration
**最重要的人工驗收**：

- Job Summary 應顯示 `Runtime State Restored: RESTORED`（不是 FIRST RUN）。
- 如果這次抓到的新聞跟上一次是同一批（或同一事件的重複報導），Job
  Summary / Email 不應該把它們當成全新事件重複通知一次。
- 如果你想更嚴謹驗證，可以到 Actions 分頁的 **Artifacts** 區塊確認
  `maritime-runtime-state-<run_id>` 這個 artifact 確實存在，且新一次
  Run 的 log 有出現「✅ Will restore from: maritime-runtime-state-...」。

兩次都確認沒問題後，系統就已經準備好交給排程自動執行了。

## Step 11 — 啟用排程

排程已經寫在 `.github/workflows/maritime-intelligence.yml` 裡（預設
`cron: "17 */6 * * *"`，UTC 每 6 小時一次），**不需要額外操作**——
`schedule` trigger 只要 workflow 檔案存在於預設分支就會自動生效。
如果貴公司有明確的正式排程需求（例如避開特定時段），直接編輯
`maritime-intelligence.yml` 裡的 cron 表達式（純文字修改，不需要改
任何 Python 程式碼）。

---

## Runtime State 是機敏資訊

`maritime-runtime-state-*` Artifact 一旦系統實際執行過，會包含事件記憶、
WHL Fleet Exposure History、Delivery History 等內部營運資訊。這是本
Repository 選擇 **Private** 的原因之一；即使是 Private Repository，這些
Artifact 也只由 workflow runtime 產生/還原，**絕對不會**被寫回 Git
版控（見 docs/history/GITHUB_ACTIONS_PACKAGING_REPORT.md §Runtime State Persistence）。

## GitHub Actions 的限制

- **GitHub Actions 只負責排程執行 `maritime_news.py`**（新聞抓取 → 風險
  評分 → Email/Teams 通知）。
- **Dashboard（FastAPI，`dashboard/app.py`）不會被這個 workflow 啟動**。
  Dashboard 需要常駐主機才能正常運作，GitHub Actions Job 執行完就會
  結束，不適合拿來跑一個永遠不下線的網頁伺服器。如果需要 Dashboard，
  請參考 `IT_DEPLOYMENT_GUIDE.md` 部署到公司內部主機。
- 如果貴公司的 SMTP 伺服器或 Fleet/Schedule Provider 只能透過公司內網
  存取，GitHub-hosted Runner（`ubuntu-latest`）**可能無法連線**——這件
  事需要 IT 協助驗證，見 `docs/history/GITHUB_ACTIONS_PACKAGING_REPORT.md` §SMTP
  Compatibility / §Internal Operational Data Compatibility。本次
  Packaging 不會、也不應該為了讓 GitHub Actions 連得到而把公司內網
  服務暴露到公開網路。
