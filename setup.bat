@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set /p APP_VERSION=<VERSION
title WHL Maritime Intelligence System — Setup

echo ========================================
echo   WHL Maritime Intelligence System
echo   Version %APP_VERSION%
echo   One-time Environment Setup
echo ========================================
echo.
echo This will:
echo   1. Create a Python virtual environment ^(venv\^)
echo   2. Install required packages ^(requirements.txt + requirements-dev.txt^)
echo   3. Create data\, logs\, backup\, output\ directories
echo   4. Copy .env.example to .env ^(if .env does not exist yet^)
echo.
echo It will NOT ask you to type any password or API key here.
echo You will edit .env yourself afterwards, in Notepad.
echo.
pause

REM ── 檢查 Python 是否安裝 ──
echo.
echo [1/4] Checking Python...
python --version
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python first ^(see PYTHON_VERSION.md^).
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

REM ── 建立虛擬環境 ──
echo.
echo [2/4] Setting up virtual environment...
if not exist "venv\" (
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Created venv\
) else (
    echo venv\ already exists, skipping creation.
)

call venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul

if exist "requirements.txt" (
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install requirements.txt.
        pause
        exit /b 1
    )
) else (
    echo [ERROR] requirements.txt not found in this folder.
    pause
    exit /b 1
)

if exist "requirements-dev.txt" (
    pip install -r requirements-dev.txt
    if errorlevel 1 (
        echo [WARNING] Failed to install requirements-dev.txt ^(only needed for running tests^).
    )
)

echo Package installation complete.

REM ── 建立必要目錄 ──
echo.
echo [3/4] Creating required directories...
if not exist "data\" mkdir data
if not exist "logs\" mkdir logs
if not exist "backup\" mkdir backup
if not exist "output\" mkdir output
echo Done.

REM ── 準備 .env（從 .env.example 複製，不自動填入任何機密值）──
echo.
echo [4/4] Preparing .env...
if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo Created .env from .env.example.
        echo.
        echo [IMPORTANT] Opening .env in Notepad now.
        echo Please fill in at least: MAIL_USER, MAIL_PASSWORD, TARGET_EMAIL
        echo Save and close Notepad when done.
        notepad .env
    ) else (
        echo [WARNING] .env.example not found — cannot create .env automatically.
        echo Please create .env manually. See CONFIGURATION_REFERENCE.md.
    )
) else (
    echo .env already exists, leaving it unchanged.
)

echo.
echo ========================================
echo   Setup complete.
echo   Next steps:
echo     1. Confirm .env is filled in correctly
echo     2. Run:  run.bat            ^(runs one intelligence cycle^)
echo     3. Run:  run_tests.bat      ^(runs the test suite^)
echo     4. Run:  run_dashboard.bat  ^(opens the Management Dashboard^)
echo ========================================
echo.
pause
