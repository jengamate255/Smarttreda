@echo off
setlocal
cd /d "E:\Project\Mt5 Ea"

rem Hard safety rails for the unattended runner.
set "PYTHONPATH=E:\Project\Mt5 Ea;E:\Project\Mt5 Ea\mt5_mcp;E:\Project\Mt5 Ea\libs"
set "MT5_AUTOTRADER_ENABLED=1"
set "MT5_AUTOTRADER_EXECUTE=1"
set "MT5_AUTOTRADER_MODE=SIM"
set "MT5_AUTOTRADER_EXPECTED_LOGIN=11911448"
set "MT5_MCP_MAGIC=235010"

"C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" "mt5_mcp\auto_trader.py" --once
exit /b %ERRORLEVEL%
