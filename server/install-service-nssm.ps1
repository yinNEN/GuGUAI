param(
    [string]$NssmPath = "C:\tools\nssm\nssm.exe",
    [string]$ServiceName = "DeepSeekChat"
)

$ErrorActionPreference = "Stop"
$ServerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ServerDir ".venv\Scripts\python.exe"
$DataDir = Join-Path $ServerDir "data"

if (-not (Test-Path $NssmPath)) {
    throw "NSSM not found at '$NssmPath'. Download it from https://nssm.cc/download, extract nssm.exe, and pass -NssmPath."
}
if (-not (Test-Path $PythonExe)) {
    throw "Virtual environment not found. Run install.ps1 first."
}
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir | Out-Null
}

Write-Host "Installing Windows service '$ServiceName'..."

& $NssmPath stop $ServiceName 2>$null | Out-Null
& $NssmPath remove $ServiceName confirm 2>$null | Out-Null

& $NssmPath install $ServiceName $PythonExe
if ($LASTEXITCODE -ne 0) { throw "nssm install failed." }

& $NssmPath set $ServiceName AppParameters "run.py"
& $NssmPath set $ServiceName AppDirectory $ServerDir
& $NssmPath set $ServiceName DisplayName "DeepSeek Chat Server"
& $NssmPath set $ServiceName Description "Private DeepSeek chat server with login and server-side API key"
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStdout (Join-Path $DataDir "service-out.log")
& $NssmPath set $ServiceName AppStderr (Join-Path $DataDir "service-err.log")
& $NssmPath set $ServiceName AppRotateFiles 1
& $NssmPath set $ServiceName AppRotateOnline 1
& $NssmPath set $ServiceName AppRotateBytes 10485760
& $NssmPath set $ServiceName AppEnvironmentExtra "PYTHONUNBUFFERED=1"
& $NssmPath set $ServiceName AppExit Default Restart
& $NssmPath set $ServiceName AppRestartDelay 3000

& $NssmPath start $ServiceName
if ($LASTEXITCODE -ne 0) { throw "nssm start failed. Check data\service-err.log." }

Write-Host "[OK] Service installed and started."
Write-Host "     Manage:  & '$NssmPath' restart $ServiceName"
Write-Host "     Remove:  & '$NssmPath' stop $ServiceName; & '$NssmPath' remove $ServiceName confirm"
Write-Host "     Logs:    $DataDir\service-out.log / service-err.log"
