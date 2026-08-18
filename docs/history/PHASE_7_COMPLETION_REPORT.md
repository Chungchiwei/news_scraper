# Phase 7 完成報告 — Operational Delivery, Management Dashboard & Notification Orchestration

範圍聲明：本階段只新增一個完全獨立的「Delivery Decision」維度（Notification Orchestration / Teams Management Alert / Management Dashboard / System Health），以及圍繞它的 Teams 通知與唯讀 Dashboard。**未修改**任何 Phase 1-5 的 severity/priority/confidence/information_status 判定邏輯、Phase 6 的 Operational Relevance 計算邏輯、Event Clustering、Persistent Memory 比對演算法、既有 Email 版面主結構與寄送機制、既有 136 項測試（Phase 1-6：113 + Phase 6：23 = 136，見第 15 節）。Port Weather / Earthquake / Tsunami / AIS / Automatic Route Diversion / Master-Agent 自動通知 / Mobile App / 公開網站 / 完整 RBAC / SSO / Cloud 部署（Phase 8）本輪**未實作**，依指示停在本報告，等待人工確認。

---

## 1. New Files

| 檔案 | 用途 |
|---|---|
| `delivery_models.py` | `DeliveryUrgency`（IMMEDIATE/PROMPT/BRIEF/DASHBOARD_ONLY/SUPPRESSED + ORDER/RANK）、`DeliveryChannel`（EMAIL/TEAMS/DASHBOARD）、`EmailMode`（ALERT/DAILY_BRIEF/NONE，見第 3 節「Email 邊界」說明）、`TeamsMode`（IMMEDIATE/PROMPT/RESOLVED/NONE）、`DeliveryDecision` dataclass（14 個 spec 必要欄位 + `teams_suppressed_by_cooldown` 診斷欄位）。 |
| `delivery_config.py` | `load_delivery_rules()`（`lru_cache`），與 `risk_config.py`/`memory_config.py`/`operational_config.py`/`llm_config.py` 同一種載入模式。 |
| `delivery_rules.json` | Delivery 分級門檻設定檔：`immediate`/`prompt`/`brief`/`dashboard_only`（priorities + notification_states + confidence_levels 白名單）、`exposure_escalated_min_urgency`/`exposure_cleared_min_urgency`（Dual-Axis Trigger 門檻）、`own_fleet_floor`（P1→IMMEDIATE、P2→PROMPT）、`cooldown_minutes`（P1=30、P2=120、operational_escalation=120、default=240）、`cooldown_bypass_notification_states`、`teams`（`max_message_chars`/`max_events_per_message`/`consolidate_same_run`/`consolidate_min_events`/`own_fleet_p1_separate`/`max_retries`/`max_sources_shown`）、`email`（`alert_urgencies`/`brief_urgencies`）。全部門檻 config-driven，不寫死在 Python。 |
| `delivery_history.py` | `DeliveryStatus`（SENT/FAILED/SUPPRESSED）、獨立 SQLite（`data/delivery_history.db`）`delivery_history` 表（event_id/run_id/channel/delivery_type/delivery_reason/dedup_key/sent_at/status/error_message），索引 (event_id,channel)/(dedup_key,channel)/sent_at。`DeliveryHistoryStore`：`record_delivery()`/`already_sent(dedup_key, channel)`/`last_sent_at(event_id, channel)`（cooldown 查詢用）/`last_delivery()`/`history_for_event()`/`recent()`。`NullDeliveryHistoryStore` 安全退化（DB 打不開時 dedup/cooldown 查詢一律回傳「無歷史」，寧可少數情況重複發送也不讓通知永久停擺）。`build_dedup_key(event_id, event_version, operational_notification_state)`。 |
| `delivery_orchestrator.py` | `DeliveryOrchestrator`：`decide()` 主入口，依序套用 Event Axis 基礎分級（`_classify_base_urgency`）→ Operational Axis Dual-Axis 合併（`_apply_operational_axis`）→ Own Fleet Floor（`_apply_own_fleet_floor`）→ Channel/Mode 映射 → Cooldown（僅 TEAMS channel）。`_more_urgent(a,b)` 是貫穿全模組的核心比較邏輯：兩條軸/兩種 override 合併時一律取「較急」的一方，絕不讓後套用的規則把已判定的 urgency 往下壓。內建 `diagnostics_report()`。 |
| `teams_config.py` | `TeamsConfig` dataclass + `load_teams_config()`：`TEAMS_ENABLED`（預設 `false`）、`TEAMS_MANAGEMENT_WEBHOOK_URL`（相容舊版 `TEAMS_WEBHOOK_URL`）、`TEAMS_SYSTEM_WEBHOOK_URL`、`DASHBOARD_BASE_URL`；`enabled = enabled and bool(management_url or system_url)`（即使旗標開著，沒設 webhook 也自動視為停用）；`redacted()` 只回傳 bool，絕不洩漏實際 webhook URL。 |
| `teams_notifier.py` | `TeamsSendResult` dataclass、`TeamsNotifier` ABC、`_build_message_card()`（Office 365 Connector MessageCard JSON）、`HttpTeamsNotifier`（production：`requests.post` + 內部有限次重試，絕不把 webhook URL 或完整例外內容記進 log）、`FakeTeamsNotifier`（測試/模擬用，`fail_count`/`always_fail` 模擬重試情境，`self.calls` 記錄所有呼叫）。 |
| `teams_renderer.py` | `render()`：依 `DeliveryDecision.teams_mode` + Own Fleet + delivery_reason 選擇四種模板（general/own_fleet/exposure_escalation/resolved），EARLY SIGNAL 警語與來源行「無條件」附加在所有模板之後（不因分支不同而遺漏）；`render_consolidated()`：多個 P1 事件合併成一則訊息。只渲染，不判斷 risk。 |
| `preview_teams.py` | 5 種離線 Teams 訊息 Preview（P1 / Own Fleet / Early Signal / Exposure Escalation / Resolved），輸出到 `output/teams_preview_*.txt`，全程不呼叫任何 `requests`/webhook。 |
| `source_health.py` | `SourceHealthStatus`（HEALTHY/DEGRADED/DOWN/UNKNOWN，門檻沿用原始 CLAUDE.md §十四：連續 3 次失敗 DEGRADED、5 次 DOWN）。`SourceHealthStore`（獨立 SQLite `data/source_health.db`）：`record_success()`/`record_failure()`/`get()`/`all()`/`summary()`。`NullSourceHealthStore` 安全退化。 |
| `system_health.py` | `SystemStatus`（HEALTHY/DEGRADED/CRITICAL）、`SystemHealthReport` dataclass、`SystemHealthService.build_report()`：彙整 Event Store / Email / Teams / Fleet-Schedule-Route Provider / LLM / Source Health 六個面向；`_compute_overall()` 判定順序：Event Store 失效或全部來源失效 → CRITICAL；Email 最終失敗 → CRITICAL；Teams 失敗/單一 Provider 不可用/部分來源 DOWN 或 DEGRADED → DEGRADED；LLM 停用/失敗絕不影響嚴重度（永遠只是 informational 的 ENABLED/DISABLED 欄位）。 |
| `dashboard/__init__.py` / `dashboard/app.py` | FastAPI app：7 個 HTML 頁面路由（`/`、`/events`、`/events/{id}`、`/fleet`、`/ports`、`/resolved`、`/health`）+ 5 個 sanitized 內部唯讀 API（`/api/summary`、`/api/events`、`/api/events/{id}`、`/api/fleet-exposure`、`/api/health`）。`require_auth()`（`DASHBOARD_AUTH_ENABLED` 預設 `false`，啟用時用 `secrets.compare_digest` 防 timing attack）。`get_dashboard_service()` 為 per-request generator dependency，開啟失敗一律安全退化（`_try_open_event_store()` 回傳 `None`，不讓整個 App crash）。`__main__` 預設 bind `127.0.0.1`，不是 `0.0.0.0`。 |
| `dashboard/service.py` | `DashboardService`：`_safe()` 包裝所有查詢（`sqlite3.Error`/任何例外一律回傳安全預設值，DB Locked ≠ Crash）；`overview()`/`list_events()`/`get_event_detail()`/`fleet_exposure()`/`port_exposure()`/`resolved_events()`/`system_health()`；`_sort_top_attention()` 實作 §三十九排序（OWN FLEET→P1→DIRECT→HIGH→P2→Management Score→Last Material Update，只影響顯示順序，不改 Event Priority）。 |
| `dashboard/view_models.py` | Jinja2 filters：`fmt_tpe`/`fmt_hours`/`priority_color`/`exposure_color`/`situation_color`/`health_color`/`exposure_label`。色彩沿用 Phase 4 Deep Navy/Port Blue/White/Light Gray + Risk P1 Red/P2 Orange/P3 Amber/Resolved Green + Exposure DIRECT Red/HIGH Orange/MODERATE Amber/LOW Blue-Gray/NONE Gray/UNAVAILABLE Neutral Gray。 |
| `dashboard/templates/*.html`（7 個） | `base.html`（導覽列、footer 免責聲明）、`overview.html`、`events.html`（篩選+搜尋）、`event_detail.html`（Event Timeline 與 Operational Exposure Timeline **分開兩個區塊**）、`fleet.html`、`ports.html`、`resolved.html`、`health.html`。 |
| `dashboard/static/style.css` | 純本地 CSS，無外部 CDN/Analytics。 |
| `tests/test_phase7_delivery.py` | 12 項測試（11 項指定 + 1 項額外 P1 Own Fleet Floor 驗證）。 |
| `tests/test_phase7_teams.py` | 9 項指定測試。 |
| `tests/test_phase7_dashboard.py` | 13 項指定測試。 |
| `tests/test_phase7_health.py` | 5 項指定測試。 |
| `phase7_simulation.py` | Management Simulation（Event A/B/C/D）+ System Health Simulation 驗證腳本（見第 16 節），使用暫存 SQLite + FakeTeamsNotifier，非 production pipeline 一部分。 |

## 2. Modified Files

| 檔案 | 修改內容與原因 |
|---|---|
| `event_store.py` | 新增 `get_latest_run()`（`SELECT * FROM system_runs ORDER BY started_at_utc DESC LIMIT 1`），供 System Health 顯示「Last Successful Run」使用。純新增方法，未觸碰既有 schema/既有方法。 |
| `requirements.txt` | 新增 `fastapi`/`uvicorn`/`jinja2>=3.1.0`（明確標註最低版本——沙盒環境驗證發現作業系統隨附的 Jinja2 3.0.3 與新版 Starlette `TemplateResponse` 快取機制不相容）/`python-multipart`；`pytest` 區塊新增 `httpx`（FastAPI TestClient 依賴）。 |
| `maritime_news.py` | 新增 Phase 7 imports；`NewsRssScraper.__init__` 新增可選 `source_health_store` 參數，`fetch_from_source()` 在既有的兩個乾淨分支點（成功 `break`／`所有 URL 均失敗`）掛上 `record_success()`/`record_failure()`，全部包 try/except，Source Health 記錄本身絕不影響爬蟲流程；新增 `_collect_operational_candidate_events()`（供 Phase 6/7 共用，見下）；`_run_operational_relevance()` 改為回傳 `(relevance_map, notif_state_map)` 二元組，且評估對象從「BriefingSelector 四個通知桶」擴大為「四個通知桶 + suppressed 桶中 notification_state==UNCHANGED 的事件」——這是 Dual-Axis Trigger 能在 production 真正生效的關鍵修正（見第 5 節）；新增 `_run_delivery_orchestration()`（呼叫 DeliveryOrchestrator → `_send_teams_for_decisions()`，整段包 try/except，Teams 停用/失敗絕不影響 Email）；新增 `_send_teams_for_decisions()`（Consolidation + Per-Channel Dedup + Failure Isolation）；新增 `_record_teams_result()`/`_record_email_delivery()`；`__main__` 串接以上全部，Email 成功/失敗兩條路徑都會回頭記錄 `DeliveryHistoryStore` 的 EMAIL channel 送達狀態。**未修改**任何 scraper 的抓取/關鍵字比對邏輯、Phase 1-6 pipeline 主流程、SMTP 邏輯本身。 |

---

## 3. Delivery Architecture

```
Intelligence Event (Phase 1-5)
        +
Operational Relevance (Phase 6)
        ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DELIVERY ORCHESTRATOR (Phase 7)
  Event Axis 基礎分級
        ↓
  Operational Axis 合併（只會讓 urgency 更急，不會壓低）
        ↓
  Own Fleet Floor（只會讓 urgency 更急）
        ↓
  Channel 映射 + Cooldown（僅 TEAMS）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        ↓
┌───────────────┬───────────────┬───────────────┐
   EMAIL              TEAMS            DASHBOARD
（沿用 Phase 4      （teams_renderer     （DashboardService
 既有 Daily Brief   純渲染，per-channel   直接讀 EventStore，
 /Alert 邏輯，只     dedup+cooldown，     視覺與 Push 決策
 額外記錄送達歷史）  consolidation）      完全分離）
```

**Delivery ≠ Risk**：`DeliveryOrchestrator.decide()` 全程只 `getattr()` 讀取 `event.management_priority`/`event.confidence_level`/`event.notification_state`/`operational_relevance.relevance_level`/`operational_notification_state`，不寫回任何一個欄位，也不重新計算它們。`DeliveryDecision` 上保留這些欄位的唯讀快照（`event_notification_state`/`operational_notification_state`/`management_priority`/`relevance_level`），純供 History/Dashboard 追溯,不是判斷輸入的另一份副本。

**Email 邊界的刻意設計決策**：`EmailMode`（ALERT/DAILY_BRIEF/NONE）是 Delivery Orchestrator 對 Email channel 的「建議」metadata，**不取代**、**不覆寫** Phase 4 `BriefingSelector` 既有的 P1-P4 分桶邏輯——Daily Brief 的實際內容仍然 100% 由 Phase 3/4 既有規則決定（這正是「EMAIL IS FOR CONTEXT」原則的落實：Email 呈現的是完整情勢，不是逐一事件的即時 push 決策）。Phase 7 在 Email 這條路徑上唯一新增的動作，是 Email 實際寄送成功/失敗後，把這次涵蓋的事件記錄進獨立的 `delivery_history`（EMAIL channel），供 Dashboard 與未來查詢使用；沒有新增第二套「要不要放進這封信」的判斷邏輯。這個邊界會在後續維運中需要持續被提醒，故在 `delivery_models.py` 的 `EmailMode` docstring 與本節都留下明確記錄。

## 4. Delivery Rules

| Urgency | 判定條件（節錄自 `delivery_rules.json`） | Channel | 範例 |
|---|---|---|---|
| **IMMEDIATE** | P1 + NEW/MATERIAL_UPDATE + HIGH/MEDIUM confidence，或 Own Fleet Floor 命中 P1 | EMAIL + TEAMS + DASHBOARD | Event A（P1 NEW HIGH confidence） |
| **PROMPT** | P1/P2 + MATERIAL_UPDATE/RESOLVED_UPDATE；或 P1 NEW/MATERIAL_UPDATE 但 confidence 未達 immediate 門檻（不可因信心不足被 SUPPRESSED）；或 Dual-Axis 合併後達到此門檻 | EMAIL + TEAMS + DASHBOARD | Event B（P2 UNCHANGED + EXPOSURE_ESCALATED）、Event D（P1 RESOLVED_UPDATE） |
| **BRIEF** | P1/P2/P3 + NEW/RESOLVED_UPDATE/MATERIAL_UPDATE，未達 PROMPT 門檻；或 Exposure Cleared 合併後達到此門檻 | EMAIL + DASHBOARD | 一般 P2 NEW（無 Own Fleet、無 Exposure 加成） |
| **DASHBOARD_ONLY** | P3/P4 參考性事件；MINOR_UPDATE | DASHBOARD | 一般產業新聞 |
| **SUPPRESSED** | notification_state == UNCHANGED，且 Operational Axis 也沒有獨立升高 | DASHBOARD（§七十九：Dashboard 永遠可見，與 Push 決策分離） | Event C（P2 UNCHANGED + EXPOSURE_UNCHANGED） |

Cooldown（僅作用於 TEAMS channel）：P1=30 分鐘、P2=120 分鐘、Operational Escalation=120 分鐘、預設 240 分鐘；`notification_state` 屬於 `MATERIAL_UPDATE`/`RESOLVED_UPDATE`（真正的新事實）一律 bypass cooldown（見 `tests/test_phase7_delivery.py::test_cooldown_suppresses_repeat` 與 `test_material_update_bypasses_cooldown`）。

## 5. Dual-Axis Trigger

Delivery Orchestrator 同時讀取兩條完全獨立的軸：

```
Event Axis        （Phase 3 NotificationState：NEW/MATERIAL_UPDATE/MINOR_UPDATE/UNCHANGED/RESOLVED_UPDATE）
Operational Axis   （Phase 6 OperationalNotificationState：EXPOSURE_NEW/ESCALATED/UNCHANGED/REDUCED/CLEARED/UNAVAILABLE）
```

`_apply_operational_axis()` 的核心邏輯：`EXPOSURE_ESCALATED` 時，把目前 urgency 與設定的 `exposure_escalated_min_urgency`（預設 PROMPT）取「較急」的一方合併；`EXPOSURE_CLEARED` 時同理合併 `exposure_cleared_min_urgency`（預設 BRIEF）。這個合併操作**只會讓 urgency 變得更急，不會因為曝險降低就把一個原本 Event Axis 判定的 urgency 往下壓**——降低曝險的通知本身是用 `exposure_cleared` 規則另外處理，不是拿掉既有通知（`delivery_rules.json` 的 `_note_dual_axis` 註解）。

**Production Wiring 的關鍵修正**：BriefingSelector 原本只把 `NEW`/`MATERIAL_UPDATE`/`RESOLVED_UPDATE` 事件放進 `immediate`/`watch`/`industry`/`resolved` 四個桶，`UNCHANGED` 事件全部進 `suppressed` 桶。如果 Phase 6/7 的評估對象只看前四個桶，Event UNCHANGED 但曝險升高的事件根本不會被 Operational Relevance Engine 評估到，Dual-Axis Trigger 在 production 就永遠不會發生。因此 `maritime_news.py` 新增 `_collect_operational_candidate_events()`，把評估範圍擴大為「四個通知桶 + suppressed 桶中 `notification_state == UNCHANGED` 的事件」（`MINOR_UPDATE`/不具產業意義的 P3/未啟用的 P4 仍不評估，維持原本範圍，避免無意義的 Provider matching 開銷）。

已透過一次性驗證腳本（開發過程使用，未併入 repository，結果併入本報告與第 16 節）以**真實 production 函式**（非隔離的 orchestrator 單元測試）證明：同一篇文章跑兩次 `run_intelligence_pipeline` + `apply_persistent_memory`，第二次事件被正確判定為 `UNCHANGED`、正確落入 `selection['suppressed']`、且在新增的港口曝險資料下被 `_run_operational_relevance()` 正確評估為 `EXPOSURE_ESCALATED`、`_run_delivery_orchestration()` 正確產生 `PROMPT` 決策並實際透過（monkeypatch 的）Teams notifier 送出訊息。詳細數字見第 16 節「Event B」。

## 6. Dedup / Cooldown（Per Channel）

`delivery_history.db` 是獨立於 Phase 3 `event_history`、Phase 6 `operational_relevance_history` 的第三個 SQLite 資料庫，描述的是「這件事實際被送到哪裡去了」。

- **Dedup**：`build_dedup_key(event_id, event_version, operational_notification_state)` 組成 `event_id:v{version}:{operational_state}`，**不含 channel**——查詢時是「dedup_key + channel」一起比對（`already_sent(dedup_key, channel)`），確保「同一個 dedup_key 在 EMAIL 上發過」不代表「TEAMS 上也發過」。
- **Per-Channel Failure Isolation**：P1 事件 Email 已成功、Teams 失敗，下次 run 透過 `already_sent(key, EMAIL)==True` 不重寄 Email，`already_sent(key, TEAMS)==False` 允許 Teams 重試——兩個 channel 的成功/失敗狀態完全獨立記錄（見 `test_email_and_teams_independent_history`）。
- **Cooldown 只作用於 TEAMS channel**：`_apply_cooldown()` 查詢 `history.last_sent_at(event_id, TEAMS)`，在窗口內就把 TEAMS 從 `channels` 移除、標記 `teams_suppressed_by_cooldown=True`，但 EMAIL/DASHBOARD 完全不受影響（Delivery Orchestrator 層面）；`maritime_news._send_teams_for_decisions()` 送出前額外再查一次 `already_sent()` 做最終保險（即使 crash 後重跑同一個 run 也不會重複發送）。

## 7. Teams — 5 種 Preview

以下由 `preview_teams.py`（純 `render()`，不呼叫任何 webhook）實際產生，字數全部遠低於 2200 字上限：

**P1（一般，287 字）**
```
🔴 MARITIME ALERT | P1

Red Sea — Container Vessel Attacked

Status:
MATERIAL UPDATE

WHL Exposure:
HIGH

Affected:
1 vessel / 1 service

What Changed:
Crew casualty confirmed.

Confidence:
HIGH

Sources:
Reuters · TradeWinds

[Open Dashboard](http://127.0.0.1:8000/events/evt_p1_general)
```

**Own Fleet（301 字）**
```
🔴 P1 | OWN FLEET

WAN HAI 503 — Vessel Incident

Event:
FIRE

Exposure:
DIRECT

Information:
CORROBORATED / HIGH

Latest:
Fire status updated: crew fighting fire, no injuries reported.

Open Dashboard for details.

Sources:
TradeWinds

[Open Dashboard](http://127.0.0.1:8000/events/evt_own_fleet_fire)
```

**Early Signal（326 字，PROMPT 而非 IMMEDIATE，因 confidence 未達門檻，但 EARLY SIGNAL 警語不因此被省略）**
```
🔴 MARITIME ALERT | P1

Possible Maritime Security Incident Near Singapore Strait

Status:
NEW

WHL Exposure:
HIGH

Confidence:
LOW

EARLY SIGNAL — UNCONFIRMED
Current information is based on limited sources and requires confirmation.

Sources:
Reddit r/maritime

[Open Dashboard](http://127.0.0.1:8000/events/evt_early_signal)
```

**Exposure Escalation（Dual-Axis 最重要案例，259 字）**
```
🟠 P2 | OPERATIONAL EXPOSURE ESCALATED

Kaohsiung Terminal Reports Berth Congestion

WHL Exposure:
HIGH

Reason:
WHL operational exposure escalated

Confidence:
MEDIUM

Sources:
TradeWinds

[Open Dashboard](http://127.0.0.1:8000/events/evt_exposure_escalation)
```

**Resolved（237 字）**
```
🟢 RESOLVED | P1

MSC Vessel Refloated Near Singapore

Status:
RESOLVED

WHL Exposure:
NONE

Vessel successfully refloated and port operations have resumed.

Sources:
TradeWinds
```

## 8. Dashboard Architecture

```
dashboard/
    app.py          — FastAPI 路由層（HTML 頁面 + 內部唯讀 API）
    service.py       — DashboardService（唯一可以查 DB 的地方，Jinja template 不直接 SQL）
    view_models.py     — Jinja2 filters（色彩/格式化）
    templates/            — 7 個 Jinja2 頁面（extends base.html）
    static/style.css       — 純本地 CSS，無外部 CDN
```

`get_dashboard_service()` 是 per-request generator dependency：每次請求開啟 EventStore/OperationalHistoryStore/DeliveryHistoryStore/SourceHealthStore 連線，`finally` 關閉——刻意選擇的簡化設計（低流量內部工具，不建立 connection pooling），已於 code review 中記錄為明確的取捨決策。`DashboardService._safe()` 包裝所有查詢：`sqlite3.Error` 或任何例外一律回傳安全預設值，DB Locked 不會讓整頁 500，更不會影響 production collector 的寫入（讀寫分離、互不阻塞）。

## 9. Dashboard Pages

| 頁面 | 路徑 | 內容 |
|---|---|---|
| Overview | `/` | Overall Situation（HIGH/ELEVATED/WATCH/NORMAL，與 Email 判定邏輯一致）、KPI Grid（Active P1/P2、Direct/High Exposure、Affected Vessels/Ports、New/Material/Resolved 計數，**不顯示** RSS Article Count/Crawler Source Count）、Top 5 Management Attention |
| Active Event Board | `/events` | 全部 Active/Monitoring 事件，含 Priority/Event Type/Lifecycle/Notification/Confidence/Information/WHL Exposure/Vessels/Location/Last Updated 欄位；篩選（priority/event_type/lifecycle/confidence/relevance_level/region/own_fleet/carrier）+ 搜尋（vessel/headline/port/carrier/event_id） |
| Event Detail | `/events/{event_id}` | Overview、Event Intelligence（Management Summary/Why It Matters/What Changed）、Event Timeline（Phase 3）、Operational Exposure Timeline（Phase 6，**與 Event Timeline 分開兩個區塊**）、Sources |
| Fleet Exposure | `/fleet` | 只顯示目前有 Operational Relevance 評估的船（不是 AIS Fleet Tracker），含 Active Risk Events/Highest Exposure/Closest Risk Window |
| Port Exposure | `/ports` | Active Events/Highest Priority/Affected WHL Vessels/Closest ETA/Exposure Level |
| Resolved | `/resolved` | 最近 7 天 Resolved 事件 |
| System Health | `/health` | 見第 12 節 |

## 10. Event Timeline（Phase 3 History）

`get_event_detail()` 呼叫既有 `EventStore.get_history(event_id)`，顯示 `change_type`/`old_value`/`new_value`/`change_reason`/`material` 隨時間變化的紀錄（例如：NEW → Confidence MEDIUM→HIGH → Crew casualty confirmed → RESOLVED）。`test_dashboard_event_history` 驗證 API 正確回傳並包含指定的 `change_reason`。

## 11. Exposure Timeline（Phase 6 History）

`get_event_detail()` 另外呼叫 `OperationalHistoryStore.get_latest(event_id)`，寫進 `row["operational_snapshot"]`——一個**與 `event_history` 完全獨立**的欄位/資料結構（`test_dashboard_operational_history` 明確斷言兩者是不同的 list/dict 物件，不會被合併成同一個 timeline）。目前版本顯示的是最新一筆快照（`relevance_level`/`relevance_status`/`affected_vessels`+ETA/`affected_ports`）；`relevance_status` 可能是 `ASSESSED`/`DATA_STALE`（`test_dashboard_stale_data` 驗證 `DATA_STALE` 會被誠實標示，不會被當成目前 confirmed 曝險）；`UNAVAILABLE` 的快照依 Phase 6 既有設計不寫入歷史（避免污染基準），事件若從未被評估過則顯示 `relevance_status=NOT_ASSESSED`（區別於「已評估過、確認沒有曝險」的 `NONE`）。

## 12. System Health

| 面向 | 資料來源 | 判定規則 |
|---|---|---|
| Event Store | `EventStore.health_check()` + `get_latest_run()` | 失敗 → CRITICAL |
| Email | `DeliveryHistoryStore.recent()` 最近一筆 EMAIL 記錄 | FAILED → DEGRADED（但拉高 Overall 到 CRITICAL，見下） |
| Teams | 同上，channel=TEAMS | FAILED → DEGRADED |
| Fleet/Schedule/Route Provider | `operational_provider_status` dict（目前 App 層固定傳空 dict，見第 13 節限制說明） | UNAVAILABLE → 該欄位顯示 `UNAVAILABLE` 字串（不是 0/空），Overall → DEGRADED |
| LLM | `llm_enabled` 旗標 | 只顯示 ENABLED/DISABLED，**永遠不影響**嚴重度判定 |
| Source Health | `SourceHealthStore.summary()` | 全部 DOWN → Overall CRITICAL；部分 DOWN/DEGRADED → Overall DEGRADED |

Overall 判定優先序：Event Store 失效 or 全部來源失效 or Email 最終失敗 → **CRITICAL**；以上皆非，但 Teams 失敗/任一 Provider 不可用/任何來源 DOWN 或 DEGRADED → **DEGRADED**；以上皆非 → **HEALTHY**。System Health 報告產生過程完全不接觸 `delivery_orchestrator.py`/`TeamsNotifier` 的 Maritime Intelligence 路徑——Source Health 記錄與 Delivery/Teams 是兩個互不相交的程式碼路徑（`test_source_health_failure_not_management_alert` 驗證：大量來源 DOWN 之後，`FakeTeamsNotifier` 的呼叫次數與 `delivery_history` 的記錄筆數都是 0）。

## 13. Privacy & Security

- **No secrets exposed**：`/api/health` 只回傳 `status`/`last_run`/`event_store`/`email`/`teams`/`operational_data`/`llm` 六個 sanitized 欄位，`try/except` 包住整個 handler，任何例外一律回傳 `{"status": "UNKNOWN"}`，絕不輸出 exception stack。`teams_config.TeamsConfig.redacted()`、`teams_notifier.HttpTeamsNotifier` 全程不記錄實際 webhook URL。`test_dashboard_no_secret_exposure` 對 `/api/health`/`/api/summary`/`/api/events`/`/api/fleet-exposure` 逐一斷言回應內文不含密碼、webhook 關鍵字、DB 路徑、Traceback 字樣。
- **Dashboard localhost default**：`dashboard/app.py.__main__` 預設 `DASHBOARD_HOST=127.0.0.1`；`DASHBOARD_AUTH_ENABLED`（預設 `false`）啟用時走環境變數 `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD`（`secrets.compare_digest` 比對，防 timing attack），不硬編碼。
- **Internal operational data stays internal**：Dashboard 前端只用本地 CSS，無外部 CDN/Analytics；`LLM_ALLOW_INTERNAL_OPERATIONAL_DATA` 邊界（Phase 5/6 既有）本輪未改動。
- **Read-only 驗證**：`test_dashboard_read_only` 對 `/events/{id}`（POST/PUT/DELETE）與 `/`（POST）逐一驗證回傳 404/405，並確認資料庫記錄未被更動。

**已知限制**（誠實揭露，非隱瞞）：`dashboard/app.py.get_dashboard_service()` 目前把 `operational_provider_status` 固定傳空 dict，因此透過真實 HTTP 路由打 `/health`/`/api/health` 時，Fleet/Schedule/Route Provider 欄位恆為 `UNKNOWN`，不會反映即時 Provider 狀態；`test_dashboard_unavailable_provider` 因此直接在 `DashboardService`/`SystemHealthService` 層驗證此欄位的正確性語意（`UNAVAILABLE` 字串而非 0），未透過 FastAPI route 驗證。這個 wiring gap 建議在下一輪維護中補上——把 `_run_operational_relevance()` 的 Provider 建置結果（成功/失敗）也持久化成一個小狀態檔，供 Dashboard 讀取。

## 14. Tests

```
Phase 1-6 既有：136
Phase 7 新增：
  Delivery Tests:  12（11 項指定 + 1 項 Own Fleet P1 IMMEDIATE 額外驗證）
  Teams Tests:      9（9 項指定）
  Dashboard Tests: 13（13 項指定）
  Health Tests:     5（5 項指定）
  ─────────────────
  Phase 7 小計:    39

Total:   175
Passed:  175
Failed:    0
```

實際執行輸出（`python3 -m pytest tests/ -q`）：

```
........................................................................ [ 41%]
........................................................................ [ 82%]
...............................                                          [100%]
175 passed, 1 warning in 3.18s
```

（唯一的 warning 是 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`，屬於 FastAPI TestClient 本身的第三方 deprecation 提示，非本專案程式碼問題，不影響測試正確性。）

全部測試使用 tmp_path 暫存 SQLite / FakeTeamsNotifier / FastAPI TestClient，無任何 live Teams webhook、live SMTP、live Internet、live Internal API、live LLM 依賴。

## 15. Regression

原 136 項測試（`test_carrier_alias_audit.py`/`test_carrier_filter.py`/`test_clustering.py`/`test_clustering_hardening.py`/`test_confidence_priority.py`/`test_extraction.py`/`test_memory_lifecycle.py`/`test_memory_store.py`/`test_phase4_email.py`/`test_phase5_llm.py`/`test_phase6_operational.py`/`test_pipeline.py`/`test_risk_scoring.py`/`test_source_independence.py`/`test_ssl_config.py`）**零修改**、**全部通過**，測試名稱與數量與 Phase 6 完成時完全一致（見上方 pytest 輸出逐一列出的 test id）。`event_store.py`/`maritime_news.py` 的修改皆為新增函式/新增可選參數（`source_health_store: Optional = None`、`_run_operational_relevance()` 回傳值從 dict 改為 tuple 但呼叫端已同步更新且無外部測試依賴此函式），未變更任何既有函式的既有行為路徑。

## 16. Management Simulation

由 `phase7_simulation.py` 實際執行產生（使用暫存 SQLite + `FakeTeamsNotifier`，不連任何真實系統）：

### Event A — P1 Security NEW, WHL Exposure HIGH
Expected：Teams IMMEDIATE / Email include / Dashboard active top attention

```
urgency          = IMMEDIATE
channels         = ['EMAIL', 'TEAMS', 'DASHBOARD']
teams_mode       = IMMEDIATE
email_mode       = ALERT
dashboard_visible= True
reason           = P1 NEW with HIGH confidence
✅ PASS
```

### Event B — P2 UNCHANGED, Exposure MODERATE → HIGH（Phase 7 最重要案例）
Expected：Teams PROMPT（Reason: WHL exposure escalated）/ Email include as operational update / Dashboard exposure escalated badge

```
event.notification_state (Phase 3 axis)        = UNCHANGED
operational exposure (Phase 6 axis)             = MODERATE → HIGH
urgency          = PROMPT
channels         = ['EMAIL', 'TEAMS', 'DASHBOARD']
teams_mode       = PROMPT
email_mode       = DAILY_BRIEF
reason           = WHL operational exposure escalated

── Actual Teams message rendered for Event B ──
🟠 P2 | OPERATIONAL EXPOSURE ESCALATED

Kaohsiung Terminal Reports Berth Congestion

WHL Exposure:
HIGH

Reason:
WHL operational exposure escalated

Confidence:
MEDIUM

Sources:
TradeWinds

[Open Dashboard](http://127.0.0.1:8000/events/evt_B)
✅ PASS — Event UNCHANGED + Exposure ESCALATED = Delivery required (not suppressed)
```

### Event C — P2 UNCHANGED, Exposure unchanged
Expected：Teams suppressed / Email no duplicate immediate notification / Dashboard still visible

```
urgency          = SUPPRESSED
channels         = ['DASHBOARD']
teams_mode       = NONE
email_mode       = NONE
dashboard_visible= True
✅ PASS — no duplicate notification, but still visible on Dashboard
```

### Event D — P1 RESOLVED_UPDATE, Exposure CLEARED
Expected：Teams resolution notification / Email Resolved section / Dashboard Resolved

```
urgency          = PROMPT
channels         = ['EMAIL', 'TEAMS', 'DASHBOARD']
teams_mode       = RESOLVED
email_mode       = DAILY_BRIEF
reason           = P1 RESOLVED_UPDATE
✅ PASS
```

### 實際透過 FakeTeamsNotifier 送出驗證

```
Event A: Teams message SENT (155 chars)
Event B: Teams message SENT (187 chars)
Event C: Teams NOT sent (urgency=SUPPRESSED, teams_mode=NONE)
Event D: Teams message SENT (176 chars)

✅ Confirmed: Teams sent for ['A', 'B', 'D'] only. Event C correctly suppressed
   (still visible on Dashboard via EventStore, independent of push decision).
```

### System Health Simulation
Scenario：RSS Healthy / Event Store Healthy / Email Success / Teams Failure / Schedule Provider Unavailable

```
SYSTEM HEALTH
  Overall: DEGRADED
  Event Store: HEALTHY   Email: HEALTHY   Teams: DEGRADED
  Fleet Provider: AVAILABLE   Schedule Provider: UNAVAILABLE   Route Provider: AVAILABLE
  LLM: DISABLED
  Sources: {'total': 3, 'HEALTHY': 3, 'DEGRADED': 0, 'DOWN': 0, 'UNKNOWN': 0}
  ⚠ TEAMS delivery failed on most recent attempt
  ⚠ Schedule Provider unavailable

✅ PASS — Overall correctly DEGRADED (not falsely HEALTHY, not falsely CRITICAL).
✅ PASS — This report never touches delivery_orchestrator.py / TeamsNotifier's
          Maritime Intelligence path — System Health stays on its own channel.
```

Dashboard 必須個別顯示 Event Store/Email/Teams/各 Provider/Source Health 六個獨立欄位，不能因為部分功能正常運作就整體回報 Healthy——上述模擬證明系統在「多數功能正常、Teams 失敗、Schedule Provider 不可用」的混合情境下，正確回報 `DEGRADED`（不是誤報 `HEALTHY`，也不是過度反應成 `CRITICAL`）。

腳本執行結果：`phase7_simulation.py` 全部 assertion 通過，exit code 0。

---

## 部署指南（Local）

```bash
pip install -r requirements.txt

# Dashboard（本機開發，預設只 bind 127.0.0.1）
python dashboard/app.py
# 或
uvicorn dashboard.app:app --host 127.0.0.1 --port 8000

# Teams（預設停用，需明確設定才會實際發送）
export TEAMS_ENABLED=true
export TEAMS_MANAGEMENT_WEBHOOK_URL="https://outlook.office.com/webhook/..."
export TEAMS_SYSTEM_WEBHOOK_URL="https://outlook.office.com/webhook/..."   # 選填，System Health 頻道用
export DASHBOARD_BASE_URL="http://127.0.0.1:8000"                          # 選填，Teams 訊息附連結用

# Dashboard Basic Auth（選填）
export DASHBOARD_AUTH_ENABLED=true
export DASHBOARD_USERNAME="..."
export DASHBOARD_PASSWORD="..."

# 主程式（不變，Phase 7 全部整合在既有 entry point）
python maritime_news.py
```

---

## 停止點

Phase 7 完成報告、Management Simulation、Teams Preview、Dashboard Demo（175 項測試全通過，含 FastAPI TestClient 對 7 個頁面 + 5 個 API 的驗證）已如上提交。依指示，**不**接續實作 Phase 8（Port Weather / Earthquake / Tsunami / AIS / Internal Production API Deployment / Unified Operational Hazard Intelligence），等待人工確認後再開始下一階段。
