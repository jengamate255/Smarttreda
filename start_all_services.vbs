Option Explicit
' Launch all MT5 trading services fully detached (pythonw, hidden) - hermes venv.
Dim shell, fso, baseDir, pyw, py, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = "E:\Project\Mt5 Ea"

pyw = "C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe"
py  = "C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
If Not fso.FileExists(pyw) Then
  WScript.Echo "pythonw not found: " & pyw
  WScript.Quit 1
End If

shell.CurrentDirectory = baseDir

' Core desk services (desk_service.vbs falls back to hermes now that .venv is disabled)
shell.Run "wscript.exe """ & baseDir & "\desk_service.vbs"" gateway", 0, False
shell.Run "wscript.exe """ & baseDir & "\desk_service.vbs"" watch", 0, False
shell.Run "wscript.exe """ & baseDir & "\desk_service.vbs"" telegram", 0, False
shell.Run "wscript.exe """ & baseDir & "\desk_service.vbs"" desk", 0, False
shell.Run "wscript.exe """ & baseDir & "\service.vbs"" prop", 0, False

' Confirm-execute daemon (live closed-confirmation scanner)
cmd = """" & pyw & """ -u -B """ & baseDir & "\confirm_execute_daemon.py"""
shell.Run cmd, 0, False

' Analysis agent loop
cmd = """" & pyw & """ -u -B """ & baseDir & "\analysis_agent.py"""
shell.Run cmd, 0, False

' Alert monitor
If fso.FileExists(baseDir & "\alert_monitor.py") Then
  cmd = """" & pyw & """ -u -B """ & baseDir & "\alert_monitor.py"""
  shell.Run cmd, 0, False
End If

' Service watchdog (hermes python - has packages)
If fso.FileExists(baseDir & "\service_watchdog.py") Then
  cmd = """" & py & """ -u """ & baseDir & "\service_watchdog.py"" --interval 30"
  shell.Run cmd, 0, False
End If
