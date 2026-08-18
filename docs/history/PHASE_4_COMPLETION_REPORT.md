# Phase 4 Completion Report — Executive Maritime Intelligence Email & Management Briefing Layer

**日期**：2026-08-11
**範圍**：只新增 Email 展示層（Selector → Summary → View Model → Renderer → SMTP），完全不修改 Phase 1-3 的 Crawler / Intelligence Core / Persistent Event Memory。

---

## 1. New Files

| 檔案 | 職責 |
|---|---|
| `email_rules.json` | Phase 4 所有門檻/白名單設定（daily_brief 上限、alert 規則、industry watch 篩選、fleet relevance 分級、subject 前綴、公司資訊）。不寫死在 Python。 |
| `email_config.py` | `load_email_rules()`，載入 `email_rules.json`（lru_cache），跟 `risk_config.py`/`memory_config.py` 同一種模式。 |
| `briefing_selector.py` | `BriefingSelector` — 把這次 run 的完整事件列表分成 immediate(P1)/watch(P2)/industry(P3)/resolved/suppressed 五桶，並依 Priority → Own Fleet → Score 排序、依 `daily_brief` 設定的上限做 overflow 裁切。 |
| `management_summary.py` | `ManagementSummaryBuilder` — Rule-Based 繁中文字產生器：`management_headline()` / `management_summary()` / `why_it_matters()` / `what_changed()`。另有 `translate_change_reason()` 把 Phase 3 `change_reason` 英文片語轉繁中條列。 |
| `email_view_model.py` | `EmailEventViewModel` / `ExecutiveBriefViewModel` 兩個純資料 dataclass + `build_daily_brief_view_model()` / `build_alert_view_model()` builder。Overall Risk、Fleet Relevance 分級、Impact Tags 裁切、Subject Line 皆在此算完，Renderer 不再計算任何風險邏輯。 |
| `executive_email_renderer.py` | `ExecutiveEmailRenderer` — `render_daily_brief()` / `render_alert()`，純 table-based inline CSS HTML 組裝 + HTML escape + URL scheme allow-list。 |
| `preview_email.py` | 本機預覽產生器，手造 5 種情境的 fixture事件，輸出 5 個 HTML 到 `output/`，不連線 SMTP。 |
| `tests/test_phase4_email.py` | 24 項指定測試。 |
| `PHASE_4_COMPLETION_REPORT.md` | 本報告。 |

## 2. Modified Files

| 檔案 | 修改內容 | 原因 |
|---|---|---|
| `email_sender.py` | ①`EmailRenderer` class 加上 `⚠ DEPRECATED` docstring，**未刪除**、行為完全不變。②`NewsEmailSender.__init__` 的 `incident_categories`/`rss_sources`/`cnyes_sources` 改為 optional（未提供時 `self.renderer = None`），既有呼叫端（傳齊三個參數）行為完全不變。③抽出共用 `_deliver(msg)` 方法（原本 `send()` 內的 SMTP 連線/重試邏輯，逐字搬移，未改動任何 retry/timeout/例外處理邏輯）。④新增 `send_html(subject, html_body)` — Phase 4 的純 SMTP 傳輸入口，只接受已經渲染好的 HTML，不知道任何 Priority/Event 概念。⑤舊版 `send()` 若在沒有 `renderer` 的情況下被呼叫，會拋出明確的 `EmailConfigError`（而不是 silently crash）。 | 讓 email_sender.py 收斂成「純 SMTP 傳輸」+ 保留舊版備援路徑，兩條路徑共用同一個 retry 邏輯，避免行為分岔。 |
| `maritime_news.py` | ①新增 Phase 4 imports（`BriefingSelector`/`build_daily_brief_view_model`/`ExecutiveEmailRenderer`）。②`__main__` 區塊：`sender.send(news_data, run_time)`（舊版）改為呼叫 `BriefingSelector().select(news_data['all_current_events'])` → `build_daily_brief_view_model()` → `ExecutiveEmailRenderer().render_daily_brief()` → `sender.send_html()`，並加入 `SEND_NO_RISK_BRIEF` 環境變數判斷（未設定時 fallback 讀 `email_rules.json` 的 `no_risk.send`）。③開頭 banner log 文字更新為 Phase 4 說明。 | 讓主程式的 production 進入點改用新版 Executive Email，同時保留 `NewsEmailSender.send()` 舊路徑程式碼不刪除，供未來需要時切回。 |
| `.gitignore` | 新增 `output/`（Phase 4 本機預覽輸出目錄，屬開發用產物，不應提交）。 | 避免預覽 HTML 污染 repo。 |

`models.py` / `risk_config.py` / `risk_rules.json` / `memory_pipeline.py` / `event_store.py` / `material_change_detector.py` / `event_lifecycle.py` / `notification_policy.py` **完全未修改**（Phase 3 Event Memory 核心維持原狀，符合本階段限制）。

## 3. Email Architecture

```
Persistent Event Memory（Phase 3，未改動）
        │  all_current_events（這次 run 比對/評分後的完整事件列表）
        ▼
BriefingSelector.select()
        │  → { immediate(P1), watch(P2), industry(P3), resolved, suppressed, overflow }
        ▼
ManagementSummaryBuilder
        │  → management_headline / management_summary / why_it_matters / what_changed
        │    （純 Rule-Based Template，無 LLM）
        ▼
build_daily_brief_view_model() / build_alert_view_model()   (email_view_model.py)
        │  → ExecutiveBriefViewModel（含 Overall Risk、Subject、Executive Summary）
        │    → 每個事件攤平成 EmailEventViewModel（None 欄位已省略、風險已分級）
        ▼
ExecutiveEmailRenderer.render_daily_brief() / render_alert()
        │  → 純 HTML 字串（table-based + inline CSS，已 escape/URL 過濾）
        ▼
NewsEmailSender.send_html(subject, html)   (email_sender.py，純 SMTP 傳輸)
        ▼
SMTP（含既有的 retry/timeout/exit(1) 邏輯，Phase 1 不變）
```

Renderer 全程不做任何風險判斷；View Model 全程不做任何 HTML 排版；Selector 全程不做任何評分——三層職責嚴格分離。

## 4. Alert vs Daily Brief 使用規則

- **Daily Brief**（`render_daily_brief`，`maritime_news.py` 目前的預設/唯一自動路徑）：顯示 Immediate(P1) + Management Watch(P2) + Industry Watch(P3) + Resolved 四個區塊，Subject 固定含 `P1:n P2:n`。本階段**沒有新增排程**，所以「一次 run 一封 Email」本身就已經是 consolidated（多個 P1 不會拆成多封）。
- **Alert**（`render_alert`，本階段只建立抽象，`maritime_news.py` 尚未自動呼叫，保留給未來排程使用）：只放 Immediate(P1)，不夾帶 P2/P3，避免把 1 個重大事件淹沒在一堆次要新聞裡。多個 P1 事件會合併成一封「N Immediate Attention Events」，不逐一寄送（`build_alert_subject()` 邏輯）。

## 5. Selection Logic

| notification_state × priority | 去向 |
|---|---|
| P1 + NEW / MATERIAL_UPDATE | `immediate`（上限 `email_rules.json.daily_brief.max_p1`，預設 5，溢出顯示 `+N additional`） |
| P2 + NEW / MATERIAL_UPDATE | `watch`（上限 max_p2=8） |
| P3 + NEW / MATERIAL_UPDATE 且具產業意義（fleet_relevance ≥ threshold 或 event_type ∈ {REGULATORY,OPERATIONS,SECURITY} 或命中 competitor 關鍵字） | `industry`（上限 max_p3=5） |
| P3 + NEW / MATERIAL_UPDATE 但不具產業意義 | `suppressed` |
| P4（任何狀態） | `suppressed`（`include_p4=false`，可設定開放） |
| 任何 Priority + RESOLVED_UPDATE | `resolved`（上限 max_resolved=5） |
| 任何 Priority + UNCHANGED / MINOR_UPDATE | `suppressed`（永不進 Email，對應 §二十一） |

排序：同一桶內先 Priority、再 Own Fleet（WAN_HAI 優先）、再 management_score 由高到低。

## 6. Management Summary Logic

`ManagementSummaryBuilder.management_summary()` 依 `incident_subtype`/`event_type` 選 template（FIRE / GROUNDING / VESSEL_ATTACK·HIJACKING·STOWAWAY_DRUGS / COLLISION·ALLISION / PORT_DISRUPTION / REGULATORY / POLLUTION / SINKING / LOSS_OF_PROPULSION / 通用 fallback），代入：地點中文名（`risk_rules.json` display 欄位取 "中文 / English" 的中文部分）、船名或「航商+船型」、由 `vessel_status`/`casualty_status`/`fire_status`/`port_status`/`navigation_status` 挑出的具體狀態描述。當 `information_status` 為 `EARLY_SIGNAL`/`UNCONFIRMED` 時，句首自動加上「據報，」（Facts ≠ Assessment，§二十二）。輸出上限 120 字元（`email_rules.json.management_summary.zh_max_chars`）。

## 7. Why It Matters Logic

依 `event_type` 對應固定中文說明（SAFETY／SECURITY／OPERATIONS／REGULATORY／CREW／ENVIRONMENT／MARKET／COMPETITOR／fallback），若 `event.carrier == "WAN_HAI"`（Own Fleet）一律覆蓋為最高優先級措辭：「涉及本公司船隊，建議列為最高優先資訊並確認船端／營運端狀況」。**不會**自行生成任何「公司已採取行動」的敘述（沒有 Master contacted / Vessel safe 之類的捏造內容）。

## 8. What Changed（Phase 3 change_reason → 管理措辭）

`translate_change_reason()` 用一組 regex → 繁中片語的對照表，逐條翻譯 `material_change_detector.py` 自己產生、格式受控的英文片語（例如 `Priority escalated P2 → P1` → 「風險優先級由 P2 升至 P1」；`Casualty status changed unknown → INJURED` → 「人員傷亡狀態更新：已有船員受傷」；`Resolution confirmed by source text` → 「事件已確認解除」）。無法辨識的片語不捏造翻譯，改為 `更新：{原文}` 保底顯示，絕不吞掉或竄改。只有 `notification_state` 為 `MATERIAL_UPDATE`/`RESOLVED_UPDATE` 的事件才會顯示此區塊。

## 9. Information Confidence 顯示

每張 Event Card 同時顯示 `confidence_level`（HIGH/MEDIUM/LOW）與 `information_status_label`（CONFIRMED/CORROBORATED/UNCONFIRMED/EARLY SIGNAL），兩者並列顯示、不合併成單一顏色。當 `information_status` 為 `UNCONFIRMED` 或 `EARLY_SIGNAL` 時，額外顯示一個醒目文字徽章：「⚠ {EARLY SIGNAL / UNCONFIRMED} — Awaiting independent confirmation」。Email 底部若含有任何未驗證事件，footer 會加註一行提醒不得當作已驗證事實。

## 10. Outlook / Gmail 相容性確認

`ExecutiveEmailRenderer` 全程只使用 `<table role="presentation">` + inline CSS（`style="..."`／`bgcolor`），未使用 CSS Grid、Flexbox、`<script>`、外部樣式表或外部圖片（Header 為純文字 + `bgcolor`）。自動化測試 `test_html_outlook_table_structure` 驗證：`<table>`/`</table>` 數量相等、不含 `display:flex`/`grid-template`/`<script`。版面寬度 780px、置中，`max-width` + `width:100%` 雙保險因應行動裝置窄螢幕。

## 11. 安全性確認

- **HTML Escape**：`ExecutiveEmailRenderer.esc()` 對 `&`/`<`/`>`/`"`/`'` 全部轉義，所有外部/未信任文字（來源標題、來源名稱、地點、船名等）進 HTML 前都經過此函式。測試 `test_html_escapes_untrusted_text` 驗證注入的 `<script>` 標籤被正確轉義成 `&lt;script&gt;`，且不會殘留可執行的 `<script>`。
- **開發過程中發現並修正一個真實的 double-escape bug**：Event Card footer 原本把已經 escape 過的 `conf_line`/`source_line` 又 escape 了一次（例如 `&` 變成 `&amp;amp;`），已修正為只在組裝來源片段時 escape 一次，避免顯示出亂碼式的雙重轉義文字。
- **URL Scheme Allow-list**：`safe_url()` 只接受 `http://`/`https://` 開頭的網址，`javascript:`/`data:`/`file:` 等 scheme 一律回傳 `None`（該連結整個不渲染）。測試 `test_html_rejects_unsafe_url_scheme` 涵蓋以上四種 scheme + `None` 輸入。
- 帳密／收件人沿用 Phase 1 的環境變數機制，本階段未新增任何 credential 相關程式碼。

## 12. Preview 檔案路徑

執行 `python preview_email.py` 後於專案 `output/` 目錄產生（不連線 SMTP、不寄送真實 Email）：

- `output/phase4_daily_brief_preview.html` — 綜合情境（P1 Red Sea Attack + P2 Singapore Terminal + P3 IMO Regulation + Resolved Wan Hai Grounding）
- `output/phase4_p1_alert_preview.html` — 單一 P1 Alert（Red Sea Attack，consolidated single-email）
- `output/phase4_no_risk_preview.html` — 無重大事件的 Daily Brief
- `output/preview_early_signal.html` — EARLY SIGNAL（單一 Reddit/Tier D 來源，未驗證火災報告）
- `output/preview_resolved.html` — 純 Resolved 情境（Wan Hai 船舶已重新浮起）

## 13. Test Results

| 項目 | 數量 |
|---|---|
| Previous（Phase 1/2/2.1/3） | 67 |
| Phase 4 新增 | 24 |
| **Total** | **91** |
| Passed | 91 |
| Failed | 0 |

24 項指定測試全部涵蓋：BriefingSelector（P1/P2/P4-suppressed/UNCHANGED-suppressed/MINOR_UPDATE-suppressed/Resolved/Own-Fleet 排序）、Overall Risk（HIGH/NORMAL）、Management Summary（Fire/Security/Port/Early-Signal 措辭）、What Changed（Material Update 翻譯/Resolved 措辭）、Subject Line（P1 Alert/Daily Brief/No Risk）、HTML Renderer（Outlook table 結構/None 值省略/來源連結/XSS escape/URL scheme 拒絕）、Preview 產生。

## 14. Regression 確認

Phase 1-3 原有的 **67 項測試逐一比對，程式碼零修改**（`git diff` 意義下 `tests/` 目錄中除新增的 `test_phase4_email.py` 外沒有任何檔案被觸碰）。全部 67 項在本次改動後**依然全數通過**，證明 Phase 4 的新增程式碼與既有 Crawler / Intelligence Core / Persistent Event Memory 完全隔離、無副作用。

## 15. Before / After

**Before（Phase 1-3）**：主管收到的 Email 主旨類似「Maritime News Alert (08/11 14:00) — 27 則」，內文是依分類（CAT1-6）排列的文章清單，每篇卡片顯示原始英文標題、300 字 RSS 摘要、關鍵字徽章——本質上是「新聞資料庫」，主管得自己判斷哪些重要。

**After（Phase 4）**：主管收到的 Email 主旨類似「[🔴 Maritime Alert] Daily Brief | 08/11 | P1:1 P2:1」，開頭 5 秒內看到 `TODAY'S RISK LEVEL: HIGH`；15 秒內看到 IMMEDIATE ATTENTION 區塊的 1-3 張 Event Card（例如「紅海商船遭攻擊事件」，繁中管理摘要 + WHY IT MATTERS + Confidence: HIGH · CORROBORATED + 3 個獨立來源 + Read Primary Source 連結）；30 秒內透過 Management Watch / Industry Watch / Resolved 區塊掌握全貌——本質上是「情報簡報」，Event-based、Priority 優先、風險與可信度分開標示、無重大事件時明確顯示 No Major Fleet Risk 而非硬湊內容。

---

## 待人工確認

以上為 Phase 4 完整交付內容。3 份（實際 5 份）HTML Preview 已產生於 `output/` 目錄，請開啟檢視版面。**在收到確認前，不會開始 Phase 5（Optional LLM Enhancement）**。
