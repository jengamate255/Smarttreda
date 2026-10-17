@echo off
cd /d "E:\Project\Mt5 Ea"
echo Starting MT5 Gateway on port 8060...
start "MT5 Gateway" /MIN ".venv\Scripts\python.exe" -X utf8 mt5_gateway.py
timeout /t 3 /nobreak >nul
echo Starting Web Dashboard on port 8050...
start "MT5 Dashboard" /MIN ".venv\Scripts\python.exe" -X utf8 web_dashboard.py
echo Both servers started.
echo   Gateway: http://127.0.0.1:8060
echo   Dashboard: http://127.0.0.1:8050
