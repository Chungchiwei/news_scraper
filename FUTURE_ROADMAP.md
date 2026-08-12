# Future Roadmap

本文件只列出**尚未實作**、經確認有實際營運需求時才會評估的未來項目。Phase 8 完成後，本專案進入 **Feature Freeze**（見 `PHASE_8_FINAL_COMPLETION_REPORT.md`）——以下項目在 v1.1/v2.0 才會重新評估，本輪不實作。

- **Weather Integration** — 天氣資料整合，用於評估航線天氣風險。
- **AIS Integration** — 即時船位資料整合，取代目前依賴新聞文字判斷船舶位置。
- **Earthquake / Tsunami Alerts** — 地震／海嘯示警整合。
- **Internal Live Schedule API** — 串接公司內部即時船期系統，取代目前的 `config/schedules_config.json` 靜態設定檔。
- **SSO（單一登入）** — Dashboard 目前只有簡易 Basic Auth，未串接企業 SSO。
- **Production Hosting / 雲端部署** — 目前是單機批次程式，尚未評估雲端架構、容器化、高可用性部署。

## 判斷原則

上述項目只有在**確認實際營運需求**後才評估是否進入開發，不會因為「技術上做得到」就自動排入計畫。日常維運工作僅限於：Bug Fix、Source Maintenance（新聞來源維護）、Configuration Update、Production Validation。
