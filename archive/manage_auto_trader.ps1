<#
Manage the guarded SIM-only autonomous trader.

Examples:
  .\manage_auto_trader.ps1 -Action Install
  .\manage_auto_trader.ps1 -Action Status
  .\manage_auto_trader.ps1 -Action Disable
  .\manage_auto_trader.ps1 -Action Enable
  .\manage_auto_trader.ps1 -Action Remove
#>

param(
    [ValidateSet("Install", "Status", "Disable", "Enable", "Remove")]
    [string]$Action = "Status"
)

$ErrorActionPreference = "Stop"
$TaskName = "MT5AutoTraderSIM"
$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $BaseDir "run_auto_trader_sim.bat"
$MemoryDir = Join-Path $BaseDir "mt5_mcp\memory"
$KillPath = Join-Path $MemoryDir "AUTO_TRADER_KILL"

if (-not (Test-Path -LiteralPath $BaseDir)) {
    throw "Project directory not found: $BaseDir"
}
if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Runner not found: $Runner"
}

switch ($Action) {
    "Install" {
        if (Test-Path -LiteralPath $KillPath) {
            Remove-Item -LiteralPath $KillPath -Force
        }
        $taskAction = New-ScheduledTaskAction `
            -Execute "cmd.exe" `
            -Argument ('/d /c call "' + $Runner + '"') `
            -WorkingDirectory $BaseDir
        $trigger = New-ScheduledTaskTrigger `
            -Once `
            -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Minutes 5) `
            -RepetitionDuration (New-TimeSpan -Days 3650)
        $settings = New-ScheduledTaskSettingsSet `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 4) `
            -StartWhenAvailable
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $taskAction `
            -Trigger $trigger `
            -Settings $settings `
            -Description "Guarded SIM-only MT5 strategy runner" `
            -Force | Out-Null
        Write-Host "Installed $TaskName (every 5 minutes, SIM-only)."
        Write-Host "It will evaluate on the next scheduled run; it was not started immediately."
    }
    "Status" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Host "Task '$TaskName' is not installed."
        } else {
            Get-ScheduledTaskInfo -TaskName $TaskName | Format-List
            Write-Host "Task state: $($task.State)"
        }
        if (Test-Path -LiteralPath $KillPath) {
            Write-Host "Kill switch: ACTIVE ($KillPath)" -ForegroundColor Yellow
        } else {
            Write-Host "Kill switch: inactive" -ForegroundColor Green
        }
    }
    "Disable" {
        if (-not (Test-Path -LiteralPath $MemoryDir)) {
            New-Item -ItemType Directory -Path $MemoryDir -Force | Out-Null
        }
        New-Item -ItemType File -Path $KillPath -Force | Out-Null
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            Disable-ScheduledTask -TaskName $TaskName | Out-Null
        }
        Write-Host "Auto-trader disabled. Kill switch: $KillPath"
    }
    "Enable" {
        if (Test-Path -LiteralPath $KillPath) {
            Remove-Item -LiteralPath $KillPath -Force
        }
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            throw "Could not enable scheduled task '$TaskName'"
        }
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
        Write-Host "Auto-trader enabled (SIM-only)."
    }
    "Remove" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        if (Test-Path -LiteralPath $KillPath) {
            Remove-Item -LiteralPath $KillPath -Force
        }
        Write-Host "Removed $TaskName."
    }
}
