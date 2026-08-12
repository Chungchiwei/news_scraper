@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set /p APP_VERSION=<VERSION
title WHL Maritime Intelligence — Dashboard

echo ========================================
echo   WHL Maritime Intelligence System
echo   Version %APP_VERSION%
echo   Management Dashboard
echo ========================================
echo.

if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found.
    echo Please run setup.bat first, then run this again.
    pause
    exit /b 1
)
call venv\Scripts\activate.bat

if not exist ".env" (
    echo [WARNING] .env not found — Dashboard will still start, but some
    echo           data ^(e.g. Delivery/Teams history^) may show as unavailable.
)

REM ── 預設只 bind localhost，不對外公開（可用 .env 的 DASHBOARD_HOST/
REM    DASHBOARD_PORT 覆寫，但預設值本身刻意保守）──
if "%DASHBOARD_HOST%"=="" (set DASHBOARD_HOST=127.0.0.1)
if "%DASHBOARD_PORT%"=="" (set DASHBOARD_PORT=8000)

echo Starting Dashboard at http://%DASHBOARD_HOST%:%DASHBOARD_PORT%
echo Press Ctrl+C to stop.
echo.

python dashboard\app.py

echo.
echo Dashboard stopped.
pause
