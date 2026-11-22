param(
    [string]$TaskName = "DeepSeekChat",
    [string]$RunAsUser = "SYSTEM"
)

$ErrorActionPreference = "Stop"
$ServerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ServerDir ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    throw "python.exe not found. Run install.ps1 first."
}

Write-Host "Registering scheduled task '$TaskName' (runs at startup)..."

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "run.py" -WorkingDirectory $ServerDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host "[OK] Task registered and started."
Write-Host "     Status:  Get-ScheduledTask -TaskName $TaskName"
Write-Host "     Stop:    Stop-ScheduledTask -TaskName $TaskName"
Write-Host "     Remove:  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host "     Logs:    $ServerDir\data\server.log"
