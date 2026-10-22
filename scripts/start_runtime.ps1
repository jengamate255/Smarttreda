<#
  Smarttreda runtime launcher (idempotent).

  Starts, in order, only what is not already running:
    1. MT5 terminal   C:\Program Files\MetaTrader 5\terminal64.exe
    2. Web dashboard  (uvicorn on 127.0.0.1:8050)
    3. Trade desk     (trade_desk.py, MT5_DESK_MODE=SIM guard)

  Safe to run repeatedly (e.g. from a logon task): every step is guarded, so
  no duplicate dashboard/desk instances and no duplicate order executors.
#>

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$LogDir = Join-Path $Root 'logs'
$Log = Join-Path $LogDir 'runtime_launcher.log'

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log([string]$Message) {
    $line = '{0} | {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $Log -Value $line -Encoding utf8
}

function Get-SmarttredaProcesses([string]$Pattern) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*$Pattern*" -and $_.CommandLine -like '*Smarttreda*' }
}

try {
    # 1. MT5 terminal (single instance per installation)
    if (-not (Get-Process terminal64 -ErrorAction SilentlyContinue)) {
        $terminal = 'C:\Program Files\MetaTrader 5\terminal64.exe'
        if (Test-Path $terminal) {
            Start-Process -FilePath $terminal -WorkingDirectory (Split-Path -Parent $terminal)
            Write-Log 'started MT5 terminal'
            Start-Sleep -Seconds 20
        } else {
            Write-Log "MT5 terminal missing at $terminal"
        }
    } else {
        Write-Log 'MT5 terminal already running'
    }

    if (-not (Test-Path $Python)) {
        Write-Log "python missing at $Python - aborting"
        exit 1
    }

    # 2. Web dashboard (port guard)
    $listening = @(Get-NetTCPConnection -LocalPort 8050 -State Listen -ErrorAction SilentlyContinue).Count -gt 0
    if ($listening) {
        Write-Log 'dashboard already listening on 8050'
    } else {
        Start-Process -FilePath $Python -ArgumentList 'web_dashboard.py' -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $LogDir 'dashboard_console.log') `
            -RedirectStandardError (Join-Path $LogDir 'dashboard_console.err.log')
        Write-Log 'started dashboard'
    }

    # 3. Trade desk (process guard - must never run twice, it submits orders)
    if (@(Get-SmarttredaProcesses 'trade_desk.py').Count -gt 0) {
        Write-Log 'trade desk already running'
    } else {
        Start-Process -FilePath $Python -ArgumentList 'trade_desk.py' -WorkingDirectory $Root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $LogDir 'desk_console.log') `
            -RedirectStandardError (Join-Path $LogDir 'desk_console.err.log')
        Write-Log 'started trade desk'
    }
} catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    throw
}
