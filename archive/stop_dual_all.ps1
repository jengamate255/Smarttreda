$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'run_dual_accounts|run_synthetic_watcher|run_prop_trader' }
$ids = @($procs | ForEach-Object { $_.ProcessId })
foreach ($id in $ids) {
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    Write-Output "stopped $id"
}
$lock = "E:\Project\Mt5 Ea\logs\dual_accounts.lock"
if (Test-Path -LiteralPath $lock) {
    Remove-Item -LiteralPath $lock -Force
    Write-Output "removed lock"
}