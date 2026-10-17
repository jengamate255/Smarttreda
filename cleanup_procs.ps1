# Kill all watcher processes
$watchers = @(5108, 17816, 21976)
foreach ($p in $watchers) {
    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    Write-Output "Killed watcher PID $p"
}
# Kill duplicate watchdogs (keep 17932 which uses hermes python)
$dup_watchdogs = @(5768, 20652)
foreach ($p in $dup_watchdogs) {
    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    Write-Output "Killed watchdog PID $p"
}
Start-Sleep -Seconds 2
# Clear pidfile
Remove-Item "E:\Project\Mt5 Ea\logs\trade_watch.pid" -Force -ErrorAction SilentlyContinue
Write-Output "Done. Single watchdog (PID 17932) will respawn one watcher."
