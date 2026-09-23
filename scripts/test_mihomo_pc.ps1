param(
    [string]$TestUrl = "https://www.gstatic.com/generate_204"
)

$ErrorActionPreference = "Stop"
$proxy = "http://127.0.0.1:17890"
$portResult = Test-NetConnection -ComputerName 127.0.0.1 -Port 17890 -WarningAction SilentlyContinue
if (-not $portResult.TcpTestSucceeded) {
    throw "Mihomo proxy port 17890 is not accepting connections."
}
$timer = [System.Diagnostics.Stopwatch]::StartNew()
$response = Invoke-WebRequest -Uri $TestUrl -Proxy $proxy -TimeoutSec 15 -UseBasicParsing
$timer.Stop()
if ($response.StatusCode -ge 500) {
    throw "Proxy connectivity test returned HTTP $($response.StatusCode)."
}
Write-Host "Proxy OK: HTTP $($response.StatusCode), $($timer.ElapsedMilliseconds) ms, $TestUrl"
