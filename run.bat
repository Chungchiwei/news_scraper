@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set /p APP_VERSION=<VERSION
title WHL Maritime Intelligence System

echo ========================================
echo   WHL Maritime Intelligence System
echo   Version %APP_VERSION%
echo   Starting...
echo ========================================
echo.

REM ── 檢查 Python 是否安裝 ──
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Please install Python 3.11 (see PYTHON_VERSION.md), then run setup.bat first.
    pause
    exit /b 1
)

REM ── 檢查虛擬環境是否已建立（由 setup.bat 負責建立，run.bat 不重複做）──
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found.
    echo Please run setup.bat first ^(one-time setup^), then run this again.
    pause
    exit /b 1
)
call venv\Scripts\activate.bat

REM ── 檢查 .env 是否已設定（由 setup.bat 從 .env.example 建立）──
if not exist ".env" (
    echo [ERROR] .env not found.
    echo Please run setup.bat first, then edit .env with your Email settings.
    pause
    exit /b 1
)

REM ── 檢查主程式 / 必要設定檔是否存在 ──
if not exist "maritime_news.py" (
    echo [ERROR] maritime_news.py not found in this folder.
    pause
    exit /b 1
)
if not exist "keywords_config.json" (
    echo [ERROR] keywords_config.json not found ^(required by maritime_news.py^).
    pause
    exit /b 1
)

REM ── 建立必要目錄（若不存在）──
if not exist "data\" mkdir data
if not exist "logs\" mkdir logs

REM ── 執行主程式（單次執行一個情報循環，執行完畢自動結束）──
echo.
python maritime_news.py
set RUN_RESULT=%errorlevel%

echo.
echo ========================================
if %RUN_RESULT%==0 (
    echo   Completed successfully.
) else (
    echo   Execution failed. See log for details.
    echo   Log file: logs\maritime_intelligence.log
    echo   For troubleshooting, see OPERATIONS_RUNBOOK.md
    echo   or run: python scripts\health_check.py
)
echo ========================================
echo.
echo Press any key to close this window...
pause >nul

exit /b %RUN_RESULT%
