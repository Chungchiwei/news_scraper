# Data Flow

概念上的一次完整執行（`run.bat` → `python maritime_news.py`），Input/Output 摘要如下。詳細模組請見 `SYSTEM_ARCHITECTURE.md`。

```
[1] RSS/HTML 來源
     Input:  來源設定（RSS_SOURCES/CNYES_SOURCES，含 URL/Tier/語言）
     Output: 原始文章 dict 列表（news_data['all']）
     │
     ▼
[2] Article 正規化
     Input:  原始文章 dict
     Output: NewsArticle 物件列表
     │
     ▼
[3] Event Extraction / Classification
     Input:  NewsArticle
     Output: 附加 event_type / incident_subtype / vessel_name / location /
             carrier 等結構化欄位的 NewsArticle
     │
     ▼
[4] Clustering
     Input:  已分類的 NewsArticle 列表
     Output: MaritimeEvent 列表（多篇報導同一事件會合併成一個，
             article_count / independent_source_count 反映來源數）
     │
     ▼
[5] Risk Scoring
     Input:  MaritimeEvent（未評分）
     Output: MaritimeEvent（含 management_priority / management_score /
             confidence_level / information_status）
     │
     ▼
[6] Persistent Memory（跨執行比對）
     Input:  本次 MaritimeEvent 列表 + Event Store（data/maritime_intelligence.db）
             裡的既有事件
     Output: MaritimeEvent（含 event_id 穩定不變、notification_state =
             NEW/MATERIAL_UPDATE/MINOR_UPDATE/UNCHANGED/RESOLVED_UPDATE）
     │
     ▼
[7] Briefing Selection
     Input:  MaritimeEvent 列表（含 notification_state）
     Output: {immediate, watch, industry, resolved, suppressed} 分桶
     │
     ▼
[8] LLM Enhancement（Optional）
     Input:  Briefing Selection 選出的事件
     Output: {event_id: IntelligenceAnalysis}（LLM_ENABLED=false 時為空 dict，
             Renderer 自動 fallback 回 Rule-Based Summary）
     │
     ▼
[9] Operational Relevance（獨立軸）
     Input:  候選事件列表 + Fleet/Schedule/Route Provider 資料
     Output: {event_id: OperationalRelevance}（relevance_level:
             NONE/LOW/MODERATE/HIGH/DIRECT）+ 曝險歷史快照
             （data/operational_relevance.db）
     │
     ▼
[10] Delivery Orchestration（Dual-Axis Trigger）
     Input:  event.notification_state（軸一）+ operational_notification_state
             （軸二：EXPOSURE_NEW/ESCALATED/UNCHANGED/CLEARED）
     Output: DeliveryDecision（urgency / channels / email_mode / teams_mode /
             dashboard_visibility），寫入送達歷史（data/delivery_history.db）
     │
     ├──▶ [11a] Email Renderer → SMTP 送出（email_sender.py）
     ├──▶ [11b] Teams Renderer → Webhook 送出（teams_notifier.py）
     └──▶ [11c] Dashboard 資料（唯讀，Dashboard 下次開啟時直接查詢
                上述所有 SQLite，不需要額外的推送步驟）
```

## 關鍵不變量（Invariants）

1. **event_id 一旦建立就不會改變**（即使 headline/severity 隨後更新），這是 Dashboard 能顯示「同一事件的時間軸」的前提。
2. **notification_state（Event Axis）與 operational_notification_state（Operational Axis）是兩條獨立時間軸**——Event UNCHANGED 不代表 Operational Axis 也不變，反之亦然。
3. **Dashboard 永遠可見**（`dashboard_visibility=True`），與是否推送 Email/Teams 完全分離——不推送不代表看不到。
4. **Email 本身是 Phase 4 既有的單一 Daily Brief/Alert 機制**；Delivery Orchestrator 不改寫 Email 的產生方式，只額外決定「這次 run 要不要送、送給哪個管道」，並事後把結果記錄進 Delivery History。

## Optional 子系統的失敗如何影響流程

見 `OPERATIONS_RUNBOOK.md` 與 `docs/history/PHASE_8_FINAL_COMPLETION_REPORT.md` 的 Graceful Degradation Table——簡而言之：LLM/Teams/Operational Provider 失敗只影響對應區塊的顯示內容，絕不會讓 Email 發不出去；只有 Event Database 打不開、或 SMTP 重試後仍失敗，才會讓整次執行以非零 exit code 結束。
