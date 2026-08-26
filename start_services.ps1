<#
.SYNOPSIS
    Starts all MT5 services as hidden background processes.

.DESCRIPTION
    Launches dashboards, synthetic watcher, and BTC trend watcher â€” all hidden.
    No visible windows. Run once; services stay alive in background.
    Re-run to restart if anything dies.

.EXAMPLE
    .\start_services.ps1
    .\start_services.ps1 -Stop          # kill all
    .\start_services.ps1 -Status        # check what's running
#>

param(
    [switch]$Stop,
    [switch]$Status,
    [string]$ProjectDir = "E:\Project\Mt5 Ea"
)

$pythonExe = (Get-Command python).Source
$logDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

# â”€â”€ Load .env â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$envFile = Join-Path $ProjectDir ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and !$line.StartsWith("#")) {
            $parts = $line.Split("=", 2)
            if ($parts.Count -eq 2) {
                [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim().Trim('"').Trim("'"), "Process")
            }
        }
    }
}

# â”€â”€ Service definitions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
$services = @(
    @{
        Name = "Prop Dashboard"
        File = "logs/prop_dashboard.pid"
        Cmd  = @("-u", "web_dashboard.py", "--port", "8051",
                 "--terminal", '"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"',
                 "--login", "11911448", "--password", '""', "--server", "FundingPips2-SIM")
        Log  = "logs/prop_dashboard.log"
    },
    @{
        Name = "Deriv Dashboard"
        File = "logs/deriv_dashboard.pid"
        Cmd  = @("-u", "web_dashboard.py", "--port", "8050",
                 "--terminal", '"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe"',
                 "--login", "21727841", "--password", '"REDACTED_MT5_PASSWORD$"', "--server", "DerivVU-Server")
        Log  = "logs/deriv_dashboard.log"
    },
    @{
        Name = "Synthetic Watcher"
        File = "logs/trade_watch.pid"
        Cmd  = @("-u", "run_synthetic_watcher.py", "--live",
                 "--symbols", '"BOOM500,BOOM600,BOOM900"',
                 "--interval", "15", "--confidence-floor", "90",
                 "--max-spike-age", "15", "--lookback", "60",
                 "--log-file", "logs/trade_watch_live.log")
        Log  = "logs/synthetic_watcher.log"
    },
    @{
        Name = "BTC Trend Watch"
        File = "logs/btc_trend_watch.pid"
        Cmd  = @("-u", "btc_trend_watch.py", "--live",
                 "--interval", "300", "--ema-fast", "50", "--ema-slow", "200",
                 "--atr-period", "14", "--sl-atr", "2.0", "--trail-atr", "2.5",
                 "--risk-percent", "0.5", "--timeframe", "H4",
                 "--min-bars-between", "20", "--log-file", "logs/btc_trend_live.log")
        Log  = "logs/btc_trend_watcher.log"
    },
    @{
        Name = "Telegram Bot"
        File = "logs/telegram_bot.pid"
        Cmd  = @("-X", "utf8", "-u", "telegram_bot.py")
        Log  = "logs/telegram_bot.log"
    }
)

# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function Get-ServiceStatus {
    foreach ($svc in $services) {
        $pidFile = Join-Path $ProjectDir $svc.File
        $alive = $false
        $procId = $null
        if (Test-Path $pidFile) {
            $procId = (Get-Content $pidFile -Raw).Trim().Trim([char]0xFEFF)
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            $alive = $null -ne $proc
        }
        $status = if ($alive) { "RUNNING (PID $procId)" } else { "STOPPED" }
        Write-Host "  $($svc.Name): $status"
    }
    # Check dashboards by port
    $ports = @(8050, 8051)
    foreach ($port in $ports) {
        $listening = netstat -ano | Select-String ":$port\s.*LISTENING"
        if ($listening) { Write-Host "  Dashboard :$port - LISTENING" }
        else { Write-Host "  Dashboard :$port - NOT LISTENING" }
    }
}

function Stop-AllServices {
    Write-Host "Stopping all services..." -ForegroundColor Yellow
    foreach ($svc in $services) {
        $pidFile = Join-Path $ProjectDir $svc.File
        if (Test-Path $pidFile) {
            $procId = (Get-Content $pidFile -Raw).Trim().Trim([char]0xFEFF)
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "All services stopped." -ForegroundColor Green
}

function Start-Services {
    # Verify terminals
    $propRunning = Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object {
        (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -match "MetaTrader 5"
    }
    if (-not $propRunning) {
        Write-Host "Starting Prop MT5 terminal..." -ForegroundColor Yellow
        Start-Process "C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"
        Start-Sleep -Seconds 8
    }

    $derivRunning = Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object {
        (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -match "Deriv"
    }
    if (-not $derivRunning) {
        Write-Host "Starting Deriv MT5 terminal..." -ForegroundColor Yellow
        Start-Process "C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe"
        Start-Sleep -Seconds 8
    }

    foreach ($svc in $services) {
        $pidFile = Join-Path $ProjectDir $svc.File
        # Check if already running
        if (Test-Path $pidFile) {
            $oldPid = (Get-Content $pidFile -Raw).Trim()
            $alive = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
            if ($alive) {
                Write-Host "$($svc.Name): already running (PID $oldPid)" -ForegroundColor DarkGray
                continue
            }
        }

        Write-Host "Starting $($svc.Name)..." -ForegroundColor Yellow
        $logPath = Join-Path $ProjectDir $svc.Log
        $logDirSplit = Split-Path $logPath -Parent
        if (-not (Test-Path $logDirSplit)) { New-Item -ItemType Directory -Path $logDirSplit -Force | Out-Null }

        $proc = Start-Process $pythonExe -ArgumentList $svc.Cmd `
            -WorkingDirectory $ProjectDir `
            -RedirectStandardOutput $logPath `
            -RedirectStandardError "$logPath.err" `
            -WindowStyle Hidden `
            -PassThru

        # Wait briefly then verify
        Start-Sleep -Seconds 3
        $proc.Refresh()
        if (-not $proc.HasExited) {
            $proc.Id | Set-Content -Path $pidFile -Encoding ASCII -NoNewline
            Write-Host "$($svc.Name): started (PID $($proc.Id))" -ForegroundColor Green
        } else {
            Write-Host "$($svc.Name): FAILED to start" -ForegroundColor Red
        }
    }

    Write-Host "`nDone. All services running in background." -ForegroundColor Cyan
    Write-Host "Ports: Deriv=8050, Prop=8051" -ForegroundColor Cyan
}

# â”€â”€ Main â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
if ($Stop) {
    Stop-AllServices
    exit 0
}

if ($Status) {
    Get-ServiceStatus
    exit 0
}

Start-Services
