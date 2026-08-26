@echo off
cd /d E:\Project\Mt5 Ea
echo.
echo Starting MT5 Web Dashboard Server...
echo.
echo If this is the first run, MetaTrader5 will be initialized...
echo.
timeout /t 3 /nobreak >nul
python web_dashboard.py
echo.
echo.
echo Dashboard should now be running at:
echo http://127.0.0.1:8051
echo.
echo Press Ctrl+C to stop the server...
timeout /t 3600 /nobreak >nul