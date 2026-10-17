' MT5 Strategy Research launcher — runs research_v2.py hidden
' Backtest agent: daily strategy discovery (M1/M5/M15 mix-and-match),
' outputs ranked research + walk-forward validation to memory\research\.
' Dedicated trading venv (see desk_service.vbs note re: hermes venv resets)
CreateObject("Wscript.Shell").Run """E:\Project\Mt5 Ea\.venv\Scripts\pythonw.exe"" -B ""E:\Project\Mt5 Ea\research_v2.py""", 0, False
