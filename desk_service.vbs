Option Explicit

' Usage: wscript.exe "E:\Project\Mt5 Ea\desk_service.vbs" <mode>
'   "gateway"  -> mt5_gateway.py     (single terminal owner, HTTP API :8060)
'   "watch"    -> watch_daemon.py    (trigger-level alerts)
'   "desk"     -> trade_desk.py      (hedge-fund scan + management)
'   "telegram" -> telegram_bot.py    (Telegram control bot)
'
' Runs pythonw.exe hidden, no console window.

Dim mode, shell, fso, baseDir, pyw, cmd
mode = WScript.Arguments.Item(0)
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

baseDir = "E:\Project\Mt5 Ea"
' Prefer hermes-agent venv (has all packages). .venv was incomplete (no pip packages).
pyw = "C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then
  pyw = baseDir & "\.venv\Scripts\pythonw.exe"
End If

If Not fso.FileExists(pyw) Then
  WScript.Echo "pythonw.exe not found: " & pyw
  WScript.Quit 1
End If

Dim script
If mode = "watch" Then
  script = "watch_daemon.py"
ElseIf mode = "telegram" Then
  script = "telegram_bot.py"
ElseIf mode = "gateway" Then
  script = "mt5_gateway.py"
Else
  script = "trade_desk.py"
End If

cmd = """" & pyw & """ -u -B """ & baseDir & "\" & script & """"

' Hidden (0), don't wait
shell.Run cmd, 0, False
