Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    $p = $_
    $wmi = Get-WmiObject Win32_Process -Filter ("ProcessId=" + $p.Id)
    if ($wmi.CommandLine -like '*watchdog*' -or $wmi.CommandLine -like '*service_watch*') {
        Write-Output ("PID {0}: {1}" -f $p.Id, $wmi.CommandLine.Substring(0, [Math]::Min(200, $wmi.CommandLine.Length)))
    }
}
Write-Output "---"
Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    $p = $_
    $wmi = Get-WmiObject Win32_Process -Filter ("ProcessId=" + $p.Id)
    if ($wmi.CommandLine -like '*run_synthetic*') {
        $parent = Get-WmiObject Win32_Process -Filter ("ProcessId=" + $wmi.ParentProcessId) -ErrorAction SilentlyContinue
        Write-Output ("PID {0} parent={1} ({2})" -f $p.Id, $wmi.ParentProcessId, $parent.Name)
    }
}
