# Trading Intelligence Platform - Service Starter
# Run this script in PowerShell

$venv = "C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
$projectDir = "E:\Project\Mt5 Ea"

Write-Host "Starting Trading Intelligence Platform..." -ForegroundColor Green
Write-Host ""

# Start Services
Write-Host "[1/2] Starting All Services (MT5 + Telegram + Dashboard)..." -ForegroundColor Yellow
Start-Process -FilePath $venv -ArgumentList "$projectDir\run_services.py" -WindowStyle Minimized

Start-Sleep -Seconds 5

Write-Host "[2/2] Checking services..." -ForegroundColor Yellow
Write-Host ""

# Check MT5
Write-Host "MT5 Connection:" -ForegroundColor Cyan
& $venv -c "import MetaTrader5 as mt5; mt5.initialize(); a = mt5.account_info(); print(f'  Account: {a.login} @ {a.server}'); print(f'  Balance: ${a.balance:,.2f}'); print(f'  Equity: ${a.equity:,.2f}'); mt5.shutdown()"

# Check Dashboard
Write-Host ""
Write-Host "Dashboard:" -ForegroundColor Cyan
$port = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
if ($port) {
    Write-Host "  Running at http://127.0.0.1:8080" -ForegroundColor Green
} else {
    Write-Host "  Starting..." -ForegroundColor Yellow
    Start-Sleep -Seconds 3
    $port = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
    if ($port) {
        Write-Host "  Running at http://127.0.0.1:8080" -ForegroundColor Green
    } else {
        Write-Host "  Not running" -ForegroundColor Red
    }
}

# Check Telegram
Write-Host ""
Write-Host "Telegram Bot:" -ForegroundColor Cyan
Write-Host "  Running (check logs at $projectDir\logs\services.log)" -ForegroundColor Green

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "All Services Started!" -ForegroundColor Green
Write-Host "Dashboard: http://127.0.0.1:8080" -ForegroundColor Cyan
Write-Host "Logs: $projectDir\logs\services.log" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Green
