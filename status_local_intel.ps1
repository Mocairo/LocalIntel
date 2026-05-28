$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $Root "data"
$ConfigPath = Join-Path $Root "config.toml"
$Url = "http://127.0.0.1:8765/"

function Get-PidStatus {
    param([string]$Name, [string]$Path)
    if (-not (Test-Path $Path)) {
        Write-Host "${Name}: not tracked"
        return
    }
    $text = Get-Content $Path -ErrorAction SilentlyContinue | Select-Object -First 1
    $pidValue = 0
    if (-not [int]::TryParse($text, [ref]$pidValue)) {
        Write-Host "${Name}: invalid pid file"
        return
    }
    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($process) {
        Write-Host "${Name}: running, PID $pidValue"
    } else {
        Write-Host "${Name}: stopped, stale PID $pidValue"
    }
}

Write-Host "Local Intel status"
Write-Host "URL: $Url"
Get-PidStatus "Dashboard" (Join-Path $DataDir "web.pid")
Get-PidStatus "Daily scheduler" (Join-Path $DataDir "scheduler.pid")

$listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host "Port 8765: listening"
} else {
    Write-Host "Port 8765: not listening"
}

if (Test-Path $ConfigPath) {
    $dailyLine = Select-String -Path $ConfigPath -Pattern '^\s*daily_time\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($dailyLine -and $dailyLine.Matches.Count -gt 0) {
        Write-Host "Daily run time: $($dailyLine.Matches[0].Groups[1].Value)"
    }
}
