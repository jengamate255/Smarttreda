@echo off
title Deriv AutoTrader
echo Starting Deriv AutoTrader...
echo.
echo Checking MT5 terminal...

:: Check if MT5 Deriv terminal is running
tasklist /FI "IMAGENAME eq terminal64.exe" 2>nul | findstr /I "terminal64.exe" >nul
if %errorlevel% neq 0 (
    echo MT5 terminal not running. Starting it...
    start "" "C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe"
    echo Waiting 30 seconds for MT5 to load and connect...
    timeout /t 30 /nobreak >nul
) else (
    echo MT5 terminal already running.
    echo Waiting 10 seconds for connection...
    timeout /t 10 /nobreak >nul
)

cd /d "E:\Project\Mt5 Ea"
set MT5_MCP_ACCOUNT=DERIV

:loop
echo.
echo ============================================================
echo Starting scan cycle at %date% %time%
echo ============================================================
.\.venv\Scripts\python.exe -X utf8 deriv_autotrade.py
echo.
echo Next scan in 5 minutes...
timeout /t 300 /nobreak >nul
goto loop
