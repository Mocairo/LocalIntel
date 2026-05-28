$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartScript = Join-Path $Root "start_local_intel.ps1"
$TaskName = "LocalIntelAutoStart"

if (-not (Test-Path $StartScript)) {
    throw "Cannot find $StartScript"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`" -NoBrowser"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Start Local Intel dashboard and daily scheduler when this Windows user logs in." `
    -Force | Out-Null

Write-Host "Installed Windows autostart task: $TaskName"
Write-Host "It will start Local Intel after you log in to Windows."
