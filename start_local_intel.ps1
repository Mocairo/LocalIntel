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
    param(
        [string]$Path,
        [string]$ExpectedText = ""
    )
    if (-not (Test-Path $Path)) { return $false }
    $text = (Get-Content $Path -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $text) { return $false }
    $pidValue = 0
    if (-not [int]::TryParse($text, [ref]$pidValue)) { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    if ($ExpectedText -and ($process.CommandLine -notlike "*$ExpectedText*")) { return $false }
    return $true
}

function Start-LocalProcess {
    param(
        [string]$Name,
        [string[]]$ProcessArgs,
        [string]$PidFile,
        [string]$OutFile,
        [string]$ErrFile,
        [string]$ExpectedText
    )
    if (Test-PidAlive $PidFile $ExpectedText) {
        $pidText = Get-Content $PidFile | Select-Object -First 1
        Write-Host "$Name already running. PID: $pidText"
        return
    }
    if (Test-Path $PidFile) {
        Remove-Item -Force $PidFile
        Write-Host "$Name had a stale PID file. Removed it."
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

function Get-PortListenerProcess {
    param([int]$Port)
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $null }
    return Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
}

function Wait-DashboardReady {
    param([int]$TimeoutSeconds = 60)
    $statusUrl = "$($Url.TrimEnd('/'))/api/runtime-status"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if ((Test-Path $WebPidFile) -and -not (Test-PidAlive $WebPidFile "app.web")) {
            return $false
        }
        try {
            $response = Invoke-WebRequest -Uri $statusUrl -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return $true }
        } catch {
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $false
}

if (-not $SchedulerOnly) {
    $listenerProcess = Get-PortListenerProcess 8765
    if ($listenerProcess) {
        if ($listenerProcess.CommandLine -like "*app.web*") {
            Set-Content -Path $WebPidFile -Value $listenerProcess.ProcessId
            Write-Host "Dashboard already listening on $Url PID: $($listenerProcess.ProcessId)"
        } else {
            Write-Host "Port 8765 is already used by PID $($listenerProcess.ProcessId): $($listenerProcess.CommandLine)"
            exit 1
        }
    } else {
        Start-LocalProcess `
            -Name "Dashboard" `
            -ProcessArgs @("-B", "-m", "app.web", "--config", ".\config.toml", "--env", ".\.env", "--host", "127.0.0.1", "--port", "8765") `
            -PidFile $WebPidFile `
            -OutFile $WebLog `
            -ErrFile $WebErr `
            -ExpectedText "app.web"
    }
}

if (-not $WebOnly) {
    Start-LocalProcess `
        -Name "Daily scheduler" `
        -ProcessArgs @("-B", "-m", "app.scheduler", "--config", ".\config.toml", "--env", ".\.env") `
        -PidFile $SchedulerPidFile `
        -OutFile $SchedulerLog `
        -ErrFile $SchedulerErr `
        -ExpectedText "app.scheduler"
}

if (-not $SchedulerOnly) {
    if (-not (Wait-DashboardReady)) {
        Write-Host "Dashboard did not become ready on $Url"
        if (Test-Path $WebPidFile) {
            $pidText = Get-Content $WebPidFile -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($pidText) {
                $webProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$pidText" -ErrorAction SilentlyContinue
                if ($webProcess) {
                    Write-Host "Dashboard process is still running. PID: $pidText"
                } else {
                    Write-Host "Dashboard process exited early. PID: $pidText"
                }
            }
        }
        if (Test-Path $WebErr) {
            Write-Host ""
            Write-Host "Recent dashboard error log:"
            Get-Content $WebErr -Tail 40
        }
        exit 1
    }
}

if (-not $NoBrowser -and -not $SchedulerOnly) {
    Start-Process $Url
}

Write-Host ""
Write-Host "Local Intel is ready: $Url"
Write-Host "Use .\status_local_intel.ps1 to check status."
Write-Host "Use .\stop_local_intel.ps1 to stop it."
