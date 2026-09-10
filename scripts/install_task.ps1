# Register/Uninstall daily 07:00 scheduled task for AI digest (run as Administrator).
# Usage:
#   .\scripts\install_task.ps1            # register
#   .\scripts\install_task.ps1 -Uninstall # uninstall
param(
    [switch]$Uninstall,
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$TaskName = "AIDigestDaily"
$PrepareTaskName = "AIDigestPrepare"
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script = Join-Path $ProjectRoot "scripts\send_ready.py"

if ($Uninstall) {
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
    schtasks /Delete /TN $PrepareTaskName /F 2>$null | Out-Null
    Write-Host "Task '$TaskName' removed (if it existed)."
    exit 0
}

if (-not (Test-Path $Python)) {
    Write-Error "python not found at $Python - create venv first: python -m venv .venv"
    exit 1
}
if (-not (Test-Path $Script)) {
    Write-Error "script not found at $Script"
    exit 1
}

# Delivery reads the current day ready pointer.
$TaskRun = '"' + $Python + '" "' + $Script + '"'

$PrepareScript = Join-Path $ProjectRoot "scripts\prepare_daily.py"
$PrepareRun = '"' + $Python + '" "' + $PrepareScript + '"'
schtasks /Create /TN $PrepareTaskName /TR $PrepareRun /SC DAILY /ST 06:05 /F
if ($LASTEXITCODE -ne 0) { exit 1 }
# Register delivery separately from preparation.
schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
schtasks /Create /TN $TaskName /TR $TaskRun /SC DAILY /ST 07:00 /F

if ($LASTEXITCODE -eq 0) {
    Write-Host "Registered task '$TaskName' (daily 07:00; report cutoff remains 06:00)."
    Write-Host "Test now:        Start-ScheduledTask -TaskName $TaskName"
    Write-Host "Check last run:  Get-ScheduledTaskInfo -TaskName $TaskName"
} else {
    Write-Host "schtasks failed with exit code $LASTEXITCODE"
    exit 1
}
