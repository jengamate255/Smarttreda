@echo off
REM Micro account watcher launcher — $200 Deriv real account
REM Symbols: BOOM1000, CRASH1000, CRASH500
REM Risk: 1%% per trade, 3%% daily halt, max 2 positions per symbol
REM
REM Set MT5_MCP_ACCOUNT=DERIV_REAL and ensure deriv_real_mt5 is configured in settings.json

set MT5_MCP_ACCOUNT=DERIV_REAL
cd /d "E:\Project\Mt5 Ea"
start /B "" "C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe" -X utf8 "E:\Project\Mt5 Ea\trade_watch.py" --micro --live --interval 15 --symbols BOOM1000,CRASH1000,CRASH500 --log-file "E:\Project\Mt5 Ea\logs\micro_watch.log"
