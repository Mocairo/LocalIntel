@echo off
setlocal

set "ROOT=%~dp0"
set "START_SCRIPT=%ROOT%start_local_intel.ps1"

if not exist "%START_SCRIPT%" (
  echo start_local_intel.ps1 was not found in "%ROOT%".
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%START_SCRIPT%"
if errorlevel 1 (
  echo.
  echo LocalIntel failed to start. Check logs in "%ROOT%logs".
  pause
  exit /b 1
)

endlocal
