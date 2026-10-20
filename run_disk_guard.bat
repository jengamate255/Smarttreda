@echo off
REM Interpreter path updated 2026-10-21: the hermes venv this used was deleted with the
REM machine reset. Task "MT5R_DiskGuard" runs C:\Users\Dave\run_disk_guard_venv314.bat.
cd /d "E:\Project\Mt5 Ea"
"E:\Project\Mt5 Ea\.venv314\Scripts\python.exe" -X utf8 "mt5_mcp\disk_guard.py" >> "E:\Project\Mt5 Ea\logs\disk_guard.log" 2>&1
