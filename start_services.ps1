<#>
.SYNOPSIS
    Starts the MT5 MCP Server and Web Dashboard as background services.

.DESCRIPTION
    This script starts both the MCP server (for AI agents) and the web dashboard.
    Configured to run at Windows startup via Task Scheduler.

.NOTES
    Requires: MetaTrader 5 terminal running and logged in
    Environment variables from .env file in project root
#>

param(
    [switch]$McpOnly,
    [switch]$DashboardOnly,
    [string]$ProjectDir = "E:\Project\Mt5 Ea"
)

$ErrorActionPreference = "Stop"

# Load .env file
$envFile = Join-Path $ProjectDir ".env"
if (Test-Path $envFile) {
    $lines = Get-Content $envFile
    foreach ($line in $lines) {
        $line = $line.Trim()
        if ($line -and !$line.StartsWith("#")) {
            $parts = $line.Split("=", 2)
            if ($parts.Count -eq 2) {
                $name = $parts[0].Trim()
                $value = $parts[1].Trim().Trim('"').Trim("'")
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
    }
    Write-Host "Loaded environment from $envFile" -ForegroundColor Green
}

# Verify MT5 terminal is running
$mt5Process = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue
if (-not $mt5Process) {
    Write-Warning "MetaTrader 5 terminal (terminal64.exe) not running. Starting it..."
    $terminalPath = $env:MT5_MCP_MT5_TERMINAL_PATH ?? "C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"
    if (Test-Path $terminalPath) {
        Start-Process $terminalPath
        Start-Sleep -Seconds 10
    } else {
        Write-Error "MT5 terminal not found at $terminalPath. Set MT5_MCP_MT5_TERMINAL_PATH in .env"
    }
}

$pythonExe = (Get-Command python).Source
Write-Host "Using Python: $pythonExe" -ForegroundColor Cyan

# Start MCP Server (stdio transport for MCP clients)
if (-not $DashboardOnly) {
    Write-Host "Starting MCP Server..." -ForegroundColor Yellow
    $mcpArgs = @("mt5_mcp/server.py")
    $mcpProcess = Start-Process $pythonExe -ArgumentList $mcpArgs -WorkingDirectory $ProjectDir -PassThru
    Write-Host "MCP Server started (PID: $($mcpProcess.Id))" -ForegroundColor Green
}

# Start Web Dashboard
if (-not $McpOnly) {
    Write-Host "Starting Web Dashboard on http://localhost:8050..." -ForegroundColor Yellow
    $dashArgs = @("web_dashboard.py")
    $dashProcess = Start-Process $pythonExe -ArgumentList $dashArgs -WorkingDirectory $ProjectDir -PassThru
    Write-Host "Web Dashboard started (PID: $($dashProcess.Id))" -ForegroundColor Green
}

Write-Host "`nServices running. Press Ctrl+C to stop." -ForegroundColor Cyan

# Keep script alive to maintain background processes
try {
    while ($true) { Start-Sleep -Seconds 60 }
} finally {
    if ($mcpProcess) { Stop-Process -Id $mcpProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($dashProcess) { Stop-Process -Id $dashProcess.Id -Force -ErrorAction SilentlyContinue }
    Write-Host "Services stopped." -ForegroundColor Yellow
}