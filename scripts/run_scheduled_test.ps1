param(
    [Parameter(Mandatory=$true)][ValidateSet('crawl','send')][string]$Mode,
    [Parameter(Mandatory=$true)][string]$Python
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$LogDirectory = Join-Path $ProjectRoot 'logs'
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$LogPath = Join-Path $LogDirectory ("test-{0}-{1}.log" -f $Mode,(Get-Date -Format 'yyyyMMdd-HHmmss'))
if ($Mode -eq 'crawl') {
    # Bind to the intended next hour before crawling; a slow run cannot become a later batch.
    $Slot = (Get-Date).AddHours(1).ToString('yyyyMMdd-HH')
    $ReadyFile = Join-Path $ProjectRoot ("data/ready-test-{0}.txt" -f $Slot)
    & $Python 'scripts/run_crawl.py' *> $LogPath
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Python 'scripts/run_daily.py' --input db --hours 48 --ready-file $ReadyFile *>> $LogPath
} else {
    $ReadyFile = Join-Path $ProjectRoot ("data/ready-test-{0}.txt" -f (Get-Date -Format 'yyyyMMdd-HH'))
    & $Python 'scripts/send_ready.py' --ready-file $ReadyFile *> $LogPath
}
exit $LASTEXITCODE
