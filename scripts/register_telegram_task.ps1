Param([string]$TaskName="MT5TelegramBot")
$proj = Split-Path $PSScriptRoot -Parent
$python = "C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
$action = New-ScheduledTaskAction -Execute $python -Argument "run_telegram_bot.py" -WorkingDirectory $proj
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Hours 0)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "MT5 Telegram bot with MT5 gateway - single instance, auto-restart"
Write-Host "Registered $TaskName -> $proj\run_telegram_bot.py"
schtasks /query /tn $TaskName /v | Select-String "TaskName|Run Level|Last Result"
