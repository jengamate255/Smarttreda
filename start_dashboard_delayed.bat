@echo off
timeout /t 2 /nobreak > nul
cd /d "E:\Project\Mt5 Ea"
"C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" run_dashboard.py
