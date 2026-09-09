# Register/Uninstall daily 07:00 scheduled task for AI digest (run as Administrator).
# Usage:
#   .\scripts\install_task.ps1            # register
#   .\scripts\install_task.ps1 -Uninstall # uninstall
param(
    [switch]$Uninstall,
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$TaskName = "AIDigestDaily"
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script = Join-Path $ProjectRoot "scripts\run_daily.py"

if ($Uninstall) {
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
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

# Build:  "python.exe" "run_daily.py" --input db --send
$TaskRun = '"' + $Python + '" "' + $Script + '" --input db --send'

# Remove any stale task first, then create.
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
