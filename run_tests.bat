@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set /p APP_VERSION=<VERSION
title WHL Maritime Intelligence — Tests

echo ========================================
echo   WHL Maritime Intelligence System
echo   Version %APP_VERSION%
echo   Running Test Suite
echo ========================================
echo.

if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found.
    echo Please run setup.bat first, then run this again.
    pause
    exit /b 1
)
call venv\Scripts\activate.bat

python -m pytest --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pytest not installed.
    echo Please run setup.bat again ^(it installs requirements-dev.txt^),
    echo or manually run: pip install -r requirements-dev.txt
    pause
    exit /b 1
)

python -m pytest -q
set TEST_RESULT=%errorlevel%

echo.
echo ========================================
if %TEST_RESULT%==0 (
    echo   ALL TESTS PASSED
) else (
    echo   SOME TESTS FAILED
    echo   See output above for details.
)
echo ========================================
echo.
pause

exit /b %TEST_RESULT%
