# Kill ALL watchers, watchdogs, and synthetic watchers
# Then we'll start a single clean watcher

$patterns = @('trade_watch.py', 'run_synthetic_watcher', 'service_watchdog')
$killed = @()

$procs = Get-WmiObject Win32_Process -Filter "Name='python.exe'"
foreach ($p in $procs) {
    $cmd = $p.CommandLine
    foreach ($pat in $patterns) {
        if ($cmd -match [regex]::Escape($pat)) {
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
                $killed += "$($p.ProcessId) ($pat)"
                Write-Host "Killed $($p.ProcessId) : $pat"
            } catch {
                Write-Host "Failed to kill $($p.ProcessId): $_"
            }
            break
        }
    }
}

if ($killed.Count -eq 0) {
    Write-Host "No matching processes found"
} else {
    Write-Host "`nKilled $($killed.Count) processes"
}

# Also clear the pidfile
$pidfile = "E:\Project\Mt5 Ea\mt5_mcp\memory\synthetic_watcher.pid"
if (Test-Path $pidfile) {
    Remove-Item $pidfile -Force
    Write-Host "Cleared pidfile"
}
