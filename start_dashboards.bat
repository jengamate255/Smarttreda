@echo off
cd /d E:\Project\Mt5 Ea

echo Starting Prop dashboard on port 8055...
start "Prop Dashboard" python -X utf8 web_dashboard.py --port 8055 --terminal "C:\Program Files\MetaTrader 5 Terminal\terminal64.exe" --login 11911448

echo Starting Deriv dashboard on port 8052...
start "Deriv Dashboard" python -X utf8 web_dashboard.py --port 8052 --terminal "C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe" --login 21727841 --password "REDACTED_MT5_PASSWORD$" --server "DerivVU-Server"

echo Both dashboards started. Check logs in E:\Project\Mt5 Ea\logs\
timeout /t 15

echo Testing Prop dashboard...
curl -H "X-Auth-Token: REDACTED_DASHBOARD_TOKEN" http://127.0.0.1:8055/api/ping

echo Testing Deriv dashboard...
curl -H "X-Auth-Token: REDACTED_DASHBOARD_TOKEN" http://127.0.0.1:8052/api/ping

pause