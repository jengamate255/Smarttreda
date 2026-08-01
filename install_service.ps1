# Installs the MT5 web dashboard as a Windows service via NSSM so it
# starts automatically on boot and keeps running in the background.
#
# Usage (admin PowerShell):
#   .\install_service.ps1            # install + start service
#   .\install_service.ps1 -Uninstall
#
# Requires NSSM (https://nssm.cc) on PATH, or set -NssmPath.

param(
    [switch]$Uninstall,
    [string]$NssmPath = "nssm"
)

$ErrorActionPreference = "Stop"
$ServiceName = "MT5Dashboard"
$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $Python) { $Python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $Python) { throw "pythonw/python not found on PATH" }

if ($Uninstall) {
    & $NssmPath stop $ServiceName 2>$null | Out-Null
    & $NssmPath remove $ServiceName confirm | Out-Null
    Write-Host "Service '$ServiceName' removed."
    exit 0
}

if (-not (Get-Command $NssmPath -ErrorAction SilentlyContinue)) {
    throw "nssm not found on PATH. Download it from https://nssm.cc and add to PATH (or pass -NssmPath)."
}

& $NssmPath install $ServiceName $Python "$BaseDir\run_dashboard.py" | Out-Null
if (-not $?) { throw "nssm install failed" }

& $NssmPath set $ServiceName AppDirectory $BaseDir | Out-Null
& $NssmPath set $ServiceName DisplayName "MT5 Web Dashboard" | Out-Null
& $NssmPath set $ServiceName Description "Serves the MT5 trading dashboard on localhost:8050 with auto-restart watchdog." | Out-Null
& $NssmPath set $ServiceName AppStdout "$BaseDir\logs\service_stdout.log" | Out-Null
& $NssmPath set $ServiceName AppStderr "$BaseDir\logs\service_stderr.log" | Out-Null
& $NssmPath set $ServiceName AppRotateFiles 1 | Out-Null
& $NssmPath set $ServiceName Start SERVICE_AUTO_START | Out-Null
& $NssmPath start $ServiceName | Out-Null

Write-Host "Service '$ServiceName' installed and started."
Write-Host "Dashboard: http://localhost:8050"
Write-Host "Manage:   nssm edit $ServiceName   /   .\install_service.ps1 -Uninstall"
