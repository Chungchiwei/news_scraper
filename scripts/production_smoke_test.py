#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/production_smoke_test.py
WHL Maritime Intelligence System — Phase 8 §三十八 Production Smoke Test

★ 這個腳本「不」會在一般驗收流程中自動執行。它會連真實網路（1-2 個公開
  新聞來源，確認連線本身沒問題）並確認 Event Database 檔案可寫入，
  純粹是給人工在部署後想確認「這台機器/這個網路環境到底能不能連上外面」
  時手動跑的工具。

不做的事（刻意，見 §三十八）：
  - 不會寄送真實 Email。
  - 不會送出真實 Teams 訊息。
  - 不會呼叫真實 LLM API。
  - 不是 Final Acceptance 的一部分——scripts/final_acceptance_test.py
    才是正式驗收標準（完全離線、deterministic）。網路/來源本身不穩定，
    不能拿來當自動驗收依據。

用法（手動執行）：
    python scripts/production_smoke_test.py
Exit code：0 = 全部檢查通過，1 = 至少一項失敗。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 兩個公開、穩定、輕量的來源，只用來確認「這台機器能不能連上外面的
# HTTPS 網站」，不代表 RSS 來源本身的健康狀態（那是 source_health.py /
# scripts/health_check.py 的職責）。
CHECK_URLS = [
    "https://www.reuters.com/",
    "https://www.tradewindsnews.com/",
]


def check_network() -> bool:
    import requests
    ok_count = 0
    for url in CHECK_URLS:
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            status = "OK" if resp.status_code < 500 else f"HTTP {resp.status_code}"
            print(f"  {url:<45}{status}")
            if resp.status_code < 500:
                ok_count += 1
        except Exception as e:
            print(f"  {url:<45}FAILED ({type(e).__name__}: {e})")
    return ok_count > 0   # 至少一個來源連得上，就算網路本身沒問題


def check_db_writable() -> bool:
    sqlite_ok = False
    try:
        import sqlite3
        with tempfile.TemporaryDirectory(prefix="smoke_") as d:
            path = os.path.join(d, "smoke_test.db")
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
            conn.close()
        print("  SQLite write/read in temp dir: OK")
        sqlite_ok = True
    except Exception as e:
        print(f"  SQLite write/read failed: {type(e).__name__}: {e}")

    # 額外確認 production data/ 目錄本身可寫入（不寫入真實 DB 檔案，只
    # 確認目錄權限）。
    data_dir_ok = False
    try:
        data_dir = ROOT / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".smoke_test_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print("  data/ directory writable: OK")
        data_dir_ok = True
    except Exception as e:
        print(f"  data/ directory not writable: {type(e).__name__}: {e}")

    return sqlite_ok and data_dir_ok


def main() -> int:
    print("WHL Maritime Intelligence")
    print("Production Smoke Test (manual, not part of automated acceptance)")
    print()

    print("Network connectivity (public sources):")
    net_ok = check_network()
    print()

    print("Database writability:")
    db_ok = check_db_writable()
    print()

    overall = net_ok and db_ok
    print("OVERALL:", "PASS" if overall else "FAIL")
    if not net_ok:
        print("  - Network check failed: confirm outbound HTTPS (443) is allowed "
              "(see IT_DEPLOYMENT_GUIDE.md).")
    if not db_ok:
        print("  - Database writability check failed: confirm the process has write "
              "permission to the data/ directory.")

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
