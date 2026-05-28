param(
    [switch]$NoBrowser,
    [switch]$WebOnly,
    [switch]$SchedulerOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $Root "data"
$LogDir = Join-Path $Root "logs"
$WebPidFile = Join-Path $DataDir "web.pid"
$SchedulerPidFile = Join-Path $DataDir "scheduler.pid"
$WebLog = Join-Path $LogDir "web.out.log"
$WebErr = Join-Path $LogDir "web.err.log"
$SchedulerLog = Join-Path $LogDir "scheduler.out.log"
$SchedulerErr = Join-Path $LogDir "scheduler.err.log"
$Python = "python"
$Url = "http://127.0.0.1:8765/"

New-Item -ItemType Directory -Force -Path $DataDir, $LogDir | Out-Null
Set-Location $Root

function Test-PidAlive {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    $text = (Get-Content $Path -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $text) { return $false }
    $pidValue = 0
    if (-not [int]::TryParse($text, [ref]$pidValue)) { return $false }
    return [bool](Get-Process -Id $pidValue -ErrorAction SilentlyContinue)
}

function Start-LocalProcess {
    param(
        [string]$Name,
        [string[]]$ProcessArgs,
        [string]$PidFile,
        [string]$OutFile,
        [string]$ErrFile
    )
    if (Test-PidAlive $PidFile) {
        $pidText = Get-Content $PidFile | Select-Object -First 1
        Write-Host "$Name already running. PID: $pidText"
        return
    }
    $process = Start-Process `
        -FilePath $Python `
        -ArgumentList $ProcessArgs `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $OutFile `
        -RedirectStandardError $ErrFile `
        -PassThru
    Set-Content -Path $PidFile -Value $process.Id
    Write-Host "$Name started. PID: $($process.Id)"
}

if (-not $SchedulerOnly) {
    $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        Write-Host "Dashboard already listening on $Url"
    } else {
        Start-LocalProcess `
            -Name "Dashboard" `
            -ProcessArgs @("-B", "-m", "app.web", "--config", ".\config.toml", "--env", ".\.env", "--host", "127.0.0.1", "--port", "8765") `
            -PidFile $WebPidFile `
            -OutFile $WebLog `
            -ErrFile $WebErr
    }
}

if (-not $WebOnly) {
    Start-LocalProcess `
        -Name "Daily scheduler" `
        -ProcessArgs @("-B", "-m", "app.scheduler", "--config", ".\config.toml", "--env", ".\.env") `
        -PidFile $SchedulerPidFile `
        -OutFile $SchedulerLog `
        -ErrFile $SchedulerErr
}

if (-not $NoBrowser -and -not $SchedulerOnly) {
    Start-Process $Url
}

Write-Host ""
Write-Host "Local Intel is ready: $Url"
Write-Host "Use .\status_local_intel.ps1 to check status."
Write-Host "Use .\stop_local_intel.ps1 to stop it."
