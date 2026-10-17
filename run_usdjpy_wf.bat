@echo off
cd /d "E:\Project\Mt5 Ea"
set PY=C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
REM Clear any prior partial log so we see only this run
type nul > "E:\Project\Mt5 Ea\logs\usdjpy_wf.out"
type nul > "E:\Project\Mt5 Ea\logs\usdjpy_wf.err"
"%PY%" -u -X utf8 "E:\Project\Mt5 Ea\tmp_usdjpy_wf.py" > "E:\Project\Mt5 Ea\logs\usdjpy_wf.out" 2> "E:\Project\Mt5 Ea\logs\usdjpy_wf.err"
echo WF_EXIT=%ERRORLEVEL% >> "E:\Project\Mt5 Ea\logs\usdjpy_wf.err"
