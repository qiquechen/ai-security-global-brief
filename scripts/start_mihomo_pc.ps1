param(
    [switch]$Smoke
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$installDir = Join-Path $projectRoot "runtime\mihomo"
$executable = Join-Path $installDir "mihomo.exe"
$config = if ($Smoke) {
    Join-Path $projectRoot "deploy\mihomo\config.smoke.yaml"
} else {
    Join-Path $installDir "config.yaml"
}
$pidFile = Join-Path $installDir "mihomo.pid"
$stdoutLog = Join-Path $installDir "mihomo.stdout.log"
$stderrLog = Join-Path $installDir "mihomo.stderr.log"

if (-not (Test-Path -LiteralPath $executable)) {
    throw "Mihomo is not installed. Run scripts\install_mihomo_pc.ps1 first."
}
if (-not (Test-Path -LiteralPath $config)) {
    throw "Configuration not found: $config"
}
if (-not $Smoke) {
    $content = Get-Content -LiteralPath $config -Raw
    $primaryMatch = [regex]::Match(
        $content,
        '(?ms)^  primary-provider:\r?\n.*?^    url: "(?<url>[^"]+)"'
    )
    $backupMatch = [regex]::Match(
        $content,
        '(?ms)^  backup-provider:\r?\n.*?^    url: "(?<url>[^"]+)"'
    )
    $secretMatch = [regex]::Match($content, '(?m)^secret: "(?<value>[^"]+)"\s*$')
    $primaryUrl = if ($primaryMatch.Success) { $primaryMatch.Groups['url'].Value } else { "" }
    $backupUrl = if ($backupMatch.Success) { $backupMatch.Groups['url'].Value } else { "" }
    $secretValue = if ($secretMatch.Success) { $secretMatch.Groups['value'].Value } else { "" }
    $primaryValid = $primaryUrl.StartsWith("https://") -or $primaryUrl.StartsWith("http://")
    $backupValid = $backupUrl.StartsWith("https://") -or $backupUrl.StartsWith("http://")
    if (-not $primaryValid -or -not $backupValid -or $secretValue.Length -lt 32) {
        throw "Mihomo config is incomplete. Set both provider URLs and a secret of at least 32 characters."
    }
}
if (Test-Path -LiteralPath $pidFile) {
    $oldPid = [int](Get-Content -LiteralPath $pidFile -Raw)
    if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
        throw "Mihomo is already running with PID $oldPid."
    }
    Remove-Item -LiteralPath $pidFile -Force
}

& $executable -t -d $installDir -f $config
if ($LASTEXITCODE -ne 0) {
    throw "Mihomo configuration validation failed."
}

$arguments = @("-d", "`"$installDir`"", "-f", "`"$config`"")
$process = Start-Process -FilePath $executable `
    -ArgumentList $arguments `
    -WorkingDirectory $installDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru
$process.Id | Set-Content -LiteralPath $pidFile -Encoding ascii
Start-Sleep -Seconds 2
if (-not (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
    throw "Mihomo exited during startup. Check $stderrLog"
}
Write-Host "Mihomo started. PID=$($process.Id), proxy=http://127.0.0.1:17890"
