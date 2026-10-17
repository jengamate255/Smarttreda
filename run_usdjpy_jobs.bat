@echo off
cd /d "E:\Project\Mt5 Ea"
set PY=C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
set LOGS=E:\Project\Mt5 Ea\logs

start "usdjpy_sens" /b cmd /c ""%PY%" -u -X utf8 -m mt5_mcp.backtest --symbols USDJPY --trend-tf H4 --zone-tfs H1,H4 --confirm-tf H1 --min-zone-score 40 --min-rr 3 --bars 5000 --sensitivity --jobs 1 --out "E:\Project\Mt5 Ea\mt5_mcp\backtest\results\usdjpy_sens" > "%LOGS%\usdjpy_sens.out" 2> "%LOGS%\usdjpy_sens.err""

start "usdjpy_wf" /b cmd /c ""%PY%" -u -X utf8 "E:\Project\Mt5 Ea\tmp_usdjpy_wf.py" > "%LOGS%\usdjpy_wf.out" 2> "%LOGS%\usdjpy_wf.err""

echo launched
