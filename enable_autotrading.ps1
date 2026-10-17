Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")] public static extern IntPtr SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
}
"@

$mt5 = Get-Process terminal64 | Where-Object { $_.MainWindowTitle -like '*Deriv*' } | Select-Object -First 1

if ($mt5) {
    $hwnd = $mt5.MainWindowHandle
    Write-Host "Deriv MT5: PID $($mt5.Id) hWnd=$hwnd"
    Write-Host "Title: $($mt5.MainWindowTitle)"

    # Restore if minimized
    if ([Win32]::IsIconic($hwnd)) {
        [Win32]::ShowWindow($hwnd, 9)  # SW_RESTORE
        Start-Sleep -Milliseconds 500
    }

    # Force to foreground
    [Win32]::SetForegroundWindow($hwnd) | Out-Null
    Start-Sleep -Milliseconds 1000

    # Send Ctrl+E
    [System.Windows.Forms.SendKeys]::SendWait('^{e}')
    Write-Host "Ctrl+E sent successfully"
    Start-Sleep -Milliseconds 500
} else {
    Write-Host "Deriv MT5 terminal not found"
}
