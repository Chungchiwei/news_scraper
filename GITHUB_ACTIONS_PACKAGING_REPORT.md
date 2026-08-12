# GitHub Actions Packaging Report — WHL Maritime Intelligence System

## 1. Package Version

`1.0.0-rc1`（沿用 Phase 8 版本，本輪不變更版本號 — 純 Packaging，不是
功能開發；`VERSION` / `version.py` 未修改）。

## 2. GitHub Package Path

```
dist/github_package/
```

## 3. ZIP Path

```
dist/WHL_Maritime_Intelligence_GitHub_v1.0.0-rc1.zip
```

## 4. Included Files Count

**152 個檔案**（由 `scripts/build_github_package.py` 每次執行時重新計算並
印出；完整分類見 `GITHUB_PACKAGE_MANIFEST.md`）。

## 5. Excluded Files

`.env`、`*.db`/`*.db-wal`/`*.db-shm`/`*.db-journal`、`news_scraper.log`/
`*.log`、`__pycache__/`、`.pytest_cache/`、`logs/`、`backup/`、`output/`、
`dist/`、`venv/`。詳細分類理由見 `GITHUB_PACKAGING_AUDIT.md`。

## 6. Secret Scan Result

**PASS**。`scripts/build_github_package.py` 對每個候選檔案做兩層檢查：
(a) 檔名/副檔名 fail-closed 名單（`.env`、`*.db*`、`*.pem`、`*.key`），
(b) 內容 fail-closed 正則掃描（OpenAI/Anthropic/Google 風格 API Key、
PEM Private Key Block、含 Token 的完整 Teams Webhook URL），加上針對
`.env.example` 的專屬檢查（機敏欄位一律必須是空值）。任何一項命中都會
讓 build **立即 `sys.exit(1)`**，不是 Warning——本輪多次執行 build，
每次都是 `SECRET SCAN: PASS`。組好 package 後，另外對整個 `dist/github_package/`
目錄再跑一次同樣的檢查（defense-in-depth）。

## 7. Required Secrets（Exact Names）

`MAIL_USER`、`MAIL_PASSWORD`、`TARGET_EMAIL`（必填）；
`TEAMS_MANAGEMENT_WEBHOOK_URL`、`TEAMS_SYSTEM_WEBHOOK_URL`（僅
`TEAMS_ENABLED=true` 時）；`ANTHROPIC_API_KEY`、`OPENAI_API_KEY`（僅
`LLM_ENABLED=true` 時，依 Provider 二擇一）。

## 8. Required Variables（Exact Names）

`TEAMS_ENABLED`、`LLM_ENABLED`、`LLM_PROVIDER`、`LLM_MODEL`、
`LLM_ALLOW_INTERNAL_OPERATIONAL_DATA`、`SEND_NO_RISK_BRIEF`、
`MAIL_SMTP_SERVER`、`MAIL_SMTP_PORT`、`MAIL_SEND_MAX_ATTEMPTS`、
`MAIL_SEND_RETRY_WAIT_SEC`、`NEWS_HOURS_BACK`、`SSL_VERIFY`、
`INTELLIGENCE_DEBUG`。全部皆有安全預設值（見 `.env.example`），不設定
也能執行。

## 9. CI Workflow

**Triggers**：`push`（任何分支）、`pull_request`（任何分支）。
**Python**：3.11.9（明確 pin，非浮動版本）。
**Tests**：`pip install -r requirements.txt -r requirements-dev.txt` →
`py_compile`（全部 production 模組）→ `pytest -q`（201 項）。完全離線，
不注入任何 Secret（`ci_workflow_has_no_secrets` 測試與
`scripts/github_actions_smoke_test.py` 皆驗證過 `secrets.` 字串不出現
在 `ci.yml` 裡）。Fork/PR 執行時同樣沒有 Secret 可用，不會、也不能寄信
或發 Teams。

## 10. Production Workflow

**Triggers**：`workflow_dispatch`（手動）、`schedule`（`cron: "17 */6 * * *"`，
UTC，對應 Asia/Taipei 約 08:17/14:17/20:17/02:17）。**刻意不使用
`on: push`**——改一行文件不會觸發主管 Email（`workflow_yaml` 測試 +
smoke test 皆驗證 `push` 未出現在 trigger 清單）。**Manual Run**：
`workflow_dispatch` 無額外必填 input，Actions 分頁「Run workflow」即可
手動觸發，供 Step 8-10 部署驗收使用（見 `GITHUB_ACTIONS_SETUP.md`）。

## 11. Runtime State Persistence

**Restore**：Job 一開始用 `gh api repos/.../actions/artifacts --paginate`
列出所有未過期、名稱前綴為 `maritime-runtime-state-` 的 artifact，依
`created_at`（不是檔名字母順序）排序取最新一筆，透過
`actions/download-artifact@v8` 的 `artifact-ids` + `github-token`
（`${{ github.token }}`，不需要 PAT，只需要 workflow 宣告
`permissions: actions: read`）還原到 `data/`，還原後用 Python `sqlite3`
的 `PRAGMA integrity_check` 逐一驗證。

**Run**：`python maritime_news.py`（`continue-on-error: true`，exit code
保留，不吞掉）。

**Save**：Run 結束後（無論 Email/Teams 成功與否）先對 `data/*.db` 再做一次
integrity check，通過才進「Stage state files for upload」——這一步
**改用 SQLite Online Backup API**（`sqlite3.Connection.backup()`），不是
天真的 `cp`。原因：`event_store.py` 等模組用 `PRAGMA journal_mode=WAL`，
已提交的資料可能還留在 `*.db-wal` 尚未 checkpoint 回主檔案，單純複製
主檔案可能複製到不完整的快照——**這個問題在
`scripts/github_actions_smoke_test.py` 的 Runner A/B 模擬中被實際重現
過一次（第一版用 `shutil.copy2`，Runner B 誤判成新事件），修正後
（改用 Online Backup API）才通過**，因此 workflow 本身也採用同樣的
修正，不是紙上談兵。驗證通過的 DB 才用
`actions/upload-artifact@v7` 存成 `maritime-runtime-state-{run_id}`
（依 run id 唯一命名，不會被下一次 Run 覆蓋）。

## 12. First-Run Behavior

找不到任何未過期的 `maritime-runtime-state-*` artifact（或全部過期）→
`found=false`，**不是 Error**，Job 繼續執行。這種情況下 workflow 會設定
`MEMORY_BASELINE_MODE=silent`（沿用既有 Phase 3 `memory_pipeline.py` 的
`is_baseline_run`/`baseline_mode` 機制，不是另外發明一套邏輯）——實測
（`tests/test_github_packaging.py::test_first_github_run_initializes_state`
與 `scripts/github_actions_smoke_test.py` Section 4）確認：
`is_baseline_run=True`、`notification_events=[]`，不會把既有新聞當
NEW 轟炸主管信箱。

## 13. Second-Run Behavior

還原到上一次的 State 後，同一則新聞／事件再次被抓到：`event_id` 不變、
`notification_state` 從 `NEW` 變成 `UNCHANGED`，Duplicate Teams
不會重複發送。實測見下方 §21 Two-Runner Simulation。

## 14. Concurrency Protection

```yaml
concurrency:
  group: maritime-intelligence-production
  cancel-in-progress: false
```

同一時間最多一個 Production Run；新 Run 排隊等待，而不是取消正在執行
的舊 Run（避免 State 半寫）。`test_concurrency_enabled` 與 smoke test
皆驗證這兩個欄位存在且值正確。

## 15. Artifact Retention

Runtime State artifact：**30 天**（`retention-days: 30`）。失敗診斷 log
artifact：**7 天**（`retention-days: 7`），且只在
`steps.run_main.outcome == 'failure'` 時才上傳——成功的 Run 不會累積
大量 log artifact。

## 16. SMTP Compatibility

**`SMTP GitHub-hosted connectivity requires IT validation.`** 本專案
`email_sender.py` 用標準 `smtplib` 連線，理論上只要目標 SMTP 伺服器
（`.env.example` 預設 `smtp.gmail.com:587`）允許一般網際網路來源連線，
GitHub-hosted Runner（`ubuntu-latest`，公開 IP、無固定 IP 範圍）就能
連得到。**但如果貴公司改用企業內部 SMTP、或對外部 SMTP 有 IP
allowlist 限制，GitHub-hosted Runner 極可能連不上**——這件事無法在
本次離線 Packaging 階段驗證，需要 IT 在 Step 8（第一次 Manual Run）
實際測試確認。本輪**沒有**為了讓 Runner 連得到而做任何繞過公司資安
限制的事（例如要求開防火牆白名單以外的做法），這個決定留給 IT。

## 17. Internal Operational Data Compatibility

`config/fleet_config.json` / `schedules_config.json` / `services_config.json`
目前皆為空陣列 Placeholder（見 `GITHUB_PACKAGING_AUDIT.md` §A），因此
GitHub Actions 執行時 Operational Relevance 引擎會誠實顯示「Fleet
Provider READY (0 vessels)」——不是錯誤，是「目前沒有可比對的內部船隊
資料」。**Full Operational Relevance requires a secure internal data
provider.**（若未來要接公司內部 Fleet/Schedule API，這個 API 多半只能
公司內網存取，GitHub-hosted Runner 可能無法連線——本輪不解決這個問題，
也不會為了讓 Runner 連得到而把內網 API 暴露到公開網路。）新聞/事件
Email 本身不受影響，仍可正常判讀、評分、寄送，只是不會顯示 WHL Direct/
High Exposure 相關內容。

## 18. Dashboard Limitation

**GitHub Actions 只負責 Scheduled Intelligence Cycle，不是持久化的
Dashboard Hosting。** `dashboard/` 原始碼完整保留在 Repository 裡（供
未來部署到公司內部/Azure/其他常駐主機使用），但 `maritime-intelligence.yml`
**不會**啟動 `uvicorn`/FastAPI Server——GitHub Actions Job 執行完就會
結束，讓一個 Job 常駐等待請求不符合 GitHub Actions 的設計方式，也不會
部署到 GitHub Pages（Dashboard 含內部營運資訊，不適合公開靜態網站）。
Dashboard Hosting 需求留給 `IT_DEPLOYMENT_GUIDE.md` 既有的「常駐主機」
建議，本輪不解決。

## 19. Tests

| 項目 | 數量 |
|---|---|
| Baseline（Phase 1-8） | 187 |
| GitHub Packaging（Phase 9，本輪新增） | 14 |
| **Total** | **201** |
| Passed | 201 |
| Failed | 0 |

新增的 14 項測試在 `tests/test_github_packaging.py`（package manifest／
secret exclusion／workflow YAML 有效性與安全政策／runtime state
manifest／first-run／second-run／corrupt state／dependency closure）。
`scripts/github_actions_smoke_test.py` 額外提供 28 項獨立離線驗證
（可重複執行，不算進 pytest 計數，是給人工在部署前再跑一次的
「總彩排」腳本）。

## 20. Clean Package Validation

在完全獨立的暫存目錄（`/tmp/clean_clone`，跟開發目錄無任何關聯）：

1. 複製 `dist/github_package/` 的內容進去（不是本機開發目錄的軟連結）。
2. 建立**全新的 Python venv**，執行
   `pip install -r requirements.txt -r requirements-dev.txt`——**真的
   從網路下載安裝**，不是重用現有環境。
3. 在這個乾淨環境裡執行：
   - `python -m pytest -q` → **201 passed**
   - `python scripts/health_check.py`（未設定 Email 變數）→
     `OVERALL: NOT READY`，exit 1（正確行為）；補上假的
     `MAIL_USER`/`MAIL_PASSWORD`/`TARGET_EMAIL` 後 → `OVERALL: READY`，
     exit 0
   - `python scripts/final_acceptance_test.py` → **RESULT: PASS**
     （14/14 階段）
   - `python scripts/github_actions_smoke_test.py` → **28/28 checks
     passed，RESULT: PASS**

過程中發現並修正一個真實問題：`tests/test_github_packaging.py` 用到
`yaml` 套件解析 workflow，但 `requirements-dev.txt` 原本沒有列
`pyyaml`——本機沙箱環境剛好預裝了 pyyaml，導致問題沒有在本機被發現，
直到乾淨環境重新 `pip install` 才顯形（3 項測試在乾淨環境中
`ModuleNotFoundError: No module named 'yaml'`）。已修正：把 `pyyaml>=6.0`
加進 `requirements-dev.txt`（只給測試/工具用，不影響 production
`requirements.txt`），修正後乾淨環境重新安裝、重新測試，201/201 全過。
**這正是「在乾淨環境重新驗證」這個步驟本身的價值**——證明 Package
沒有偷偷依賴 Package 外的檔案或本機環境的巧合。

## 21. Two-Runner Persistence Simulation（Local Mock）

用 `tests/test_github_packaging.py::test_second_github_run_restores_state`
與 `scripts/github_actions_smoke_test.py` Section 4 兩種方式各自獨立
模擬（皆用真正的 Phase 3 pipeline：`EventExtractor` → `RiskScorer` →
`EventClusterer` → `apply_persistent_memory`，不是假資料）：

```
Runner A（完全空的 data/，全新暫存目錄）
  → 第一次執行同一則「Kaohsiung 飛彈攻擊」測試新聞
  → is_baseline_run = True
  → baseline_mode = silent（模擬 workflow 偵測「找不到 state」時的注入行為）
  → notification_events = []（不通知，避免轟炸信）
  → event_id = evt_78a1a2be17a4b169

  ↓ SAVE STATE：sqlite3 Online Backup API（不是天真 cp，見 §11）
  ↓ 複製到「artifact staging」暫存目錄（模擬 upload-artifact）
  ↓ 再複製到 Runner B 的暫存目錄（模擬 download-artifact，完全不同的
    檔案系統路徑，Runner B 對 Runner A 的存在一無所知，只認得那個
    還原回來的 .db 檔案）

Runner B（全新、互不相干的暫存目錄，還原 Runner A 的 State）
  → 第二次執行同一則新聞
  → is_baseline_run = False   ✅ state 有被正確辨識
  → event_id = evt_78a1a2be17a4b169   ✅ 跟 Runner A 完全相同
  → notification_state = UNCHANGED    ✅ 不是 NEW，Duplicate 被抑制
```

**結果：PASS。** 兩種獨立實作方式（pytest 版與 smoke-test 版）都得到
一致結果，且都在真正遇到 WAL checkpoint 陷阱後修正過一次——不是理論上
應該可行，是實際跑出來確認可行。

若要模擬 Corrupted State：`scripts/github_actions_smoke_test.py`
Section 5 與 `tests/test_github_packaging.py::test_corrupt_state_rejected`
會把一個 `.db` 檔案寫成純亂數 bytes，確認 `PRAGMA integrity_check`
能正確判定為損毀（不是誤判成可用）——對應 workflow 裡「還原後
integrity check 失敗就直接 fail、不覆蓋舊 artifact」的邏輯。

---

## 附錄：本輪對既有程式碼的修改（§七十三要求逐項列出）

**沒有修改任何 Risk Scoring / Clustering / LLM Analysis / Operational
Relevance / Dashboard Layout / Delivery Logic。** 本輪對「既有」檔案的
修改僅限：

| 檔案 | 修改內容 | 原因 |
|---|---|---|
| `.gitignore` | 新增 `dist/` | Package Builder 的輸出目錄不該進版控 |
| `requirements-dev.txt` | 新增 `pyyaml>=6.0` | 測試需要解析 workflow YAML；Clean Package Validation 過程中發現本機環境巧合預裝、乾淨環境會缺少 |

其餘全部是**新增檔案**（`.github/workflows/*.yml`、
`config/github_state_files.json`、`data/.gitkeep`、
`scripts/build_github_package.py`、`scripts/github_actions_smoke_test.py`、
`tests/test_github_packaging.py`、本文件與另外 3 份 GitHub Packaging
文件），沒有任何一行改動 `maritime_news.py` 或任何 Phase 1-8 既有的
業務邏輯模組。

---

## 最終結論

```
GitHub Package:              READY
ZIP:                         READY
Secret Scan:                 PASS
CI:                          PASS
GitHub Actions Workflow:     VALID
Runtime State Persistence:   PASS
Two-Runner Simulation:       PASS
Baseline Tests:              PASS (201/201)
```

完成後不再新增 Phase，不建議 Weather/AIS/Deployment Platform 等新功能
（依 Feature Freeze 原則，見 `FUTURE_ROADMAP.md`）。本輪**不會**執行
`git init`/`git push`——只交付 `dist/github_package/` 與
`WHL_Maritime_Intelligence_GitHub_v1.0.0-rc1.zip`，由使用者自行決定
何時、以何種方式上傳到 GitHub。
