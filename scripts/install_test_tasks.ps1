param([Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = (Resolve-Path -LiteralPath $Python).Path
$Runner = Join-Path $PSScriptRoot 'run_scheduled_test.ps1'
$Shell = (Get-Process -Id $PID).Path
$Principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 6) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
foreach ($Mode in @('crawl','send')) {
    $Minute = if ($Mode -eq 'send') { 0 } else { 30 }
    $Now = Get-Date
    $Start = $Now.Date.AddHours($Now.Hour).AddMinutes($Minute)
    if ($Start -le $Now) { $Start = $Start.AddHours(1) }
    $Trigger = New-ScheduledTaskTrigger -Once -At $Start -RepetitionInterval (New-TimeSpan -Hours 1)
    $Arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Mode {1} -Python "{2}"' -f $Runner,$Mode,$Python
    $Action = New-ScheduledTaskAction -Execute $Shell -Argument $Arguments -WorkingDirectory $ProjectRoot
    Register-ScheduledTask -TaskName ("AISecurityBrief-Test-{0}" -f $Mode) -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
}
