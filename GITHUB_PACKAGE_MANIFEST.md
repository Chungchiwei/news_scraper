# GitHub Package Manifest — v1.0.0-rc1

由 `scripts/build_github_package.py` 產生（每次執行都重新計算，本文件
反映最近一次 build 的結果：152 個檔案，Fail-Closed Secret Scan PASS）。

## Entry Point

```
maritime_news.py          # 唯一 production entry point（python maritime_news.py）
dashboard/app.py          # Dashboard entry point（本機/內部主機用，非 GitHub Actions 執行）
```

## Python Version

**3.11.9**（見 `PYTHON_VERSION.md`、`.github/workflows/*.yml` 的
`setup-python` 明確版本 pin，不使用浮動版本號）。3.10.x 亦通過完整測試。

## Included Files（INCLUDE，見 GITHUB_PACKAGING_AUDIT.md §A-C 詳細分類）

- 55 個根目錄 production `.py` 模組（含 `version.py`；不含 3 個本機
  offline simulation 工具 phase3/6/7_simulation.py 與 2 個本機 preview
  工具 preview_email.py/preview_teams.py 之外的其餘 49 個構成真正的
  Dependency Closure，另外 5 個為 OPTIONAL 開發工具一併保留）
- 7 個根目錄規則 JSON（risk/memory/delivery/email/llm/operational_rules.json
  + keywords_config.json）
- `config/`（fleet/schedules/services/ports_config.json — 皆為 Placeholder
  或公開參考資料，非真實內部資料，見 Audit §A）
- `dashboard/`（FastAPI + Jinja2 全部原始碼與樣板）
- `scripts/`（backup_data.py / health_check.py / final_acceptance_test.py /
  production_smoke_test.py / build_github_package.py /
  github_actions_smoke_test.py）
- `tests/`（21+ 測試檔 + fixtures，共 201 項測試）
- `prompts/`（LLM system prompt）
- `legacy/`（歷史程式碼，已在 `DEPRECATED.md` 標示 Do Not Use，掃描確認
  無硬編碼密碼）
- `.github/workflows/`（`ci.yml`、`maritime-intelligence.yml`）
- Windows 本機操作：`run.bat` / `run_dashboard.bat` / `run_tests.bat` /
  `setup.bat`
- 文件：README / SYSTEM_ARCHITECTURE / DATA_FLOW / CONFIGURATION_REFERENCE /
  OPERATIONS_RUNBOOK / IT_DEPLOYMENT_GUIDE / PYTHON_VERSION / DEPRECATED /
  FUTURE_ROADMAP / FINAL_ARCHITECTURE_AUDIT / FINAL_ACCEPTANCE_CHECKLIST /
  PHASE_8_FINAL_COMPLETION_REPORT / 本輪新增的 4 份 GitHub Packaging 文件 /
  claude.md / PHASE2-7 歷史報告
- `requirements.txt`、`requirements-dev.txt`、`VERSION`、`.env.example`、
  `.gitignore`
- `data/.gitkeep`（唯一放進 `data/` 的檔案）

## Excluded Files（EXCLUDE，Fail-Closed 驗證）

```
.env                          — 真實密碼（SECRET）
*.db  *.db-wal  *.db-shm  *.db-journal   — Runtime State（RUNTIME STATE）
news_scraper.log  *.log       — 含真實內部信箱等歷史紀錄（SECRET/GENERATED）
__pycache__/  *.pyc  .pytest_cache/      — GENERATED
logs/  backup/  output/  dist/           — GENERATED
venv/  .venv/                             — 本機虛擬環境
```

## Required GitHub Secrets（Repository → Settings → Secrets and variables → Actions → Secrets）

| Secret | 必填 |
|---|---|
| `MAIL_USER` | ✅ |
| `MAIL_PASSWORD` | ✅ |
| `TARGET_EMAIL` | ✅ |
| `TEAMS_MANAGEMENT_WEBHOOK_URL` | 僅 `TEAMS_ENABLED=true` 時 |
| `TEAMS_SYSTEM_WEBHOOK_URL` | 僅 `TEAMS_ENABLED=true` 時 |
| `ANTHROPIC_API_KEY` | 僅 `LLM_ENABLED=true` 且使用 Anthropic 時 |
| `OPENAI_API_KEY` | 僅 `LLM_ENABLED=true` 且使用 OpenAI 時 |

## Required GitHub Variables（同頁 Variables 分頁，皆有安全預設值，非必填）

`TEAMS_ENABLED` `LLM_ENABLED` `LLM_PROVIDER` `LLM_MODEL`
`LLM_ALLOW_INTERNAL_OPERATIONAL_DATA` `SEND_NO_RISK_BRIEF`
`MAIL_SMTP_SERVER` `MAIL_SMTP_PORT` `MAIL_SEND_MAX_ATTEMPTS`
`MAIL_SEND_RETRY_WAIT_SEC` `NEWS_HOURS_BACK` `SSL_VERIFY` `INTELLIGENCE_DEBUG`

完整說明見 `GITHUB_ACTIONS_SETUP.md` Step 4-5。

## Runtime State Files（不進 Repository，只由 workflow artifact 管理，見 `config/github_state_files.json`）

| 檔案 | 對應模組 | Required |
|---|---|---|
| `data/maritime_intelligence.db` | Event Store（Phase 3） | ✅ |
| `data/delivery_history.db` | Delivery/Dedup History（Phase 7） | ✅ |
| `data/ai_analysis.db` | LLM Cache（Phase 5） | Optional |
| `data/operational_relevance.db` | Operational Exposure History（Phase 6） | Optional |
| `data/source_health.db` | Source Health（Phase 7） | Optional |

## Workflow Files

```
.github/workflows/ci.yml                    — push/PR，離線 pytest + py_compile
.github/workflows/maritime-intelligence.yml  — workflow_dispatch + schedule，正式執行
```

Action 版本（實際查證，非猜測，見 GITHUB_ACTIONS_PACKAGING_REPORT.md §Action Version Audit）：
`actions/checkout@v7`、`actions/setup-python@v7`、`actions/upload-artifact@v7`、
`actions/download-artifact@v8`。
