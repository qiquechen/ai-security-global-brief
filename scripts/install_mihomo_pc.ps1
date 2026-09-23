param(
    [string]$Version = "v1.19.31"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$installDir = Join-Path $projectRoot "runtime\mihomo"
$archiveName = "mihomo-windows-amd64-compatible-$Version.zip"
$downloadUrl = "https://github.com/MetaCubeX/mihomo/releases/download/$Version/$archiveName"
$archivePath = Join-Path $installDir $archiveName
$extractDir = Join-Path $installDir "extract"
$executable = Join-Path $installDir "mihomo.exe"

New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Write-Host "Downloading Mihomo $Version from the official release..."
Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath -UseBasicParsing

if (Test-Path -LiteralPath $extractDir) {
    Remove-Item -LiteralPath $extractDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
Expand-Archive -LiteralPath $archivePath -DestinationPath $extractDir -Force
$downloadedExe = Get-ChildItem -LiteralPath $extractDir -Filter "*.exe" -File | Select-Object -First 1
if (-not $downloadedExe) {
    throw "Downloaded archive does not contain a Windows executable."
}
Copy-Item -LiteralPath $downloadedExe.FullName -Destination $executable -Force
Remove-Item -LiteralPath $extractDir -Recurse -Force
Remove-Item -LiteralPath $archivePath -Force

$template = Join-Path $projectRoot "deploy\mihomo\config.example.yaml"
$localConfig = Join-Path $installDir "config.yaml"
if (-not (Test-Path -LiteralPath $localConfig)) {
    Copy-Item -LiteralPath $template -Destination $localConfig
    Write-Host "Created private config: $localConfig"
}

& $executable -v
Write-Host "Installed: $executable"
Write-Host "Next: edit runtime\mihomo\config.yaml, then run scripts\start_mihomo_pc.ps1"
