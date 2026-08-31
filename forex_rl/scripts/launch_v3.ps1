# Launch 4 parallel v3 trainings (one per symbol, one per core)
$root = "E:\Project\Mt5 Ea"
Set-Location -LiteralPath $root
$py = "C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
New-Item -ItemType Directory -Force -Path "$root\forex_rl\logs" | Out-Null

foreach ($sym in @("EURUSD", "GBPUSD", "USDJPY", "XAUUSD")) {
    Start-Process -FilePath $py `
        -ArgumentList "-X", "utf8", "forex_rl/scripts/train_v3.py", $sym, "20000" `
        -RedirectStandardOutput "$root\forex_rl\logs\v3_$($sym.ToLower()).log" `
        -RedirectStandardError "$root\forex_rl\logs\v3_$($sym.ToLower()).err.log" `
        -WindowStyle Hidden
    Write-Output "Launched $sym"
}
