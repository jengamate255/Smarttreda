Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    $p = $_
    $wmi = Get-WmiObject Win32_Process -Filter ("ProcessId=" + $p.Id)
    if ($wmi.CommandLine -like '*run_synthetic_watcher*') {
        Write-Output ("PID {0}: {1}" -f $p.Id, $wmi.CommandLine.Substring(0, [Math]::Min(150, $wmi.CommandLine.Length)))
    }
}
