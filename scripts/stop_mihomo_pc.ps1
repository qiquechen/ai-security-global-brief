$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$installDir = Join-Path $projectRoot "runtime\mihomo"
$pidFile = Join-Path $installDir "mihomo.pid"
$expectedExecutable = (Join-Path $installDir "mihomo.exe").ToLowerInvariant()

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "Mihomo PID file does not exist; nothing to stop."
    exit 0
}
$processId = [int](Get-Content -LiteralPath $pidFile -Raw)
$process = Get-Process -Id $processId -ErrorAction SilentlyContinue
if ($process) {
    $actualExecutable = $process.Path.ToLowerInvariant()
    if ($actualExecutable -ne $expectedExecutable) {
        throw "PID $processId does not belong to this project's Mihomo executable."
    }
    Stop-Process -Id $processId
    $process.WaitForExit(5000) | Out-Null
}
Remove-Item -LiteralPath $pidFile -Force
Write-Host "Mihomo stopped."
