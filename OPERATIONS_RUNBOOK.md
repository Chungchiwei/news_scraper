# Operations Runbook

給每天實際操作／維運這個系統的人看。遇到問題時，先照這份文件的順序排查。

---

## Daily Operation

**如何啟動**：雙擊 `run.bat`（或依排程機制在固定時間執行 `python maritime_news.py`，見 `IT_DEPLOYMENT_GUIDE.md`）。

**如何確認成功**：`run.bat` 結束時會印出：

```
════════════════════════════════════
WHL MARITIME INTELLIGENCE
RUN COMPLETE
════════════════════════════════════
...
Result:
SUCCESS
════════════════════════════════════
```

搭配視窗上方的 `Completed successfully.`。若看到 `Execution failed. See log for details.` 或 `RUN FAILED`，見下方對應章節。

**如何查看 Dashboard**：雙擊 `run_dashboard.bat`，瀏覽器開啟 `http://127.0.0.1:8000`。Dashboard 是唯讀的，可以隨時開著，不影響 `run.bat` 的執行。

**如何查看 Log**：`logs/maritime_intelligence.log`（每日自動 rotate，保留 14 天）。Console 只顯示精簡摘要，完整細節都在這個檔案裡。

---

## Email Failure

**症狀**：`run.bat` 顯示 `RUN FAILED`，`Critical Component` 為 `Email Configuration` 或 `Email Delivery (SMTP)`。

1. 執行 `python scripts/health_check.py`，確認 `Email` 那一列是否為 `CONFIGURED`。
2. 若顯示 `NOT CONFIGURED`：檢查 `.env` 的 `MAIL_USER` / `MAIL_PASSWORD` / `TARGET_EMAIL` 是否都有填。
3. 若已設定但仍寄送失敗：確認 `MAIL_SMTP_SERVER` / `MAIL_SMTP_PORT` 是否正確；若使用 Gmail，`MAIL_PASSWORD` 必須是「應用程式密碼」而非登入密碼。
4. 查看 `logs/maritime_intelligence.log` 內的完整 SMTP 錯誤訊息（例如認證失敗、連線逾時）。
5. 系統已內建重試（預設 3 次，見 `MAIL_SEND_MAX_ATTEMPTS`），若重試後仍失敗才會判定為失敗。

## Teams Failure

**症狀**：Dashboard 或 Log 顯示 Teams 送達失敗；`run.bat` 的 CLI Summary 顯示 `Teams: FAILED` 或 `PARTIAL`。

1. 確認 `TEAMS_ENABLED=true` 是否確實設定（未設定或設 `false` 時，Teams 完全不送，這是正常行為，不是錯誤）。
2. 確認 `TEAMS_MANAGEMENT_WEBHOOK_URL`（與需要時的 `TEAMS_SYSTEM_WEBHOOK_URL`）是否正確、未過期。
3. 查看送達歷史：`data/delivery_history.db`（可用任何 SQLite 工具開啟，或透過 Dashboard 的 Events 頁面查看個別事件的送達狀態）。
4. Teams 失敗**不影響** Email 是否送出——兩者是獨立管道，這是刻意設計（見 `SYSTEM_ARCHITECTURE.md` DELIVERY 章節）。

## Database Failure

**症狀**：`run.bat` 顯示 `RUN FAILED`，`Critical Component` 為 `Event Database`；或 `scripts/health_check.py` 的 `Event Store` 顯示 `FAIL`。

還原步驟（Event Store 是唯一 Fatal 等級的資料庫，其餘 4 個資料庫失敗只會讓對應功能退化，不需要走這個流程）：

1. **停止程式**：確認沒有 `maritime_news.py` 或 `dashboard/app.py` 正在執行。
2. **備份目前狀態**：`python scripts/backup_data.py`（即使目前的資料庫已經壞掉，也先備份一份，避免還原時弄丟其他還能用的資訊）。
3. **還原選定版本**：從 `backup/<時間戳記>/maritime_intelligence.db` 複製回 `data/maritime_intelligence.db`（本步驟目前為手動操作，刻意不自動化，避免誤還原到錯誤的時間點）。
4. **執行健康檢查**：`python scripts/health_check.py`，確認 `Event Store` 顯示 `PASS`。
5. **重新啟動**：雙擊 `run.bat`。

## Fleet Exposure Unavailable

**症狀**：Email 或 Dashboard 的 Fleet Exposure 區塊顯示 `UNAVAILABLE` 或整段不顯示。

1. 執行 `python scripts/health_check.py`，檢查 `Fleet Provider` / `Schedule Provider` / `Route Provider` 三列狀態。
2. 確認 `config/fleet_config.json` / `config/schedules_config.json` / `config/services_config.json` 是否存在、格式正確（合法 JSON）。
3. 確認這些檔案內的資料時效性（`generated_at_utc` 欄位）——目前這三個資料來源是本機靜態設定檔，需要人工定期更新（見 `FUTURE_ROADMAP.md` 關於未來串接內部系統的規劃）。
4. Fleet Exposure 資料不可用時，Email/Teams/其他功能都不受影響，只有曝險評估這一段會標示 UNAVAILABLE，不會被誤判為「沒有曝險」。

## Source Failure

**症狀**：Log 出現特定新聞來源的抓取錯誤（例如 timeout、HTTP 錯誤）。

1. **這通常不需要處理**——單一來源失敗不會影響其他來源，也不會讓整次執行失敗（見 `SYSTEM_ARCHITECTURE.md` COLLECTION 章節設計原則）。
2. 若想確認來源健康狀態的長期趨勢，可查看 `data/source_health.db`。
3. 若某來源連續失敗（例如網站改版、URL 失效），考慮更新 `RSS_SOURCES`／對應設定，或暫時停用該來源。
4. 若想單獨確認網路連線本身是否正常（而非個別來源問題），可手動執行 `python scripts/production_smoke_test.py`（僅測連線與資料庫可寫入，不送真實 Email/Teams，預設不會自動執行）。
