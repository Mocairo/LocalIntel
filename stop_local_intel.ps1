$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $Root "data"
$PidFiles = @(
    @{ Name = "Dashboard"; Path = Join-Path $DataDir "web.pid" },
    @{ Name = "Daily scheduler"; Path = Join-Path $DataDir "scheduler.pid" }
)

foreach ($entry in $PidFiles) {
    if (-not (Test-Path $entry.Path)) {
        Write-Host "$($entry.Name) is not tracked."
        continue
    }
    $text = Get-Content $entry.Path -ErrorAction SilentlyContinue | Select-Object -First 1
    $pidValue = 0
    if (-not [int]::TryParse($text, [ref]$pidValue)) {
        Remove-Item -Force $entry.Path
        Write-Host "$($entry.Name) had an invalid pid file. Removed it."
        continue
    }
    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $pidValue -Force
        Write-Host "$($entry.Name) stopped. PID: $pidValue"
    } else {
        Write-Host "$($entry.Name) was not running. PID file removed."
    }
    Remove-Item -Force $entry.Path
}
