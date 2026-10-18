$procs = Get-WmiObject Win32_Process -Filter "Name='python.exe'"
foreach ($p in $procs) {
    $cmd = $p.CommandLine
    if ($cmd.Length -gt 140) { $cmd = $cmd.Substring(0, 140) + "..." }
    Write-Host "$($p.ProcessId) : $cmd"
}
