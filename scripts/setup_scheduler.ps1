param(
  [string]$TaskName = "SCLAW_Every2Hours_Pipeline",
  [int]$IntervalHours = 2,
  [string]$StartTime = "00:00",
  [string]$RunScript = "",
  [switch]$Force
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$ResolvedRunScript = $RunScript
if (-not $ResolvedRunScript) {
  $ResolvedRunScript = Join-Path $ProjectRoot "scripts\run_daily.ps1"
}
$ResolvedRunScript = [string]$ResolvedRunScript
if (-not [System.IO.Path]::IsPathRooted($ResolvedRunScript)) {
  $ResolvedRunScript = Join-Path $ProjectRoot $ResolvedRunScript
}
if (!(Test-Path $ResolvedRunScript)) {
  throw "Cannot find run script: $ResolvedRunScript"
}
$ResolvedRunScript = (Resolve-Path $ResolvedRunScript).Path
$PowerShellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

$taskExists = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($taskExists) {
  if ($Force) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  } else {
    throw "Task '$TaskName' already exists. Re-run with -Force to replace."
  }
}

$action = New-ScheduledTaskAction `
  -Execute $PowerShellExe `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ResolvedRunScript`""

try {
  $dt = [DateTime]::ParseExact($StartTime, "HH:mm", $null)
} catch {
  throw "Invalid StartTime '$StartTime'. Use HH:mm, e.g. 00:00"
}

if ($IntervalHours -lt 1) {
  throw "IntervalHours must be >= 1"
}

$trigger = New-ScheduledTaskTrigger `
  -Once `
  -At $dt `
  -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
  -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Description "SCLAW crawler + translation + WP export pipeline every 2 hours"

Write-Host "Scheduled task created: $TaskName"
Write-Host "Repeats every $IntervalHours hour(s), starting at $StartTime"
Write-Host "You can run it now with: Start-ScheduledTask -TaskName `"$TaskName`""
