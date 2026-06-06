param(
  [string]$TaskName = "SCLAW_Hourly_Maintenance",
  [string]$StartTime = "00:00",
  [string]$RunScript = "",
  [switch]$Force
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..")
$ConfigPath = Join-Path $ProjectRoot "config\crawl_settings.json"
$SetupScript = Join-Path $ProjectRoot "scripts\setup_scheduler.ps1"
$DefaultRunScript = Join-Path $ProjectRoot "scripts\run_hourly_maintenance.ps1"

if (!(Test-Path $ConfigPath)) {
  throw "Missing config file: $ConfigPath"
}

$cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$interval = [int]$cfg.interval_hours
if ($interval -lt 1) { $interval = 2 }

$args = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", $SetupScript,
  "-TaskName", $TaskName,
  "-IntervalHours", "$interval",
  "-StartTime", $StartTime
)
$ResolvedRunScript = $RunScript
if (-not $ResolvedRunScript) { $ResolvedRunScript = $DefaultRunScript }
if ($ResolvedRunScript) { $args += @("-RunScript", $ResolvedRunScript) }
if ($Force) { $args += "-Force" }

& powershell @args
