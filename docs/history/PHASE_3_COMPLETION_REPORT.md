# Phase 3 完成報告 — Persistent Event Memory & Event Lifecycle Management

範圍聲明：本階段只實作 Persistent Event Store / Stable Event Identity / Cross-run Matching / Event Lifecycle / NEW-MATERIAL-MINOR-UNCHANGED-RESOLVED_UPDATE / Material Change Detection / Resolution-Expiry / Notification Policy / Event History / Storage Integrity。未動 Executive Email 排版、LLM、Dashboard、Web UI、外部資料庫服務、排程迴圈；Email 仍沿用 Phase 1/2 相容格式，只是現在只收到 `should_notify=True` 的事件。

---

## 1. New Files

| 檔案 | 用途 |
|---|---|
| `memory_rules.json` | Phase 3 設定檔：matching_windows、expiry_days、material_change threshold、resolution/reopen 關鍵字、notification 規則、baseline_mode、status_keywords（operational status 抽取字典）、status_escalation_order、matching_weights、database 設定。全部外部化，不寫死在程式碼。 |
| `memory_config.py` | 載入 `memory_rules.json`（`lru_cache`），與 `risk_config.py` 分開，避免循環 import，也維持「risk_rules 回答嚴重度、memory_rules 回答要不要再通知」的職責分離。 |
| `event_identity.py` | `EventIdentityBuilder`：canonical key 正規化（vessel name / location / date bucket）、Level 1-4 身份訊號、`generate_event_id()`（SHA256 前 16 碼）。 |
| `event_store.py` | `EventStore`：SQLite 四張表（events / event_articles / event_history / system_runs）+ schema_meta，WAL mode（best-effort）、URL 正規化去重、`persist_event_update()` 單一 transaction 合併寫入、`health_check()`、`EventStoreError`（DB 失敗 fatal）。 |
| `persistent_matcher.py` | `PersistentEventMatcher`：canonical_key fast path + 跨 run fuzzy 比對（positive/negative signal，沿用 Phase 2.1 hard reject 哲學），依 event_type 決定搜尋窗口。 |
| `status_extractor.py` | `StatusExtractor`：rule-based（keyword/regex，無 LLM）抽取 vessel_status / casualty_status / fire_status / port_status / navigation_status / pollution_status / operational_status / crew 傷亡人數，缺證據一律 `None`。 |
| `material_change_detector.py` | `MaterialChangeDetector`：只比較 Structured Fact Snapshot（§二十五 A〜I 規則），完全不看 article_count / 原始標題摘要文字，架構上排除「多一篇轉載」「用詞不同」被誤判成 Material 的可能性。 |
| `event_lifecycle.py` | `EventLifecycleManager`：ACTIVE/MONITORING/RESOLVED/EXPIRED 轉換、resolve/reopen 關鍵字判斷、`sweep()` 掃描全部既有事件做 expiry/monitoring 檢查。 |
| `notification_policy.py` | `NotificationPolicy`：`notification_state × priority → should_notify + notification_reason`。 |
| `memory_pipeline.py` | 整合層 `apply_persistent_memory()`：串接 matcher → 欄位合併 → 重新評分 → lifecycle → material change → notification → persist，供 `maritime_news.py` 與測試共用。 |
| `tests/test_memory_lifecycle.py` | CASE 3-A〜3-K 對應的 12 項生命週期測試。 |
| `tests/test_memory_store.py` | 10 項 Persistent Store 基礎設施測試（restart / URL dedup / version / transaction / schema / DB failure / baseline mode）。 |
| `phase3_simulation.py` | 一次性驗證腳本：三次連續 run 的模擬（見第 15 節），使用暫存 SQLite，不是 production pipeline 的一部分。 |

## 2. Modified Files

| 檔案 | 修改內容與原因 |
|---|---|
| `models.py` | 新增 `EventStatus`、`NotificationState` 兩個命名空間類別；`MaritimeEvent` 新增 operational fields（vessel_status/casualty_status/crew_*/fire_status/pollution_status/port_status/navigation_status/cargo_status/operational_status）、`canonical_key`、`imo_number`、`content_fingerprint`、`version`、`last_material_update`、`notification_state`、`should_notify`、`notification_reason`、`change_reason`、`run_id`。全部 nullable，不影響既有 Phase 2/2.1 程式碼路徑。 |
| `maritime_news.py` | 新增 Phase 3 imports；`run_intelligence_pipeline()` 在既有 cluster+score 之後接上 `apply_persistent_memory()`；新增 `filter_compat_for_notification()`（Compatibility Adapter，只保留 should_notify 事件對應的文章給舊版 Email 用）；回傳值新增 `all_current_events` / `notification_events` / `memory_run_id`；`__main__` banner 文字更新為 Phase 3。**未修改**任何 scraper 抓取邏輯、`fetch_from_source()`、`_download_rss()`、Email HTML 渲染邏輯。 |
| `.gitignore` | 新增 `data/*.db-shm`、`data/*.db-wal`（WAL 模式的輔助檔案，同樣不可提交）。 |
| `tests/conftest.py` | 新增 `memory_rules`、`event_store`（tmp_path）、`make_dated_article`、`run_memory_cycle` 四個 Phase 3 專用 fixture；**未修改**任何既有 Phase 1/2/2.1 fixture。 |

---

## 3. Database Schema

檔案位置：`data/maritime_intelligence.db`（`MARITIME_DB_PATH` 環境變數可覆寫），程式自動 `mkdir` 建立 `data/` 目錄。`schema_version = 1`（存於 `schema_meta` 表，未來版本比目前程式支援的新則拒絕啟動）。

**`events`**（PK: `event_id`）
索引：`canonical_key`、`event_status`、`last_seen_utc`、`event_type`。
欄位涵蓋：身份（canonical_key/imo_number/vessel_name/vessel_type/carrier）、地理（location/country/region/port/sea_area/shipping_lane）、時間（first_seen_utc/last_seen_utc/last_material_update_utc）、三個獨立狀態（event_status/information_status/confidence_level/management_priority）、五個評分子項、article_count/independent_source_count、operational fields（vessel_status…operational_status）、content_fingerprint、version、notification_state、change_reason、last_run_id、created_at_utc/updated_at_utc。

**`event_articles`**（PK: `id` autoincrement，`UNIQUE(event_id, article_id)`）
索引：`event_id`、`normalized_url`。防止同一篇文章因為每次 execution 重新 insert；`normalized_url` 供 tracking-parameter 去重查詢。

**`event_history`**（PK: `id` autoincrement）
索引：`event_id`。欄位：`change_type` / `old_value_json` / `new_value_json` / `change_reason` / `material` / `run_id` / `timestamp_utc`。

**`system_runs`**（PK: `run_id`）
欄位：started_at_utc / completed_at_utc / articles_collected / valid_articles / events_detected / new_events / material_updates / unchanged_events / resolved_events / status / error_message。

**`schema_meta`**（PK: `key`）：目前只有一筆 `schema_version`。

---

## 4. Event Identity Strategy

`EventIdentityBuilder` 依照可得資訊組出 canonical_key，優先序：

1. **IMO**（`IMO_<imo>`）— schema 已預留，目前無抽取來源。
2. **Vessel**（`VESSEL_<正規化船名>_<event_type>_<日期bucket>`）— 船名正規化把 `"MSC Orion"`/`"m.s.c. orion"` 都收斂成 `MSC_ORION`。
3. **Carrier+Location**（`CARRIER_<carrier>_<event_type>_<sea_area>_<日期bucket>`）— 未知船名時使用。
4. **Weak**（`WEAK_<event_type>_<location>_<標題指紋>_<日期bucket>`）。

**canonical_key 只是資料庫索引的 fast path，不是唯一比對依據**（§十五要求）。`PersistentEventMatcher.find_match()` 流程：先用 canonical_key 精確查找既有事件（若命中且非 hard-reject 直接採用），找不到則在該 event_type 對應的搜尋窗口（`memory_rules.matching_windows.by_event_type`，例如 SAFETY/SECURITY/CREW=14 天，REGULATORY=30 天，MARKET=3 天，其餘 7 天）內對 ACTIVE/MONITORING/RESOLVED 的既有事件做 fuzzy 比對，正負訊號沿用 Phase 2.1 哲學：

- **Hard reject**：兩邊都有 IMO 且不同、或都有船名且不同 → 直接 `-9999`。
- **正向**：same_vessel_name(60)、same_carrier(15)、same_incident_subtype(30)、same_event_type(15)、same_sea_area(15)、related_region_group(10)、`carrier_and_location_bonus`(20，只有在 incident_subtype 沒有明確衝突時才給，見下方 Bug Fix 說明)、title_similarity(15)、time_proximity(5)。
- **負向**：different_vessel_type(-70)、different_incident_subtype(-35)、different_event_type(-10，只在雙方都有「非 OTHER」的具體 event_type 時才比較——後續進度報導常常抓不到具體分類關鍵字，籠統的 `OTHER` 視同缺資料，不當衝突訊號）。

**未知船名後來揭露時如何保持同 Event ID**：比對永遠先用「這次 run 自己抽取到的欄位」（未合併）去搜尋，找到既有事件後才合併欄位（新資訊優先，缺資料 fall back 既有事實），並且**沿用既有 event_id，只更新 canonical_key**——不會因為身份升級（例如從 Level 4 升到 Level 2）就產生新 ID。實測見第 10 節。

**開發過程中修正的 2 個 bug**（不是 fixture 問題，是 persistent_matcher 演算法本身）：

1. 初版 `carrier_and_location_bonus` 沒有排除 `incident_subtype` 衝突的情況，導致「MSC grounding」與「MSC collision」（同航商同海域、不同具體事故）在跨 run 情境下被誤判成同一事件——用真實測試（非 fixture）抓到後修正為「只有在 subtype 不衝突時才給這個加分」。
2. `different_event_type` 原本沒有排除 `OTHER`，導致「refloated」這類進度更新文章（分類不到具體 event_type）被誤判成跟原事件衝突而配對失敗。

## 5. Lifecycle Model

`event_status`（`ACTIVE` / `MONITORING` / `RESOLVED` / `EXPIRED`）與 `management_priority` / `information_status` 完全獨立（§十九），只回答「事件現在處於什麼生命週期」。

- **ACTIVE**：預設狀態；新事件、或有 Material Update 的既有事件維持/回到 ACTIVE。
- **MONITORING**：`sweep()` 掃描全部 ACTIVE 事件，若最近一次 Material Update 已經超過 `monitoring_after_days`（預設 3 天）沒有更新，降級為 MONITORING（仍持續追蹤，只是不再視為新鮮事件）。
- **RESOLVED**：新文章文字命中 `resolution_keywords`（例如 "fire extinguished" / "port reopened" / "refloated" / 中文對應詞）才會標記，不會過度積極自動判定。
- **EXPIRED**：`sweep()` 對每個既有 ACTIVE/MONITORING 事件檢查：`now - last_seen_utc > expiry_days(event_type)` 且最新文字沒有 `ongoing` 關鍵字才會過期；`expiry_days` 依 event_type 分開設定（SAFETY/SECURITY/CREW=14 天、REGULATORY=30 天、OPERATIONS=7 天、MARKET=3 天，其餘 7 天，全部在 `memory_rules.json`）。
- **REOPENED**：`RESOLVED` 事件若新文章文字命中 `reopen_keywords`（例如 "closed again" / "reinstated restriction"）→ 回到 `ACTIVE`，並記一筆 `REOPENED` history。

## 6. Notification State

`NEW` / `MATERIAL_UPDATE` / `MINOR_UPDATE` / `UNCHANGED` / `RESOLVED_UPDATE`，與 lifecycle/priority/confidence 完全獨立的第四個概念：

- **NEW**：`PersistentEventMatcher` 找不到匹配的既有事件。
- **MATERIAL_UPDATE**：`MaterialChangeDetector.compare()` 判斷任一結構化事實變化具有 management significance；或事件從 RESOLVED 狀態 `REOPENED`（視為 Material）。
- **MINOR_UPDATE**：有變化（例如新來源、confidence 小幅波動）但沒有觸發任何 Material 規則。
- **UNCHANGED**：結構化事實完全沒有變化（可能新增了轉載文章，但事實不變）。
- **RESOLVED_UPDATE**：這次比對確認事件轉為 `RESOLVED`。

## 7. Material Update Rules（§二十五 A〜I 對照實作）

| 規則 | Trigger | change_type |
|---|---|---|
| A. Priority Escalation | RANK 數字變小（更急迫） | `PRIORITY_CHANGED`（降級的話同樣記錄但 `material=False`） |
| B. Significant Score Increase | severity_score 或 management_score 增加 ≥ 15（可設定） | `SEVERITY_CHANGED` / `MANAGEMENT_SCORE_INCREASED` |
| C. Casualty Development | casualty_status 沿升級順序前進，或 crew_injured/fatalities/missing 數字增加 | `CASUALTY_UPDATE` |
| D. Vessel Condition | vessel_status 或 fire_status 改變（例如 GROUNDED→REFLOATED、ONGOING→EXTINGUISHED） | `VESSEL_STATUS_UPDATE` |
| E. Security Escalation | SECURITY 事件的 incident_subtype 改變 | `SECURITY_ESCALATION` |
| F. Port/Navigation Change | port_status 或 navigation_status 改變 | `PORT_STATUS_UPDATE` |
| G. Confidence Upgrade | confidence_level 或 information_status 升級，**且**事件目前是 P1/P2 | `CONFIDENCE_CHANGED` / `INFORMATION_STATUS_CHANGED` |
| H. Fleet Relevance Change | fleet_relevance_score 增加 ≥ 10（可設定） | `FLEET_RELEVANCE_CHANGED` |
| I. Vessel Identity Revealed | vessel_name 或 carrier 從 None 變成有值 | `VESSEL_STATUS_UPDATE` / `FLEET_RELEVANCE_CHANGED` |

**架構性排除（§二十六）**：`MaterialChangeDetector` 只讀 Structured Fact Snapshot（`build_snapshot()`），完全不包含 article_count、headline/summary 原始文字、URL、published time 格式、primary_source——這些欄位根本不在比較範圍內，不需要另外寫「排除規則」，架構上就不可能觸發 Material。`SOURCE_ADDED`（新文章加入但事實不變）另外記錄成非 material 的 history 事件。

## 8. Suppression Logic

- `MINOR_UPDATE` / `UNCHANGED`：一律不通知（`memory_rules.notification` 對照表）。
- `NEW` / `MATERIAL_UPDATE` / `RESOLVED_UPDATE`：只有 P1/P2 立即通知；P3 保留給 Daily Brief（Phase 4 才會真的拆兩套 Email，本階段只標記 `should_notify=False` + reason 註明「reserved for daily brief」）；P4 純參考不通知。
- **RESOLVED_UPDATE 用「解除前曾經達到的最高 Priority」判斷是否通知**，不是解除當下重新算出來的（通常會下降的）Priority——避免「原本 P1 的事件解除時，因為當下文字severity較低，被悄悄壓下通知」。
- **Baseline Silent Mode**（`MEMORY_BASELINE_MODE=silent`）：資料庫是空的（系統第一次正式執行）時，事件仍正常寫入，但 `should_notify` 一律強制 `False`，避免上線第一天把歷史累積的所有事件當成告警轟炸主管信箱。預設是 `notify`（第一次執行時所有事件正常視為 NEW，依一般規則判斷）。

## 9. Confidence Update Behavior

展示（`test_early_signal_to_confirmed`，CASE 3-H）：

```
Run 1（Reddit 傳聞）：
  P1 / confidence=LOW / information_status=EARLY_SIGNAL

Run 2（Reuters 證實，同一 event_id）：
  notification_state = MATERIAL_UPDATE
  should_notify = True
  information_status 從 EARLY_SIGNAL 升級（UNCONFIRMED/CORROBORATED，視獨立來源 tier 而定）
```

confidence/information_status 的計算改成**跨 run 累計**：`_cumulative_independent_tiers()` 把資料庫裡這個事件既有的文章（含 family/tier）跟這次 run 的新文章合併後重新計算「每個獨立來源家族的最佳 tier」，而不是每個 run 都只看當次那幾篇文章。這是開發過程中發現並修正的一個真實 bug——若不這樣做，CASE 3-B（Reuters + TradeWinds 各自獨立報導）會因為每個 run 只看見「這次自己的 1 篇文章」而永遠卡在 MEDIUM，confidence 永遠無法真正升到 HIGH。

## 10. Wan Hai Fleet Relevance Update

展示（`test_wanhai_identity_revealed`，CASE 3-E 實際輸出）：

```
Run 1: Unknown containership collision near Kaohsiung
  carrier=None  fleet_relevance_score=10.0  priority=P3

Run 2: Wan Hai containership collision near Kaohsiung（同一 event_id）
  carrier=WAN_HAI  fleet_relevance_score=25.0  priority=P1
  notification_state=MATERIAL_UPDATE  should_notify=True
```

機制：比對（matching）先用這次 run 自己的欄位（carrier=None）去搜尋既有事件，找到之後才合併（carrier: None → 沿用嘗試新值，新值仍是 None 時 fall back 舊值；這裡是新值有 "WAN_HAI" 所以直接採用新值），**合併完之後重新呼叫 `RiskScorer.score_event()`**，讓 fleet_relevance / priority 正確反映「現在才知道是自家船隊」——這是 own-fleet critical override 邏輯（`_check_critical_override`）在合併後的欄位上重新判斷的結果，不是寫死的 `if carrier=="WAN_HAI"` 規則。

## 11. Resolution / Reopen

展示（`test_event_reopened`，CASE 3-J 實際輸出）：

```
Run 1: Singapore Strait canal restriction after incident
  event_status=ACTIVE

Run 2: canal restriction lifted, channel reopened（同一 event_id）
  event_status=RESOLVED  notification_state=RESOLVED_UPDATE

Run 3: canal restriction reinstated, channel closed again（同一 event_id）
  event_status=ACTIVE（REOPENED）  notification_state=MATERIAL_UPDATE
  history 含一筆 change_type=REOPENED
```

`event_history` 完整保留 RESOLVED → REOPENED 的轉換紀錄，供未來 Email／Dashboard 直接呈現「這件事的來龍去脈」。

## 12. Database Failure Behavior

`EventStore.__init__()` 把 `sqlite3.connect()` / `executescript()` 包在 `try/except (sqlite3.Error, OSError)`，任何失敗一律拋出 `EventStoreError`（`RuntimeError` 子類別）。`schema_version` 檢查也會在「資料庫被更新版本程式寫過」時主動拒絕啟動並拋出同一個例外。

`maritime_news.py::run_intelligence_pipeline()` **刻意不 try/except 吞掉這個例外**——讓它一路往上拋到 `__main__` 既有的 `except Exception` 區塊，統一 `logger.error()` + `exit(1)`。因為這個例外發生在 `sender.send()` 被呼叫之前，Email 保證不會被寄出。已用 `test_database_failure_is_fatal`（mock `sqlite3.connect` 拋出 `OSError`）驗證：不會 silent fallback 成「全部當新事件」。

## 13. Test Results

```
Previous tests (Phase 1/2/2.1): 45
New Phase 3 tests:               22
Total:                           67
Passed:                          67
Failed:                          0
```

`python -m py_compile` 對全部 `.py` 檔案（含新增與既有）零錯誤。整個 `tests/` 目錄零 live 網路依賴（無 `requests.get/post`、無 `smtplib`、無 `praw`、無指向真實 URL 的 `feedparser.parse`），全部 Phase 3 測試使用 pytest `tmp_path` 建立暫存 SQLite，不觸碰 production database (`data/maritime_intelligence.db` 從未在測試中被建立)。

代表性測試對應：

| 測試 | 對應情境 |
|---|---|
| `test_new_event_first_seen` | CASE 3-A（首次見到） |
| `test_existing_event_unchanged` | CASE 3-A（第二次完全相同 → UNCHANGED） |
| `test_same_event_new_source` | CASE 3-B（新媒體佐證，confidence 可能升級） |
| `test_material_casualty_update` | CASE 3-C |
| `test_priority_escalation_material` | CASE 3-D |
| `test_wanhai_identity_revealed` | CASE 3-E |
| `test_headline_change_not_material` | CASE 3-F |
| `test_repost_storm_suppressed` | CASE 3-G |
| `test_early_signal_to_confirmed` | CASE 3-H |
| `test_event_resolved` | CASE 3-I |
| `test_event_reopened` | CASE 3-J |
| `test_different_vessel_new_event` | CASE 3-K |
| `test_memory_survives_restart` | §六十四 |
| `test_tracking_url_dedup` / `test_tracking_url_dedup_find_article` | §六十五 |
| `test_database_transaction` | §四十三、七十一 Atomicity |
| `test_schema_version` | §四十五 |
| `test_database_failure_is_fatal` | §四十六、六十六 |
| `test_baseline_silent_mode` | §四十八 |

## 14. Fixture Changes

**沒有任何 Phase 1 / Phase 2 / Phase 2.1 的既有 fixture 或既有測試檔案被修改。** `tests/fixtures/articles.json` 與全部既有測試檔（`test_carrier_filter.py`/`test_clustering.py`/`test_clustering_hardening.py`/`test_confidence_priority.py`/`test_extraction.py`/`test_pipeline.py`/`test_risk_scoring.py`/`test_carrier_alias_audit.py`/`test_source_independence.py`/`test_ssl_config.py`）維持原樣，全程用「重跑整套舊測試」驗證沒有被破壞（見第 13 節：45 項全數通過）。`risk_rules.json` 本身在本階段**沒有被修改**（Phase 3 的設定全部新增在獨立的 `memory_rules.json`）。

過程中有 2 次 Phase 3 自己新模組（非既有 fixture）的演算法調整，特此列出（見第 4 節詳述）：

1. `persistent_matcher.py`：`carrier_and_location_bonus` 加上「incident_subtype 不衝突」的前提條件，避免同航商同海域的不同具體事故被誤判成同一事件。
2. `persistent_matcher.py`：`different_event_type` 排除籠統的 `OTHER` 分類，避免進度更新報導（抓不到具體分類關鍵字）配對失敗。

兩者都是在開發過程中用真實的困難情境（CASE 3-D 尾段的優先權比對、CASE 3-F/3-I 的進度更新用詞）測出來的演算法修正，不是調整測試資料去遷就演算法。

## 15. Example Three-Run Simulation

用 `phase3_simulation.py`（暫存 SQLite，不寫 production database）實際跑出來的結果：

**Run 1（08:00）— 全部 NEW：**
```
[NEW] evt_44d3ec2d748f9ce1  P=P2  MSC vessel grounding near Singapore
[NEW] evt_57c5b6eb715da86e  P=P2  Port congestion reported at Los Angeles terminal
Lifecycle: NEW=2  MATERIAL=0  UNCHANGED=0  RESOLVED=0
```

**Run 2（14:00）— NEW / MATERIAL_UPDATE / UNCHANGED 同時出現：**
```
[MATERIAL_UPDATE] evt_44d3ec2d748f9ce1  P=P2  MSC vessel grounding update
  reason: Casualty status changed unknown → INJURED; Crew injured count updated: unknown → 1;
          Confidence upgraded MEDIUM → HIGH; Information upgraded UNCONFIRMED → CORROBORATED
[UNCHANGED]       evt_57c5b6eb715da86e  P=P2  Port congestion reported at Los Angeles terminal
  reason: No change since last run
[NEW]             evt_1af09097d5304ded  P=P1  Container ship attacked in Red Sea
Lifecycle: NEW=1  MATERIAL=1  UNCHANGED=1  RESOLVED=0
```

**Run 3（20:00）— Lifecycle Transition（RESOLVED）：**
```
[RESOLVED_UPDATE] evt_44d3ec2d748f9ce1  P=P3(解除前為P2)  status=RESOLVED
  MSC vessel refloated near Singapore
  reason: Vessel status changed GROUNDED → REFLOATED; Resolution confirmed by source text
[UNCHANGED]       evt_57c5b6eb715da86e  P=P2  status=ACTIVE
  Port congestion reported at Los Angeles terminal
Lifecycle: MATERIAL=0  UNCHANGED=1  RESOLVED=1
```

同一起 MSC 事故的 `event_id`（`evt_44d3ec2d748f9ce1`）在三次 run 中完全沒有改變，完整走完 `NEW → MATERIAL_UPDATE → RESOLVED_UPDATE` 生命週期，符合 §八十一 最終驗收標準：**不會**是 `08:00 NEW / 14:00 NEW / 20:00 NEW`。

---

## 驗收問題自我檢查（§八十二）

1. 這件事情以前出現過嗎？→ `event_id` 跨 run 穩定，`PersistentEventMatcher` 回答。
2. 第一次是什麼時候看到？→ `events.first_seen_utc`（合併時鎖定不變）。
3. 最近一次什麼時候有新聞？→ `events.last_seen_utc`（每次比對到都會前進）。
4. 最近一次真正重要的變化是什麼時候？→ `events.last_material_update_utc`（只有 Material 才前進，第 13 節 `test_last_material_update` 驗證兩者確實分開）。
5. 事件現在仍然 active 嗎？→ `events.event_status`。
6. 跟上次相比什麼改變？→ `event_history` 逐筆 `change_type` + `old_value_json`/`new_value_json`。
7. 這個改變值得重新通知主管嗎？→ `notification_state` + `should_notify`。
8. 為什麼值得通知？→ `notification_reason` / `change_reason`（人類可讀）。
9. 這個事件過去有哪些來源？→ `event_articles` 表（含 source_family/source_tier）。
10. Priority/Confidence 如何演變？→ `event_history` 中的 `PRIORITY_CHANGED`/`CONFIDENCE_CHANGED`/`INFORMATION_STATUS_CHANGED` 條目。

---

本階段到此為止，等待確認後再開始 **Phase 4 — Executive Maritime Intelligence Email**。
