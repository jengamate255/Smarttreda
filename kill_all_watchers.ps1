# Kill all watchdogs to prevent respawning
Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    $p = $_
    $wmi = Get-WmiObject Win32_Process -Filter ("ProcessId=" + $p.Id)
    if ($wmi.CommandLine -like '*service_watchdog*') {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        Write-Output "Killed watchdog PID $($p.Id)"
    }
    if ($wmi.CommandLine -like '*run_synthetic_watcher*') {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        Write-Output "Killed watcher PID $($p.Id)"
    }
}
Start-Sleep -Seconds 1
Remove-Item "E:\Project\Mt5 Ea\logs\trade_watch.pid" -Force -ErrorAction SilentlyContinue
Write-Output "All cleaned"
