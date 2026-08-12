# Phase 2.1 — Intelligence Validation & Hardening 完成報告

範圍聲明：本階段只修改 clustering / confidence / carrier extraction / source independence / SSL 五個面向，未動 scraper 抓取邏輯、Email UI、LLM、DB、Persistent Memory、production entry point。

---

## 1. Clustering Hardening（Positive / Negative / Hard Reject）

`event_clusterer.py` 的 `pair_score()` 現在同時計算正向與負向訊號，並在 `cluster()` 層級（不只 pair 層級）套用 hard reject：

**Hard Reject（回傳 `-9999`，直接阻斷該候選 cluster）**
- 兩篇文章都有 `vessel_name` 且不同（不分大小寫比對）→ 立即 reject，不看其他訊號。

**強負向訊號**
- `different_vessel_type`：`-70`（container ship vs tanker）
- `different_incident_subtype`：`-35`（collision vs grounding 這類細分事故類型衝突）
- `different_event_type`：`-10`（只在 subtype 未一致時才加總，避免與 subtype 訊號重複扣分）

**正向訊號（節錄權重）**
- `same_vessel_name` +50、`same_incident_subtype` +30、`same_carrier` +15、`same_event_type` +15
- `same_location`(精確海域) +15、`related_region_group`(地理相關但非同海域，如 Red Sea/Gulf of Aden/Bab el-Mandeb 同屬 `RED_SEA_CORRIDOR`) +12、`same_major_sea_area` +10
- `title_similarity_high` +20、`summary_similarity_high`（新增）+15、`time_proximity_close_bonus`（6 小時內）+10

**Threshold**：維持既有 `50` 分門檻；`_is_hard_reject(score)` 獨立判斷 `-9999` sentinel。

**關鍵設計修正**：`cluster()` 迴圈原本只取「與 cluster 內任一成員的最高分」判斷是否加入，這會讓一篇文章透過與 A 的高分而加入，即使它與同 cluster 裡的 B 有 vessel_name 衝突。修正後：先算出該文章與 cluster 內**每一個**現有成員的 pair_score，只要任一個是 hard reject，整個候選 cluster 就直接淘汰，才取 max 判斷門檻。

驗證了 CLAUDE.md 給定的三個困難情境（詳見第 9 節測試結果）。

---

## 2. Missing Metadata Handling（無 carrier / vessel_name 時如何聚類）

CASE 2.1-A 要求：3 篇紅海事件報導，只有 1 篇提到 carrier/vessel，另外 2 篇完全沒有。若只靠 vessel_name/carrier 訊號會被迫拆成 2-3 個事件。

解法：新增 `incident_subtype`（細分事故類型，如 `VESSEL_ATTACK`/`EXPLOSION`/`GROUNDING`）作為與 vessel_name 同等重要的主訊號，搭配 `region_group`（地理相關性，非精確同海域也算相關）與 `time_proximity_close_bonus`（40 分鐘內視為同時間窗）、`summary_similarity_high`。三篇文章即使 metadata 稀疏，仍能靠「同 incident_subtype + 同 region_group + 時間相近 + 摘要相似」的組合分數穿過 50 分門檻聚成 1 個事件。

`event_clusterer.py` 內部用 `used_missing_carrier_path` 旗標紀錄「這次聚類是在雙方都缺 carrier 的情況下成立的」，計入 `diagnostics["missing_carrier_matches"]`，方便日後 debug 追蹤（不影響決策，只是可觀察性）。

過程中修正一個真實 bug：文章用「explosion」描述而非「attack」時會被分類進不同的 `incident_subtype`（`EXPLOSION` vs `VESSEL_ATTACK`），造成本應同一事件的三篇報導被拆散。修法是在 `VESSEL_ATTACK` 關鍵字桶內納入 "explosion"/"爆炸" 等詞（與 `EXPLOSION` 桶刻意重疊），並用 `incident_subtype_priority_order` 讓 `VESSEL_ATTACK` 優先勝出——因為即時攻擊回報常常在「attack」與「explosion」措辭間搖擺，實際上是同一起事件。這是演算法修正，不是 fixture 修正。

---

## 3. Priority 與 Confidence/Information Status 的獨立性

三個欄位完全獨立計算、互不覆寫：

| 欄位 | 代表什麼 | 誰決定 |
|---|---|---|
| `management_priority` (P1-P4) | 主管有多需要知道（急迫性/艦隊關聯性） | `RiskScorer.determine_priority()`：management_score 門檻 + Critical Override |
| `confidence_level` (HIGH/MEDIUM/LOW) | 這個分數/描述本身有多可信 | `RiskScorer.confidence_level()`：依 `independent_source_tiers` 計算 |
| `information_status` (CONFIRMED/CORROBORATED/UNCONFIRMED/EARLY_SIGNAL) | 這件事本身有沒有被證實 | `RiskScorer.determine_information_status()`：同樣依 `independent_source_tiers`，但是四階而非三階 |

`models.py` 的 `MaritimeEvent.information_status` 欄位文件明確記載：P1 + LOW + EARLY_SIGNAL 是合法組合（`test_priority_confidence_independent` 直接在 dataclass 層級證明修改 priority 不會連動 confidence/status）。

---

## 4. Critical Override 規則

不是原本擔心的「`if carrier=="Wan Hai" and event_type=="Fire": P1`」這種寫死規則。實際邏輯（`risk_scorer.py::_check_critical_override`）：

1. 先過 `min_score_to_consider`（55 分）門檻——分數太低的事件不觸發 override 判斷。
2. **own_fleet 路徑**：`is_own_fleet=True` 且 `severity_score >= own_fleet_min_severity(15)` → 觸發 P1。也就是「自家艦隊 + 有一定嚴重度的事故」才會被拉到 P1，而非任何提及 Wan Hai 的字眼。
3. **event_type_triggers 路徑**：特定 event_type（如重大傷亡、攻擊、封鎖）在文字中出現對應觸發詞時，也可獨立觸發，不需要是自家船隊。

Override 只影響 `management_priority`（透過 `determine_priority()` 回傳 `(P1, True)`），**完全不碰** `confidence_level` 或 `information_status`——這兩者仍然只看 `independent_source_tiers`。所以「Reddit 上未證實的萬海火災傳聞」可以合法地是 P1（因為艦隊關聯性把它拉高）同時是 LOW confidence + EARLY_SIGNAL（因為只有 Tier D 來源）。`test_wanhai_reddit_critical_override_low_confidence` 端到端驗證了這個組合。

---

## 5. Carrier Alias 稽核

逐一稽核 12 家主要航商別名清單（`risk_rules.json::major_carriers`），標記歧義風險：

| Carrier | 別名 | 歧義風險 | 處理方式 |
|---|---|---|---|
| **ONE** | `ocean network express` | **高**（裸字 "one" 是最常見英文詞之一） | 移除裸字別名，改用 `special_matching: ONE_CARRIER_RULE`：只有「完整名稱 Ocean Network Express」「大小寫完全相符的全大寫 ONE」「one 緊鄰 shipping context word（line/container/vessel/shipping/news）」三種情況才判定命中。裸字小寫 "one" 一律拒絕。 |
| **EVERGREEN** | `evergreen marine`/`evergreen line`/長榮/长荣 | 中（"evergreen" 單獨可指基金/策略/常青樹） | 移除裸字 "evergreen"，只留無歧義片語與中文別名。 |
| **PIL** | `pil`/太平船務/太平船务 | 中（naive substring 會誤中 "pilot"） | 改用 CJK/Latin 分流的 word-boundary regex（Latin 用 `\bpil\b` IGNORECASE），修正了稽核過程中發現的真實 bug。 |
| WAN_HAI | `wan hai`/萬海/万海/**whl** | 低（`whl` 3 字母縮寫不是常見英文詞，但仍建議留意未來若出現同名品牌） | 目前維持，word-boundary 已足夠防護 |
| MSC / MAERSK / CMA_CGM / COSCO / YANG_MING / HAPAG_LLOYD / HMM / OOCL | 各自別名 | 低（皆非常見英文單字或已是無歧義片語/專有名詞） | 維持現況，同樣受惠於 word-boundary regex |

CJK 別名處理上有一個關鍵技術發現：Python regex 的 `\b` 對 CJK 字元完全失效（因為所有 CJK 字元在 `\w` 判定下都是「單字字元」，中文連續文字裡任兩個字之間沒有 `\b` 邊界）。所以 `_compile_alias_pattern()` 對 CJK 別名使用純 substring regex，只對 Latin 別名套用 `\b...\b` IGNORECASE word-boundary regex，兩者分流處理。

---

## 6. Source Independence（article_count vs source_count vs independent_source_count）

新增 `source_provenance.py::SourceProvenanceResolver`，三層區分：

- **`article_count`**：原始文章篇數（可能同一事件 5 篇文章）。
- **`source_count`**：`source_name` 去重後的數量（例如 Reuters/Yahoo/MSN 算 3 個 source_name，即使 Yahoo、MSN 其實都是轉載 Reuters）。
- **`independent_source_count`**：偵測「轉載關係」後收斂的**真正獨立**消息來源數。用 `risk_rules.json::source_family_patterns` 裡的 deterministic 片語比對（如 "according to reuters"/"reuters reported"/"via reuters"/"reported by reuters"），把明顯轉載 Reuters 的文章歸入 `REUTERS` family，即使 `source_name` 顯示的是 Yahoo/MSN。目前涵蓋 REUTERS/AP/AFP/BLOOMBERG/XINHUA 五個 family。

`RiskScorer.confidence_level()` / `determine_information_status()` 改用 `event.independent_source_tiers`（而非 `event.source_tiers`）計算，這是本階段修正的一個關鍵 bug——原本 confidence 是用未去重的 raw article 篇數計算，會讓「3 篇轉載自同一家 Reuters 的文章」被誤判成 HIGH confidence（3 個來源互相佐證），實際上只是同一份原始情報被複製 3 次。

---

## 7. Confidence Logic

Source Tier 定義（沿用 Phase 2）：
- **Tier A**：IMO / 政府機構 / Coast Guard / Navy / Port Authority / UKMTO / IMB / 航商官方公告
- **Tier B**：Reuters / Lloyd's List / TradeWinds / Maritime Executive / gCaptain / Safety4Sea
- **Tier C**：一般新聞媒體、產業網站
- **Tier D**：Reddit / 社群 / forum / 未驗證內容

Confidence 規則（`confidence_level_rules`，改用 `independent_source_tiers`）：
- **HIGH**：至少 1 個 Tier A 獨立來源，或 2 個以上獨立 Tier B 來源互相佐證
- **MEDIUM**：1 個獨立 Tier B 來源，或 2 個以上獨立 Tier C 來源
- **LOW**：只有 Tier D，或沒有任何交叉驗證

`information_status` 用同一組 `independent_source_tiers` 但輸出四階（CONFIRMED/CORROBORATED/UNCONFIRMED/EARLY_SIGNAL），語意上是「這件事本身被證實的程度」，與 confidence（分數可信度）分開存放但共用同一份底層資料，避免兩套邏輯彼此矛盾。

---

## 8. TLS/SSL 變更

- 新增模組層級開關：`maritime_news.py::SSL_VERIFY = os.getenv("SSL_VERIFY", "true")`，未設定時預設 `True`（開啟驗證），設為 `false/0/no/""` 之外的任何值都視為開啟。關閉時會印出 `logger.warning`。
- `fetch_from_source()` 支援 per-source 覆寫：`source.get("verify_ssl", SSL_VERIFY)`，只有來源設定明確寫 `"verify_ssl": false` 才會關閉該來源的驗證，且會記一筆 `WARNING` log（不會靜默關閉）。
- 所有其他 scraper class（`RedditShippingScraper`／`OneShippingScraper`／`LloydsListScraper`／`Amz123Scraper`／`XindeScraper`／CNYES fetch）的 `verify=False` 已全部改為 `verify=SSL_VERIFY`（吃全域開關，暫不支援 per-source override——這些 class 目前沒有 per-source config dict，屬於刻意的範圍限制，非遺漏）。
- 支援 `REQUESTS_CA_BUNDLE` 環境變數（requests 套件原生支援，未額外包裝，企業內部 CA 憑證可透過此變數指定）。
- **目前仍需要 SSL override 的來源**：無。稽核後沒有發現任何來源設定檔裡有 `"verify_ssl": false`；所有來源預設走 `SSL_VERIFY=true`。若未來遇到特定來源憑證異常，可在該來源設定加 `"verify_ssl": false` 並會自動記錄 WARNING，不需要改動全域開關。

---

## 9. Tests

**Total: 45　Passed: 45　Failed: 0**（含原 Phase 2 的 26 項 + 本階段新增 19 項，pytest 統計實際列為 45 項，詳見下方明細）

原始 26 項（`test_carrier_filter.py` / `test_clustering.py` / `test_extraction.py` / `test_pipeline.py` / `test_risk_scoring.py`）**全部未修改，全部維持通過**——確認沒有為了配合新演算法而弱化既有測試。

本階段新增 19 項（含 13 項指定 + 6 項額外回歸測試），8 項代表性結果如下：

| # | 測試 | 情境 | 結果 |
|---|---|---|---|
| 1 | `test_cluster_same_event_without_carrier` | CASE 2.1-A：3 篇紅海事件報導，僅 1 篇有 carrier/vessel_name | **PASS** — 正確聚成 1 個 Event，`article_count == 3` |
| 2 | `test_cluster_same_area_different_vessel` | CASE 2.1-B：Container ship vs Tanker，皆稱「Red Sea attacked」 | **PASS** — 正確拆成 2 個 Event（vessel_type 衝突） |
| 3 | `test_cluster_same_carrier_different_event` | CASE 2.1-C：同為 MSC + 新加坡，一篇 collision 一篇 grounding | **PASS** — 正確拆成 2 個 Event（incident_subtype 衝突） |
| 4 | `test_cluster_different_vessel_hard_reject` | MSC ORION vs MSC AURORA，其餘欄位相同 | **PASS** — `pair_score` 觸發 `-9999` hard reject，拆成 2 個 Event |
| 5 | `test_wanhai_reddit_critical_override_low_confidence` | Reddit 傳聞：萬海船疑似火災 | **PASS** — `P1` + `LOW` + `EARLY_SIGNAL` 同時成立 |
| 6 | `test_one_false_positive` | "One crew member was injured..." | **PASS** — `extract_carrier() is None`，不誤判 ONE |
| 7 | `test_one_valid_uppercase` / `test_ocean_network_express_alias` | "ONE launches..." / "Ocean Network Express announced..." | **PASS** — 正確判定為 `ONE` |
| 8 | `test_independent_source_count_reuters_reposts` | Reuters 原稿 + 2 篇轉載（"according to Reuters"/"Reuters reported"） | **PASS** — `article_count==3`，`independent_source_count==1`，confidence 未被推高 |

額外驗證：`test_independent_source_reuters_tradewinds`（Reuters + TradeWinds 各自獨立報導 → `independent_source_count==2`，confidence==HIGH）、`test_pil_not_matched_inside_pilot`（PIL 不誤中 pilot）、`test_evergreen_bare_word_not_matched`、`test_priority_confidence_independent`（dataclass 層級解耦驗證）、`test_ssl_default_verify_enabled`、`test_ssl_per_source_override` 均通過。

**Regression 確認**：`python -m py_compile` 對所有 `.py` 檔案（含所有既有與新增檔案）零錯誤；整個 `tests/` 目錄零 live 網路依賴（無 `requests.get/post`、無 `smtplib`、無 `praw`、無指向真實 URL 的 `feedparser.parse` 呼叫，全部使用 `tests/fixtures/articles.json` 與 `make_article` fixture 建構的假資料，或用 `unittest.mock.patch.object` 攔截）。

---

## 10. Fixture Change Log

**沒有任何既有 fixture 或既有測試檔案被修改。**

`tests/fixtures/articles.json` 與 Phase 2 的 5 個測試檔案（`test_carrier_filter.py`/`test_clustering.py`/`test_extraction.py`/`test_pipeline.py`/`test_risk_scoring.py`）維持原樣，全程用「重跑整個 26 項舊測試」驗證沒有被破壞。

過程中有 2 次演算法/設定檔調整（**不是** fixture 調整，特此區分）：

1. **`risk_rules.json::incident_subtype_keywords.VESSEL_ATTACK`** 新增 "explosion"/"爆炸" 等關鍵字。原因：CASE 2.1-A 的第三篇文章用「explosion」描述而非「attack」，導致與另外兩篇被分類進不同 incident_subtype 而無法聚類，但這三篇報導的其實是同一起真實事件——即時攻擊回報常在「attack」與「explosion」措辭間搖擺。這是修正分類字典的覆蓋範圍，不是修改測試資料去配合演算法。
2. **`risk_rules.json::clustering.weights.different_vessel_type`** 從 `-40` 調整為 `-70`。原因：CASE 2.1-B 情境下，`title_similarity_high`(+20) + `summary_similarity_high`(+15) 等正向訊號加總後，-40 的懲罰不足以讓「container ship vs tanker」這種明確不同船型的案例落到 50 分門檻以下。調高懲罰後重新驗證了 CASE 2.1-A（不受影響，因為該案例沒有 vessel_type 衝突）與全部 26 項舊測試（仍全數通過）。

兩者皆屬「調整規則設定檔以正確處理真實世界的措辭變異」，而非「弱化測試去遷就演算法」，符合本階段「不可修改既有成功測試」的要求。

---

## 結論

6 項 Success Criteria（A-F，對應 CASE 2.1-A/B/C、Priority/Confidence 解耦、ONE 誤判、Source Independence）均已用實際 pytest 測試證明成立，且未犧牲任何 Phase 1/2 既有能力。

**本階段到此為止，等待確認後再開始 Phase 3（Persistent Event Memory）。**
