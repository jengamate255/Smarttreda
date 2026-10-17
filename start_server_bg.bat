@echo off
cd /d "E:\Project\Mt5 Ea"
python -X utf8 -u web_dashboard.py --port 8055 --terminal "C:\Program Files\MetaTrader 5 Terminal\terminal64.exe" --login 11911448 > server.log 2>&1