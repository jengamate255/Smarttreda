@echo off
cd /d "E:\Project\Mt5 Ea"
echo Killing old watcher processes...
taskkill /F /FI "WINDOWTITLE eq *trade_watch*" 2>nul
taskkill /F /IM python.exe /FI "MEMUSAGE gt 50000" 2>nul
timeout /t 2 /nobreak >nul
echo Starting watchdog...
start "watchdog" /B "C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" service_watchdog.py --interval 30
echo Done.
