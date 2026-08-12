#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
version.py
Phase 8 — 系統版本 Single Source of Truth。

所有需要顯示版本號的地方（maritime_news.py CLI 啟動橫幅、
scripts/health_check.py、dashboard/app.py、README 產生器等）都應該
從這裡讀取，不應該各自硬編碼版本字串——避免版本號分散在多個檔案裡
最後互相不一致。

版本號優先讀取專案根目錄的 VERSION 檔案（單一文字檔，內容例如
"1.0.0-rc1"），若該檔案不存在（例如被打包成單一執行檔時 VERSION
未一併附上），則退回本檔案內的 _FALLBACK_VERSION 常數。
"""

from pathlib import Path

_FALLBACK_VERSION = "1.0.0-rc1"

SYSTEM_NAME = "WHL Maritime Intelligence System"


def _read_version_file() -> str:
    version_path = Path(__file__).resolve().parent / "VERSION"
    try:
        content = version_path.read_text(encoding="utf-8").strip()
        return content or _FALLBACK_VERSION
    except OSError:
        return _FALLBACK_VERSION


__version__ = _read_version_file()


def version_banner() -> str:
    """回傳單行版本橫幅，例如："WHL Maritime Intelligence System / Version 1.0.0-rc1" """
    return f"{SYSTEM_NAME} / Version {__version__}"


if __name__ == "__main__":
    print(version_banner())
