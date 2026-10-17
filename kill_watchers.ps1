$pids = @(8160, 9024, 10636, 18772, 23264)
foreach ($p in $pids) {
    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    Write-Output "Killed PID $p"
}
Start-Sleep -Seconds 2
Remove-Item "E:\Project\Mt5 Ea\logs\trade_watch.pid" -Force -ErrorAction SilentlyContinue
Write-Output "Pidfile cleared. Watchdog will respawn."
