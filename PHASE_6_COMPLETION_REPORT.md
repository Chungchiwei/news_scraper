# Phase 6 完成報告 — Fleet, Route & Port Operational Relevance Integration

範圍聲明：本階段只新增一個完全獨立的「Operational Relevance」維度（Own Fleet Matching / Port Call Exposure / Route Exposure / Regional & Regulatory Exposure / Exposure Lifecycle / Executive Email Fleet Exposure 區塊 / LLM Privacy Guardrail）。**未修改**任何 Phase 1-5 的 severity/priority/confidence/information_status 判定邏輯、Event Clustering、Persistent Memory 比對演算法、既有 Email 版面主結構、既有 113 項測試。Teams / Dashboard / Port Weather Integration / Internal API（Phase 7）本輪未實作，依指示停在本報告，等待人工確認。

---

## 1. New Files

| 檔案 | 用途 |
|---|---|
| `operational_rules.json` | Phase 6 設定檔：`relevance_thresholds`（DIRECT/HIGH/MODERATE/LOW 分數門檻）、`eta_windows_hours`（immediate/high/moderate/watch）、`weights`（own_fleet/port_call/route/regulatory 各項權重）、`data_freshness`（schedule/fleet 過期時數）、事件類型白名單（`port_call_relevance_event_types`、`own_fleet_carrier_context_event_types`、`route_relevant_event_types`）、`global_fleet_regulatory_keywords`、`market_relevant_event_keywords`、`email_display`。全部門檻/權重集中於此，不寫死在 Python（§三十四）。 |
| `operational_models.py` | `RelevanceLevel`（DIRECT/HIGH/MODERATE/LOW/NONE + ORDER/RANK）、`RelevanceStatus`（ASSESSED/DATA_STALE/UNAVAILABLE）、`ExposureType`（OWN_VESSEL/PORT_CALL/SERVICE_ROUTE/SHIPPING_LANE/REGIONAL/GLOBAL_FLEET/NONE）、`OperationalNotificationState`（EXPOSURE_NEW/ESCALATED/UNCHANGED/REDUCED/CLEARED/UNAVAILABLE）、`FleetVessel`/`PortCall`/`Service`/`AffectedVessel`/`OperationalRelevance` dataclass。 |
| `operational_config.py` | `load_operational_rules()`（`lru_cache`），與 `risk_config.py`/`memory_config.py`/`email_config.py`/`llm_config.py` 同一種載入模式。 |
| `port_normalizer.py` | `PortNormalizer`：把英文/繁中/簡中/常見別名/UN-LOCODE 各種寫法正規化成單一 UN/LOCODE；找不到明確對應一律回傳 `None`，不做 substring/模糊猜測；支援 Phase 2「中文 / English」組合格式。 |
| `config/ports_config.json` | 港口別名表，涵蓋 22 個主要港口（Singapore/Kaohsiung/Keelung/Taipei Port/Hong Kong/Shanghai/Ningbo/Shenzhen/LA/Long Beach/Oakland/Rotterdam/Hamburg/Antwerp/Busan/Tokyo/Yokohama/Laem Chabang/Jakarta/Manila/Ho Chi Minh/Colombo/Jebel Ali）。刻意不收錄 "Portland"（多國同名、缺國家別資訊無法唯一判定）。 |
| `fleet_provider.py` | `FleetDataProvider` 介面（ABC）+ `ConfigFleetProvider`（讀 `config/fleet_config.json`，單筆缺 vessel_id/vessel_name 記 WARNING 並略過，不整批 crash）+ `FakeFleetProvider`（測試用，支援 `raise_error=True` 模擬 Provider 失敗）。 |
| `schedule_provider.py` | `ScheduleDataProvider` 介面 + `ConfigScheduleProvider`（讀 `config/schedules_config.json`，ETA/ETD 格式錯誤或 ETD 早於 ETA 一律整筆略過並記 WARNING）+ `FakeScheduleProvider`。 |
| `route_provider.py` | `RouteDataProvider` 介面 + `ConfigRouteProvider`（讀 `config/services_config.json`）+ `FakeRouteProvider`。 |
| `config/fleet_config.json` / `config/schedules_config.json` / `config/services_config.json` | Local Config Provider 的第一版資料來源，目前為空陣列佔位（`vessels: []` / `port_calls: []` / `services: []`），附 `_comment` 說明實際使用前需以真實資料取代；未來要接公司內部 API/DB，只需新增一個實作對應介面的 Provider 類別，`operational_relevance.py` 完全不需修改。 |
| `fleet_relevance.py` | `FleetRelevanceEngine`：IMO 精確比對 → 正規化船名精確比對 → Carrier-only fallback（僅限特定 event_type，且不設 `own_fleet_involved=True`）三段式判斷；`normalize_vessel_name()` 只做空白/大小寫正規化，無 fuzzy/substring 比對。 |
| `port_relevance.py` | `PortRelevanceEngine`：`resolve_event_port_code()` 拒絕在 `event.sea_area`/`event.shipping_lane` 已標示時 fallback 到 `location` 猜港口；`assess()` 過濾未來 Port Call、依 ETA 排序、依時間窗計分。 |
| `route_relevance.py` | `RouteRelevanceEngine`：比對 `event.sea_area`/`event.shipping_lane` 是否落在某 Service 的 `major_shipping_lanes`；沒有改道資料就不擅自調降既有曝險。 |
| `geographic_relevance.py` | `GeographicRelevanceEngine`（region 廣域比對）+ `GlobalFleetRegulatoryEngine`（REGULATORY 事件的關鍵字比對，rule-based，非 LLM 判斷）。 |
| `operational_relevance.py` | `OperationalRelevanceEngine`：整合上述五個子引擎，實作 Provider 載入快取、Categorical Override（own fleet 精確比對 → 一律 DIRECT）、Exposure Type 聚合、`relevance_reasons` 產生、Stale 判斷、`diagnostics_report()`。 |
| `operational_history.py` | `OperationalHistoryStore`（獨立 SQLite `operational_relevance_history` 表，獨立資料庫檔案，不碰 Phase 3 `event_store.py`）+ `NullOperationalHistoryStore`（安全退化）+ `compute_operational_notification_state()`（RANK-based 跨 run 比對）。 |
| `tests/fixtures/fleet.json` / `schedules.json` / `services.json` | Phase 6 測試用 fixture（非 runtime config），刻意各包含一筆壞資料（缺 vessel_name、ETA 格式錯誤、ETD 早於 ETA），供 Provider 資料驗證測試使用。 |
| `tests/test_phase6_operational.py` | 23 項指定單元測試（見第 16 節）。 |
| `phase6_simulation.py` | Three-Run Exposure Simulation 驗證腳本（見本報告末尾），使用暫存 SQLite，非 production pipeline 一部分。 |

## 2. Modified Files

| 檔案 | 修改內容與原因 |
|---|---|
| `llm_config.py` | `LLMConfig` 新增 `allow_internal_operational_data: bool = False` 欄位（環境變數 `LLM_ALLOW_INTERNAL_OPERATIONAL_DATA`，預設 `false`）；`redacted()` 輸出新增此欄位供診斷；不影響既有 `enabled`/`provider`/timeout/retry 邏輯。 |
| `source_grounding.py` | `GroundedInputPackage` 新增 `operational_context: Optional[dict] = None` 欄位；新增 `sanitize_operational_context()`（把 `OperationalRelevance` 清洗成只含 `relevance_level`/受影響船舶**數量**/`affected_services`/`closest_eta_hours` 的最小化 dict，`UNAVAILABLE` 一律回傳 `None`）；`build_grounded_input()`/`build_user_payload()` 新增可選參數，只有非 `None` 時才在 prompt 中附加 `OPERATIONAL_CONTEXT` 區塊。 |
| `intelligence_analyzer.py` | `analyze_event()`/`analyze_events()` 新增可選的 `operational_relevance`/`operational_relevance_map` 參數；只有 `self.config.allow_internal_operational_data is True` 且呼叫端有提供時，才呼叫 `sanitize_operational_context()` 並傳入 prompt——預設路徑完全不變。 |
| `email_view_model.py` | 新增 `_build_exposure_fields()`/`_append_exposure_summary()`；`EmailEventViewModel` 新增 `has_operational_assessment`/`relevance_level`/`relevance_status`/`operational_own_fleet_match`/`exposure_vessel_names`/`exposure_vessels_display`/`exposure_service_codes`/`exposure_closest_eta_display`/`exposure_no_direct_text`/`exposure_unavailable_text`/`exposure_is_stale`/`exposure_stale_note`；`ExecutiveBriefViewModel` 新增彙總欄位（`exposure_direct_count`/`exposure_high_count`/`exposure_affected_vessel_count`/`exposure_unavailable`/`exposure_stale`）；`build_event_view_model()`/`build_daily_brief_view_model()`/`build_alert_view_model()` 新增可選的 `operational_relevance`/`operational_relevance_map` 參數，沒有提供時所有新欄位維持預設值（`has_operational_assessment=False`），對純 Phase 1-5 呼叫端行為零影響。 |
| `executive_email_renderer.py` | 新增 `_exposure_summary_block()`（頂部 WHL FLEET EXPOSURE 彙總）與 `_exposure_card_block()`（單一事件卡片的 WHL OPERATIONAL EXPOSURE 區塊，DIRECT/HIGH/MODERATE/LOW 用彩色徽章、NONE 用「未發現直接曝險」、UNAVAILABLE 用明確措辭），插入 `_render()`/`_event_card()` 既有流程；`has_operational_assessment=False` 時兩個區塊都不渲染。 |
| `maritime_news.py` | 新增 Phase 6 imports；新增 `_run_operational_relevance()`（建立 Provider/Engine、對 selection 的事件跑 `assess()`、寫入/讀取 `OperationalHistoryStore`、記錄 `diagnostics_report()`，整段包在 try/except，引擎建置失敗只讓 Fleet Exposure 區塊不顯示，不影響 Email 發送）；`__main__` 在 `ai_analyses` 之後呼叫 `_run_operational_relevance()`，並把結果傳入 `build_daily_brief_view_model()`。**未修改**任何 scraper、Phase 1-5 pipeline、SMTP 邏輯。 |

---

## 3. Provider Architecture

Fleet/Schedule/Route 三種資料各自定義一個 `ABC` 介面（`FleetDataProvider`/`ScheduleDataProvider`/`RouteDataProvider`，各兩個方法：取得清單 + `data_timestamp()`）。第一版實作 `Config*Provider` 讀本地 `config/*.json`；測試/預覽用 `Fake*Provider` 直接注入 Python 物件、並支援 `raise_error=True` 模擬 Provider 失敗。`OperationalRelevanceEngine.__init__()` 只依賴介面，完全不知道背後是本機檔案還是公司內部系統——未來要接真實 Fleet Management System / AIS / 排程資料庫，只需新增一個實作對應介面的類別，`operational_relevance.py`、`fleet_relevance.py`、`port_relevance.py`、`route_relevance.py` 全部不需修改。這跟 Phase 5 的 `LLMProvider` 抽象是同一種設計語言。

Provider 層級的失敗（檔案不存在、格式損毀、模擬的 `raise_error`）一律讓例外往上拋，不得靜默回傳空清單假裝「沒有船/沒有航班」——這個責任分界很重要：**Provider 負責誠實地失敗，Engine 負責把失敗轉成 `UNAVAILABLE` 狀態**（見第 12 節）。單筆資料格式問題（缺欄位、ETA 解析失敗、ETD 早於 ETA）則是 Provider 內部處理：略過該筆並記 WARNING，不影響其餘資料，也不讓整個 Provider 失敗。

## 4. Data Models

`operational_models.py` 定義的核心輸出結構 `OperationalRelevance`：

| 欄位 | 說明 |
|---|---|
| `event_id` | 對應的 MaritimeEvent |
| `relevance_level` | DIRECT/HIGH/MODERATE/LOW/NONE，`UNAVAILABLE` 時為 `None` |
| `relevance_score` | 0-100（`UNAVAILABLE` 時為 `None`） |
| `relevance_status` | ASSESSED / DATA_STALE / UNAVAILABLE |
| `own_fleet_involved` | 是否為 IMO/船名精確比對 |
| `affected_vessels` | `list[AffectedVessel]`（vessel_name/service_code/next_port/eta_display/exposure_type/hours_to_exposure），已依 ETA 排序 |
| `affected_services` / `affected_ports` | 受影響的 Service 代碼 / 港口代碼 |
| `exposure_types` | `list[ExposureType]`，可同時多個（OWN_VESSEL/PORT_CALL/SERVICE_ROUTE/SHIPPING_LANE/REGIONAL/GLOBAL_FLEET） |
| `closest_eta_hours` | 最近一筆相關 Port Call 的距離時數 |
| `direct_match_count` / `potential_match_count` | 精確匹配數 / 潛在匹配數 |
| `relevance_reasons` | 英文、deterministic 的判定理由句 |
| `data_timestamp` / `is_stale` | 船期資料時間戳 / 是否過期 |
| `assessed_at` / `run_id` | 本次評估時間 / run 識別碼 |

`RelevanceLevel.RANK`（DIRECT=0…NONE=4）供跨 run 比較用（數字越小代表曝險越高）。`OperationalNotificationState` 比 spec 建議的 5 種多了一種 `EXPOSURE_UNAVAILABLE`（我方新增，處理 Provider 失敗時的第三種狀態，不屬於「升高/降低/不變」中任何一種）。

## 5. Port Normalization

`PortNormalizer.normalize()` 只做「精確別名表查詢」：先嘗試整段字串，若含 `" / "`（Phase 2 常見的「中文 / English」格式）則額外嘗試拆開後的兩段；找不到就回傳 `None`，**絕不**做 substring 或模糊比對。`config/ports_config.json` 刻意不收錄「Portland」這種在美國奧勒岡州、緬因州等多地都存在同名港口、缺乏國家/UNLOCODE 資訊就無法唯一判定的地名（CASE 6-N，`test_ambiguous_port_rejected` 驗證）。

`port_relevance.py.resolve_event_port_code()` 是 Sea Area/Shipping Lane 與 Port 分離的關鍵：只有在 `event.sea_area`/`event.shipping_lane` 都是 `None` 時，才會嘗試把 `event.location` 當作港口名稱去猜；只要事件已經被歸類為某個海域/航道（例如「新加坡海峽」），就完全不會去比對 `config/ports_config.json` 裡「新加坡港」的 Port Call（CASE 6-O，`test_shipping_lane_not_equal_port` 驗證）——這個判斷屬於 `route_relevance.py` 的職責，兩者的資料來源（`port_code` vs `major_shipping_lanes`）完全不同，架構上就不會混淆。

## 6. Own Fleet Matching

`FleetRelevanceEngine.match()` 三段式優先序：

1. **IMO 精確比對**：兩邊 IMO 字串完全相等（trim 後）→ `own_fleet_involved=True`。
2. **正規化船名精確比對**：`normalize_vessel_name()` 只做「壓縮空白 + 轉大寫」，不做任何模糊/substring 比對——"WAN HAI 503" 與 "WAN HAI 505" 永遠是兩艘不同的船（CASE 6-B，`test_different_wanhai_vessel_no_match` 驗證）。
3. **Carrier-only fallback**：新聞只標示 `carrier=WAN_HAI`、沒有具體船名/IMO 時，只在事件類型屬於「可能是操作性事故」的範圍（`own_fleet_carrier_context_event_types`：OPERATIONS/SAFETY/SECURITY/ENVIRONMENT/CREW，刻意排除 MARKET/COMPETITOR）才給予較低分數（權重 30，落在 LOW），且**明確不設** `own_fleet_involved=True`——一般 Wan Hai 企業公告或市場新聞不代表事故直接涉及本公司船舶。

`operational_relevance.py.assess()` 實作 Categorical Override：`if fleet_result.own_fleet_involved: level = DIRECT`，不經過一般分數門檻判斷。這是刻意的設計決定——即使未來調整權重數值，只要確認是本公司船舶，DIRECT 判定永遠不會被權重調整意外破壞（目前 `own_fleet_imo_match`/`own_fleet_vessel_name_match` 權重 90 本身也已超過 DIRECT 門檻 80，兩條路徑目前互相一致，但 Categorical Override 提供了語意上更清楚、更抗變動的保證）。

## 7. Port Exposure Logic

`PortRelevanceEngine.assess()`：先用 `resolve_event_port_code()` 拿到港口代碼，過濾出 `port_code` 相符且 ETA 在未來（`hours >= 0`）的 Port Call，依時間由近到遠排序，依最近一筆的距離時數計分（CASE 6-J 多船排序：`test_multiple_vessels_sorted_eta` 驗證 `affected_vessels` 排序與 `closest_eta_hours` 取最小值）。

ETA 時間窗（`eta_windows_hours`）：`immediate`(24h) / `high`(48h) / `moderate`(72h) / `watch`(120h)，超過 120h 用最低權重。

## 8. Route Exposure

`RouteRelevanceEngine.assess()` 比對 `event.sea_area`/`event.shipping_lane` 是否出現在任一 `Service.major_shipping_lanes`；`GeographicRelevanceEngine.assess()` 是更寬鬆的一層，比對 `event.region` 是否出現在 `Service.regions`（用於事件沒有明確海域/航道，但仍屬同一大區域的情況）。兩者都刻意**不會**因為 config 沒有標示某條 Service 已改道（例如繞行好望角）就自動調降既有曝險——沒有資料就是沒有資料，不能拿來降低已知的評估。`GlobalFleetRegulatoryEngine` 則是完全不同的路徑：只有 `event_type == REGULATORY` 才觸發，用關鍵字白名單（SOLAS/MARPOL/IMO/PSC/...）比對 headline + primary article summary，rule-based、非 LLM 判斷法規適用範圍。

## 9. Operational Relevance Score

門檻（`relevance_thresholds`）：DIRECT≥80、HIGH≥60、MODERATE≥35、LOW≥15，低於 15 為 NONE。各子引擎分數加總（上限 100），再對照門檻分級，own fleet 精確比對另有 Categorical Override（見第 6 節）。

**權重調整說明（本報告依規範要求，明確記錄任何偏離 spec 示意數值的理由）**：規範文件（§三十二）給出的示意權重表（port_call 25/20/15/10/5）若直接套用規範自己的門檻表（§三十四：HIGH≥60），會導致「僅有單一強訊號（例如 18h 內有 Port Call，沒有其他佐證）」永遠落不到 HIGH，只能停在 LOW——這與規範 CASE 6-C「18h ETA 應達到 HIGH 或以上」的期望互相矛盾。規範文字本身也明確授權「Threshold：config-driven」與 CASE 6-C 的「依照 configured threshold」措辭，因此本階段調整了 `operational_rules.json` 的權重數值（例如 `port_call_immediate` 從示意的 25 調整為 68），採取的設計原則是：**寧可對單一強訊號提早示警，也不要讓主管誤以為「分數不夠高」代表「不重要」**——這個原則已寫入 `operational_rules.json.weights._note` 供未來維護者參考。目前的權重讓：18h Port Call 單獨即達 HIGH、40h Port Call 單獨即達 HIGH（供 Escalation 情境使用）、96h Port Call 單獨落在 MODERATE、Red Sea 航線曝險落在 MODERATE、REGULATORY 關鍵字命中恰好落在 HIGH 門檻。`test_operational_score_config` 額外驗證了門檻本身是 config-driven（換一份 `relevance_thresholds` 設定，同樣的分數會落到不同 Level），而非寫死在 Python 判斷式中。

## 10. Relevance vs Severity

`OperationalRelevanceEngine` 完全不讀取、也不寫入 `event.severity_score`/`event.management_priority`/`event.confidence_level`/`event.information_status`/`event.notification_state` 等 Phase 1-5 已經決定好的欄位——`assess()` 只讀取事件的「事實」欄位（vessel_name/imo_number/carrier/location/port/sea_area/shipping_lane/region/event_type/headline）做比對，回傳的 `OperationalRelevance` 是完全獨立的新物件。`test_event_confidence_unchanged_by_relevance` 明確驗證：即使事件被判定為 DIRECT 曝險（本公司船舶精確比對），事件本身的 `confidence_level`/`management_priority`/`information_status`/`management_score` 在 `assess()` 呼叫前後逐位元組完全相同。Executive Email 的 Priority 分桶（P1-P4，決定事件出現在哪個區塊）也完全不受 Fleet Exposure 影響——`executive_email_renderer.py` 的 Fleet Exposure 徽章只是附加資訊，不會把一個 P3 事件因為 DIRECT 曝險而搬進 P1 區塊。

## 11. Exposure Lifecycle

`OperationalNotificationState`（EXPOSURE_NEW/ESCALATED/UNCHANGED/REDUCED/CLEARED/UNAVAILABLE）是完全獨立於 Phase 3 `NotificationState`（NEW/MATERIAL_UPDATE/MINOR_UPDATE/UNCHANGED/RESOLVED_UPDATE）的第二條時間軸，兩者刻意存放在不同物件、不同資料庫（`operational_relevance_history` vs Phase 3 的 `event_history`）。`compute_operational_notification_state(previous, current)` 用 `RelevanceLevel.RANK` 的數字比較：rank 變小 = ESCALATED，rank 變大且變成 NONE = CLEARED，變大但非 NONE = REDUCED，不變 = UNCHANGED；`current.relevance_status == UNAVAILABLE` 直接短路回傳 `EXPOSURE_UNAVAILABLE`（不污染下一次比對基準，因為 `OperationalHistoryStore.save_snapshot()` 不會儲存 UNAVAILABLE 快照）。`test_exposure_escalation`/`test_exposure_cleared` 與本報告末尾的 Three-Run Simulation 共同證明了核心主張：**事件本身可以維持 Phase 3 UNCHANGED，Operational Exposure 仍能獨立判定為 ESCALATED/CLEARED**。

## 12. Provider Failure

`OperationalRelevanceEngine._load_data()` 把 Fleet/Schedule/Route 任一 Provider 的失敗視為**整個引擎本次 run 的失敗**（而非細粒度的「Fleet 掛了但 Port 還能用」）——這是刻意的簡化設計決定：細粒度部分失敗會讓「Overall Level 是 NONE 還是實際上 Fleet 未知」的語意變得混淆不清，簡化成「只要有一個 Provider 失敗就整體 UNAVAILABLE」在正確性與可理解性上更安全。`assess()` 在 `_load_error=True` 時直接回傳 `relevance_level=None, relevance_status=UNAVAILABLE`，**絕不**回傳 `NONE`（`test_fleet_provider_failure_unknown` 驗證 `relevance_level != RelevanceLevel.NONE`）。Email 端 `_exposure_card_block()` 對 `UNAVAILABLE` 有獨立的措辭分支（"WHL Operational Exposure: Unavailable..."），不會被 NONE 分支的「未發現直接曝險」文字誤用（`test_email_unavailable_exposure_wording` 驗證）。`maritime_news.py._run_operational_relevance()` 的外層 try/except 只防守「引擎本身建置失敗」（設定檔遺失/損毀）這種更根本的問題，此時整段 Fleet Exposure 區塊不顯示，但不會讓 Executive Email 因此發不出去——與 Phase 5 LLM Enhancement 是同一種 Non-Critical Dependency 處理原則。

## 13. Stale Data

`_is_stale(now)` 比較 `schedule_provider.data_timestamp()` 與當前時間，超過 `data_freshness.schedule_stale_after_hours`（預設 12 小時）即標記 `is_stale=True`、`relevance_status=DATA_STALE`（`test_stale_schedule` 驗證）。Email 端 `email_view_model.py._build_exposure_fields()` 計算實際過期時數並產生雙語提示句（"船期曝險評估係依最後更新之船期資料（Xh 小時前），請留意資料時效 / Fleet relevance assessment based on schedule data last updated Xh ago"），`executive_email_renderer.py._exposure_card_block()` 在卡片底部渲染此提示，`_exposure_summary_block()` 也會在彙總區塊顯示過期警告——資料時效問題不會被隱藏，也不會被誤判成「已確認沒有曝險」。

## 14. Email Integration

**彙總區塊**（`_exposure_summary_block()`，插在 Executive Summary 之後）：顯示 "WHL FLEET EXPOSURE | Direct: N | High: N | Affected Vessels: N"，只在本次 run 實際跑過 Phase 6（`has_operational_assessment=True`）才渲染；若有事件因 Provider 失敗顯示 Unavailable，或有事件基於過期資料評估，會在彙總區塊下方加上對應警告句。

**單一事件卡片**（`_exposure_card_block()`，插在 WHY IT MATTERS 之後）：DIRECT/HIGH/MODERATE/LOW 用彩色徽章 + 受影響船舶清單（船名 + 目的港 + ETA）或 Service 代碼；NONE 用固定措辭「未發現直接曝險（No direct exposure identified）」（`test_email_no_exposure_wording` 驗證絕不出現「不受影響」這種過度武斷的措辭）；UNAVAILABLE 用獨立措辭並附上不等於 NONE 的說明。

**Executive Summary 補充**（`_append_exposure_summary()`）：在既有規則式摘要句子後面補一句船隊曝險資訊（例如「其中 1 起事件直接涉及本公司船舶...」），但完全不改變 Overall Risk（HIGH/ELEVATED/WATCH/NORMAL）或 P1-P4 分桶邏輯本身——那些仍然 100% 由 Phase 1-5 決定。

**排版原則**：本階段刻意沒有為了 Fleet Exposure 而重新排序事件卡片的出現順序（維持 Phase 4 既有的 Priority → Notification Badge 排序），Fleet Exposure 純粹是附加在既有卡片上的資訊層，不介入卡片本身的排序決策，避免「Fleet Exposure 高就自動排到最前面」造成 Priority 語意混淆。

## 15. LLM Privacy Guardrail

`LLM_ALLOW_INTERNAL_OPERATIONAL_DATA` 環境變數，production 預設 `false`。只有明確設為 `true`，且呼叫端有傳入 `operational_relevance`，`intelligence_analyzer.py.analyze_event()` 才會呼叫 `sanitize_operational_context()` 把資料清洗成只含 `relevance_level`、受影響船舶**數量**（不是名稱）、`affected_services`（Service 代碼）、`closest_eta_hours` 的最小化 dict，附加進 prompt 的 `OPERATIONAL_CONTEXT` 區塊，並在 prompt 中明確標註「reference only, you may not recompute or contradict these numbers」。船名、IMO、精確時間戳、內部港口代碼等細節絕不會出現在送往外部 LLM Provider 的 payload 中，即使功能被啟用也一樣。預設關閉狀態下，`intelligence_analyzer.py`/`source_grounding.py` 的行為與 Phase 5 完全相同（`operational_context=None`，prompt 不含此區塊）。

## 16. Tests

新增 `tests/test_phase6_operational.py`，23 項指定測試全數通過：

`test_own_fleet_exact_match`、`test_different_wanhai_vessel_no_match`、`test_port_eta_18h_high_exposure`、`test_port_eta_96h_moderate`、`test_no_port_call`、`test_red_sea_route_exposure`、`test_unrelated_route_no_exposure`、`test_global_regulatory_relevance`、`test_market_news_not_direct`、`test_exposure_escalation`、`test_exposure_cleared`、`test_fleet_provider_failure_unknown`、`test_stale_schedule`、`test_ambiguous_port_rejected`、`test_shipping_lane_not_equal_port`、`test_multiple_vessels_sorted_eta`、`test_event_confidence_unchanged_by_relevance`、`test_operational_score_config`、`test_operational_history_saved`、`test_operational_history_restart`、`test_email_operational_exposure_display`、`test_email_no_exposure_wording`、`test_email_unavailable_exposure_wording`。

全部使用 `FakeFleetProvider`/`FakeScheduleProvider`/`FakeRouteProvider`（`raise_error=True` 模擬失敗）+ `tmp_path` SQLite（`OperationalHistoryStore`），不連任何真實內部系統、真實 SMTP、真實 LLM、真實 Internet。

```
23 passed in 0.32s   (tests/test_phase6_operational.py 單獨執行)
```

## 17. Regression

```
136 passed in 2.04s   (tests/ 全部，含 Phase 1-5 原有 113 項 + Phase 6 新增 23 項)
```

原有 113 項測試（`test_carrier_alias_audit.py`、`test_carrier_filter.py`、`test_clustering.py`、`test_clustering_hardening.py`、`test_confidence_priority.py`、`test_extraction.py`、`test_memory_lifecycle.py`、`test_memory_store.py`、`test_phase4_email.py`、`test_phase5_llm.py`、`test_pipeline.py`、`test_risk_scoring.py`、`test_source_independence.py`、`test_ssl_config.py`）**零修改、零新增斷言**，全部維持原樣通過。`tests/conftest.py`、`tests/fixtures/articles.json` 也未被觸碰。

---

## Three-Run Exposure Simulation

`phase6_simulation.py` 使用同一個 `MaritimeEvent`（`sim_kaohsiung_congestion`，同一則新聞、同一個 headline/summary，Phase 3 `notification_state` 全程 `UNCHANGED`），只改變 Fake Schedule Provider 提供的船期資料，驗證核心主張 **EVENT UNCHANGED ≠ OPERATIONAL EXPOSURE UNCHANGED**：

```
Run 1 (ETA 96h):                relevance_level=MODERATE  operational_notification_state=EXPOSURE_NEW
Run 2 (ETA 40h, event 仍 UNCHANGED): relevance_level=HIGH  operational_notification_state=EXPOSURE_ESCALATED
Run 3 (Port Call 從船期移除):    relevance_level=NONE      operational_notification_state=EXPOSURE_CLEARED

Event Phase 3 notification_state across all 3 runs: UNCHANGED → UNCHANGED → UNCHANGED  (unchanged throughout)
Operational relevance_level across all 3 runs:        MODERATE → HIGH → NONE
Operational notification_state across all 3 runs:     EXPOSURE_NEW → EXPOSURE_ESCALATED → EXPOSURE_CLEARED
```

Run 3 的 CLEARED 判定完全基於「船期資料中已無此 Port Call」這個明確事實，並非任何憑空推測的改道判斷（符合規範要求：Cleared 判定必須基於明確船期資料，不可用猜測的改道推論）。完整輸出見 `phase6_simulation.py` 執行結果（腳本內含斷言，執行完成即代表全部驗證通過）。

---

## 下一步

Phase 6 到此為止。依指示，**不**開始 Phase 7（Teams/Dashboard/Port Weather Integration/Internal API deployment），等待人工確認後再繼續。
