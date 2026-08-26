$ErrorActionPreference = "Stop"
$Root = "E:\Project\Mt5 Ea"
$Python = "C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "hermes venv python not found: $Python"
}

$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'run_dual_accounts' }
if ($existing) {
    Write-Warning "supervisor already running:"
    $existing | ForEach-Object { Write-Warning "  pid $($_.ProcessId)" }
    exit 1
}

$env:MT5_MCP_MT5_LOGIN = '11911448'
$env:MT5_MCP_MT5_PASSWORD = ''
$env:MT5_MCP_MT5_SERVER = 'FundingPips2-SIM'
$env:MT5_MCP_MT5_TERMINAL_PATH = 'C:\Program Files\MetaTrader 5 Terminal\terminal64.exe'

Start-Process -FilePath $Python -ArgumentList 'run_dual_accounts.py', '--live' `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Root 'logs\dual_accounts_live.out.log') `
    -RedirectStandardError (Join-Path $Root 'logs\dual_accounts_live.err.log')

Start-Sleep -Seconds 8
Get-Content (Join-Path $Root 'logs\dual_accounts_status.json') -Raw