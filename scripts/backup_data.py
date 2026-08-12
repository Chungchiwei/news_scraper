#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/backup_data.py
WHL Maritime Intelligence System — Phase 8 §十八〜二十 簡易資料庫備份工具

用途：把 data/ 目錄下的 5 個 SQLite 資料庫，安全地複製一份到
backup/YYYYMMDD_HHMM/。

★ 為什麼不能直接 shutil.copy()：
  資料庫可能正被 maritime_news.py / dashboard/app.py 開著（WAL/journal
  模式下寫入是分散在 .db + .db-wal/.db-journal 多個檔案），若程式正在
  寫入的當下用檔案複製，備份出來的檔案可能損毀或資料不一致。

  本工具改用 Python 標準庫 sqlite3 的 Online Backup API
  （Connection.backup()），這是 SQLite 官方建議的線上備份方式，讀取端
  會在一致的快照上逐頁複製，寫入端可以繼續運作，不會拿到損毀檔案。

用法：
    python scripts/backup_data.py
    python scripts/backup_data.py --retention 30
    python scripts/backup_data.py --dry-run

不做的事（刻意，見 Phase 8 §二十）：
  - 不做雲端備份（不上傳 S3／GCS／OneDrive 等）。
  - 不自動 restore——restore 是人工決策動作，步驟寫在
    OPERATIONS_RUNBOOK.md（停止程式 → 備份現況 → 還原指定版本 →
    health check → 重新啟動），不在本工具自動化，避免誤觸還原到
    錯誤時間點的資料。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_RETENTION = 14  # 保留最近 N 次備份（§十九：簡單、config-driven，非複雜備份服務）


def _resolve_db_targets() -> list[tuple[str, str]]:
    """
    回傳 [(名稱, 實際路徑), ...] —— 路徑解析邏輯與 maritime_news.py /
    dashboard/app.py 完全一致（env var 覆寫優先，否則用各模組的
    DEFAULT_*_DB_PATH），確保備份的是「程式實際在用」的那個檔案，
    不是憑空猜的路徑。
    """
    from memory_pipeline import DEFAULT_DB_PATH
    from ai_cache import DEFAULT_AI_CACHE_DB_PATH
    from operational_history import DEFAULT_OPERATIONAL_HISTORY_DB_PATH
    from delivery_history import DEFAULT_DELIVERY_HISTORY_DB_PATH
    from source_health import DEFAULT_SOURCE_HEALTH_DB_PATH

    return [
        ("maritime_intelligence", os.environ.get("MARITIME_DB_PATH", DEFAULT_DB_PATH)),
        ("ai_analysis", os.environ.get("MARITIME_AI_CACHE_DB_PATH", DEFAULT_AI_CACHE_DB_PATH)),
        ("operational_relevance", os.environ.get(
            "MARITIME_OPERATIONAL_HISTORY_DB_PATH", DEFAULT_OPERATIONAL_HISTORY_DB_PATH)),
        ("delivery_history", os.environ.get(
            "MARITIME_DELIVERY_HISTORY_DB_PATH", DEFAULT_DELIVERY_HISTORY_DB_PATH)),
        ("source_health", os.environ.get(
            "MARITIME_SOURCE_HEALTH_DB_PATH", DEFAULT_SOURCE_HEALTH_DB_PATH)),
    ]


def sqlite_online_backup(src_path: Path, dst_path: Path) -> None:
    """使用 SQLite Online Backup API 安全複製一份資料庫（見檔案頂部說明）。"""
    src_conn = sqlite3.connect(str(src_path))
    try:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        dst_conn = sqlite3.connect(str(dst_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def apply_retention(backup_root: Path, retention: int, dry_run: bool = False) -> list[str]:
    """
    只保留最近 `retention` 次備份資料夾（依資料夾名稱排序，命名格式
    YYYYMMDD_HHMM 本身就是可字典排序的時間戳），多的直接刪除整個資料夾。
    """
    if not backup_root.exists():
        return []
    run_dirs = sorted(
        [d for d in backup_root.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )
    to_remove = run_dirs[:-retention] if retention > 0 else []
    removed = []
    for d in to_remove:
        removed.append(d.name)
        if not dry_run:
            shutil.rmtree(d, ignore_errors=True)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="WHL Maritime Intelligence — Database Backup")
    parser.add_argument("--retention", type=int,
                         default=int(os.environ.get("BACKUP_RETENTION_COUNT", DEFAULT_RETENTION)),
                         help=f"保留最近 N 次備份（預設 {DEFAULT_RETENTION}，"
                              "亦可用環境變數 BACKUP_RETENTION_COUNT 設定）")
    parser.add_argument("--backup-dir", default=os.environ.get("BACKUP_DIR", "backup"),
                         help="備份根目錄（預設 backup/）")
    parser.add_argument("--dry-run", action="store_true",
                         help="只顯示會做什麼，不實際複製/刪除任何檔案")
    args = parser.parse_args()

    run_time = datetime.now(tz=timezone.utc)
    stamp = run_time.strftime("%Y%m%d_%H%M")
    backup_root = ROOT / args.backup_dir
    run_dir = backup_root / stamp

    print("WHL Maritime Intelligence")
    print("Database Backup")
    print(f"Timestamp (UTC): {run_time.isoformat()}")
    print(f"Target: {run_dir}" + (" (dry-run)" if args.dry_run else ""))
    print("-" * 50)

    targets = _resolve_db_targets()
    backed_up = 0
    skipped = 0

    for name, db_path_str in targets:
        db_path = Path(db_path_str)
        if not db_path.is_absolute():
            db_path = ROOT / db_path

        if not db_path.exists():
            print(f"  {name:<24} SKIP (not found: {db_path})")
            skipped += 1
            continue

        dst_path = run_dir / f"{name}.db"
        if args.dry_run:
            print(f"  {name:<24} WOULD BACKUP  {db_path} → {dst_path}")
        else:
            try:
                sqlite_online_backup(db_path, dst_path)
                size_kb = dst_path.stat().st_size / 1024
                print(f"  {name:<24} OK  ({size_kb:.1f} KB)")
                backed_up += 1
            except Exception as e:
                print(f"  {name:<24} FAILED ({e})")

    print("-" * 50)
    removed = apply_retention(backup_root, args.retention, dry_run=args.dry_run)
    if removed:
        verb = "WOULD REMOVE" if args.dry_run else "Removed"
        print(f"Retention (keep last {args.retention}): {verb} {len(removed)} old backup(s): "
              f"{', '.join(removed)}")
    else:
        print(f"Retention (keep last {args.retention}): nothing to remove")

    print("-" * 50)
    print(f"Databases backed up: {backed_up}   Skipped (not found): {skipped}")
    print("RESULT: SUCCESS" if not args.dry_run else "RESULT: DRY-RUN COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
