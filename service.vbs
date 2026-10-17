Option Explicit

' Usage: wscript.exe "E:\Project\Mt5 Ea\service.vbs" <mode>
'   mode "prop"  -> web_dashboard.py, prop account  (port 8050)
'   mode "deriv" -> web_dashboard.py, Deriv account (port 8052)
'
' Runs pythonw.exe hidden (no console window). Credentials live in .env;
' MT5_MCP_ACCOUNT routes each instance to its own config section:
'   PROP  -> settings.mt5        (MT5_MCP_MT5_*)
'   DERIV -> settings.deriv_mt5   (MT5_MCP_DERIV_MT5_*)

Dim mode, shell, fso, baseDir, pyw, cmd
mode = WScript.Arguments.Item(0)
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

baseDir = "E:\Project\Mt5 Ea"
' Prefer hermes venv (has packages); .venv was incomplete
pyw = "C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then
  pyw = "E:\Project\Mt5 Ea\.venv\Scripts\pythonw.exe"
End If

If Not fso.FileExists(pyw) Then
  WScript.Echo "pythonw.exe not found: " & pyw
  WScript.Quit 1
End If

' Env vars must be set BEFORE Run so the child inherits them.
' They take precedence over .env (dotenv doesn't override existing env).
If mode = "deriv" Then
  shell.Environment("PROCESS")("WEB_PORT") = "8052"
  shell.Environment("PROCESS")("MT5_MCP_ACCOUNT") = "DERIV"
  shell.Environment("PROCESS")("MT5_MCP_DB_PATH") = "memory/journal_deriv.db"
Else
  shell.Environment("PROCESS")("WEB_PORT") = "8050"
  shell.Environment("PROCESS")("MT5_MCP_ACCOUNT") = "PROP"
End If

cmd = """" & pyw & """ -u -B """ & baseDir & "\web_dashboard.py"""

' Window style 0 = hidden, don't wait for exit
shell.Run cmd, 0, False
