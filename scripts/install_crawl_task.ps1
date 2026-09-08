# Register hourly crawl scheduled task (run as Administrator).
# Usage:
#   .\scripts\install_crawl_task.ps1            # register
#   .\scripts\install_crawl_task.ps1 -Uninstall # uninstall
param(
    [switch]$Uninstall,
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$TaskName = "AIDigestCrawl"
$FinalTaskName = "AIDigestPreDailyCrawl"
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script = Join-Path $ProjectRoot "scripts\run_crawl.py"

if ($Uninstall) {
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
    schtasks /Delete /TN $FinalTaskName /F 2>$null | Out-Null
    Write-Host "Tasks '$TaskName' and '$FinalTaskName' removed (if they existed)."
    exit 0
}

if (-not (Test-Path $Python)) {
    Write-Error "python not found at $Python"
    exit 1
}
if (-not (Test-Path $Script)) {
    Write-Error "script not found at $Script"
    exit 1
}

# Every 60 minutes starting at 00:05 (i.e. :05 past each hour)
$TaskRun = '"' + $Python + '" "' + $Script + '"'

schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
schtasks /Create /TN $TaskName /TR $TaskRun /SC MINUTE /MO 60 /ST 00:05 /F
$HourlyExitCode = $LASTEXITCODE
schtasks /Delete /TN $FinalTaskName /F 2>$null | Out-Null
schtasks /Create /TN $FinalTaskName /TR $TaskRun /SC DAILY /ST 05:55 /F
$FinalExitCode = $LASTEXITCODE

if ($HourlyExitCode -eq 0 -and $FinalExitCode -eq 0) {
    Write-Host "Registered task '$TaskName' (hourly at :05)."
    Write-Host "Registered task '$FinalTaskName' (daily at 05:55)."
    Write-Host "Check: Get-ScheduledTaskInfo -TaskName $TaskName"
} else {
    Write-Host "schtasks failed: hourly=$HourlyExitCode final=$FinalExitCode"
    exit 1
}
