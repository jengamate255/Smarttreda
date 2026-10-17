@echo off
set MT5_MCP_ACCOUNT=DERIV
"C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" -X utf8 "E:\Project\Mt5 Ea\trade_watch.py" --live --interval 15 --confidence-floor 60 --max-spike-age 60 --lookback 60 --symbols BOOM1000,BOOM900,BOOM600,BOOM500,BOOM300,BOOM200,BOOM150,BOOM100,BOOM99,BOOM50,CRASH1000,CRASH900,CRASH600,CRASH500,CRASH300,CRASH200,CRASH150,CRASH100,CRASH99,CRASH50 --log-file "E:\Project\Mt5 Ea\logs\trade_watch.log"
