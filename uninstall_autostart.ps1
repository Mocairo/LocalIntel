$ErrorActionPreference = "Stop"
$TaskName = "LocalIntelAutoStart"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed Windows autostart task: $TaskName"
} else {
    Write-Host "Autostart task was not installed: $TaskName"
}
