# Kill ALL watchers, watchdogs, synthetic watchers, and clear pidfiles
$patterns = @('trade_watch.py', 'run_synthetic_watcher', 'service_watchdog')
$killed = 0

$procs = Get-WmiObject Win32_Process -Filter "Name='python.exe'"
foreach ($p in $procs) {
    $cmd = $p.CommandLine
    foreach ($pat in $patterns) {
        if ($cmd -match [regex]::Escape($pat)) {
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
                $killed++
                Write-Host "Killed $($p.ProcessId) : $pat"
            } catch {
                Write-Host "Failed: $($p.ProcessId)"
            }
            break
        }
    }
}
Write-Host "`nKilled $killed processes"

# Clear pidfiles
$pidfiles = @(
    "E:\Project\Mt5 Ea\mt5_mcp\memory\synthetic_watcher.pid",
    "E:\Project\Mt5 Ea\logs\trade_watch.pid"
)
foreach ($pf in $pidfiles) {
    if (Test-Path $pf) {
        Remove-Item $pf -Force
        Write-Host "Cleared $pf"
    }
}

# Wait a moment for processes to die
Start-Sleep -Seconds 2

# Verify
$remaining = Get-WmiObject Win32_Process -Filter "Name='python.exe'"
$still_running = @()
foreach ($p in $remaining) {
    foreach ($pat in $patterns) {
        if ($p.CommandLine -match [regex]::Escape($pat)) {
            $still_running += $p.ProcessId
            break
        }
    }
}
if ($still_running.Count -gt 0) {
    Write-Host "`nWARNING: Still running: $($still_running -join ', ')"
} else {
    Write-Host "`nAll clean"
}
