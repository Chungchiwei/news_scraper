# Deprecated Code — Do Not Use

本文件記錄專案中「已過時、不應在新開發中使用」的程式碼。依 Phase 8 原則，**過時程式碼不代表可以刪除**——只有在確認無 import/runtime/test/config 依賴時才會搬移至 `legacy/`；若移動風險過高（例如仍被其他模組 import），則保留原位並在此標記。

---

## 1. `legacy/news_scraper.py`（原路徑：專案根目錄 `news_scraper.py`）

- **狀態**：Do Not Use — 已搬移至 `legacy/`
- **原因**：Phase 1 之前的獨立單檔爬蟲＋SMTP 寄送實作，自帶 `smtplib`/`MIMEText` 邏輯，完全不依賴 Phase 2 以後建立的任何模組（`event_extractor.py`、`risk_scorer.py`、`memory_pipeline.py`、`executive_email_renderer.py` 等）。
- **確認安全搬移的依據**：`grep -rn "import news_scraper\|from news_scraper"` 全專案（排除 `venv/`）結果為零筆，僅 `claude.md`／`docs/history/PHASE2_REPORT.md` 有敘述性文字提及，非程式碼引用。
- **替代方案**：`maritime_news.py`（唯一 production entry point）。

## 2. `legacy/news_scraper_2024.04.16.py`（原路徑：`SPARE/news_scraper_2024.04.16.py`）

- **狀態**：Do Not Use — 已搬移至 `legacy/`
- **原因**：比 `news_scraper.py` 更早的日期備份版本，同樣無任何程式引用。
- **替代方案**：`maritime_news.py`。

## 3. `email_sender.py` 內的 `EmailRenderer` class 與 `NewsEmailSender.send()` 方法

- **狀態**：⚠️ Deprecated in-place — **不搬移、不刪除**
- **原因**：這是 Phase 1-3 的舊版「新聞列表」HTML 渲染器與寄送路徑。Phase 4 起，主管日常 Email 一律改用 `executive_email_renderer.py` 的 `ExecutiveEmailRenderer` + `NewsEmailSender.send_html()`。
- **為何不能移除**：程式內部 docstring 已明確標註「本類別保留但不再是 production 預設路徑，不得刪除（不完全取代前不可刪除舊版 Renderer）」。目前仍作為緊急 fallback 路徑保留，具有 production 備援價值。
- **正確用法**：新開發一律使用 `send_html()` + `ExecutiveEmailRenderer`；`send()`／`EmailRenderer` 僅供緊急備援，不建議在新程式中呼叫。
- **後續規劃**：延後至 v1.1 再評估是否移除，記錄於 `FUTURE_ROADMAP.md`。

## 4. `news_scraper.log`（原專案根目錄，已刪除）

- **狀態**：已刪除（Phase 9 專案整理）
- **原因**：`news_scraper.py`（舊版）時代產生的執行紀錄，與現行 `maritime_news.py` 的 log 輸出（`logs/maritime_intelligence.log`）無關；內容含舊版執行時期的真實內部信箱等過期資訊，已無保留價值。
- **確認安全刪除的依據**：`grep -rn` 全專案（排除 `venv/`）確認沒有任何 `.py`／測試／config／文件把它列為必要元件；`.gitignore` 原本就已排除所有 `*.log`，代表它從未被視為需要版控保存的檔案。
- **後續**：若需要查詢現行系統的執行紀錄，改看 `logs/maritime_intelligence.log`（`OPERATIONS_RUNBOOK.md` 有說明）。

---

## 判斷原則（供未來維護者參考）

只有在同時滿足以下四項時，才可將程式碼視為安全可搬移／刪除：

1. 沒有任何 `.py` 檔案 import 它
2. 沒有任何 runtime 路徑會呼叫到它（例如 `run.bat`、`dashboard/app.py`、GitHub Actions／排程）
3. 沒有任何測試依賴它
4. 沒有任何 config／文件將它列為現行必要元件

「看起來沒用」不等於「確認沒用」——搬移前務必實際執行上述四項確認（例如全專案 `grep`）。
