# Find and kill processes on ports 8080 and 8050
$ports = @(8080, 8050, 8082, 8060, 8051)
foreach ($port in $ports) {
    $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($conn) {
        $pids = $conn | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($pid in $pids) {
            if ($pid -gt 0) {
                $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
                if ($proc) {
                    Write-Host "Port $port : PID $pid ($($proc.ProcessName)) - killing"
                    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                }
            }
        }
    } else {
        Write-Host "Port $port : not in use"
    }
}

Start-Sleep -Seconds 2

# Disable the MT5R tasks that auto-respawn (they'll conflict)
$tasksToDisable = @('MT5R_Dashboard', 'MT5R_Telegram')
foreach ($tn in $tasksToDisable) {
    schtasks /End /TN $tn 2>&1 | Out-Null
    schtasks /Change /TN $tn /DISABLE 2>&1 | Out-Null
    Write-Host "Disabled: $tn"
}

Start-Sleep -Seconds 1
Write-Host "`nPorts cleared. Starting dashboard..."

# Start the FastAPI dashboard (8050) - it has all API routers
$python = "C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
$proc = Start-Process -FilePath $python `
    -ArgumentList "-X utf8 `"E:\Project\Mt5 Ea\run_dashboard.py`"" `
    -WorkingDirectory "E:\Project\Mt5 Ea" `
    -PassThru `
    -RedirectStandardOutput "E:\Project\Mt5 Ea\logs\dashboard_stdout.log" `
    -RedirectStandardError "E:\Project\Mt5 Ea\logs\dashboard_stderr.log"

Write-Host "Dashboard started: PID=$($proc.Id)"
Start-Sleep -Seconds 5

# Check if alive
$alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
if ($alive) {
    Write-Host "Dashboard ALIVE (PID=$($proc.Id))"
} else {
    Write-Host "Dashboard DIED"
}

# Try hitting it
try {
    $resp = Invoke-WebRequest -Uri 'http://localhost:8050' -UseBasicParsing -TimeoutSec 5
    Write-Host "HTTP 8050: $($resp.StatusCode)"
} catch {
    Write-Host "HTTP 8050: $($_.Exception.Message)"
}

try {
    $resp = Invoke-WebRequest -Uri 'http://localhost:8080' -UseBasicParsing -TimeoutSec 5
    Write-Host "HTTP 8080: $($resp.StatusCode)"
} catch {
    Write-Host "HTTP 8080: $($_.Exception.Message)"
}

# Check stderr
$stderr = Get-Content "E:\Project\Mt5 Ea\logs\dashboard_stderr.log" -ErrorAction SilentlyContinue
if ($stderr) {
    Write-Host "`nStderr (last 10):"
    $stderr | Select-Object -Last 10
}
