@echo off
cd /d "E:\Project\Mt5 Ea"
set PY=C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
"%PY%" -u -X utf8 -m mt5_mcp.backtest --symbols USDJPY --trend-tf H4 --zone-tfs H1,H4 --confirm-tf H1 --min-zone-score 40 --min-rr 3 --bars 5000 --sensitivity --jobs 1 --no-frame-cache --frame-ttl 0 --out "E:\Project\Mt5 Ea\mt5_mcp\backtest\results\usdjpy_sens" > "E:\Project\Mt5 Ea\logs\usdjpy_sens.out" 2> "E:\Project\Mt5 Ea\logs\usdjpy_sens.err"
echo SENS_EXIT=%ERRORLEVEL% >> "E:\Project\Mt5 Ea\logs\usdjpy_sens.err"
