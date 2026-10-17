@echo off
title Trading Intelligence Platform
cd /d "E:\Project\Mt5 Ea"
call "C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\activate.bat"
python run_services.py
pause
