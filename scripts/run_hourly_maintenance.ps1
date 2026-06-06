param(
  [int]$MaxRetry = 1,
  [int]$RetryWaitSeconds = 20,
  [int]$PerSourceLimit = 24,
  [int]$QcLimit = 80,
  [int]$BackfillLimit = 120,
  [int]$HomesCleanLimit = 12000,
  [int]$HomesRepairLimit = 12,
  [int]$ZeroCellMaxCells = 8,
  [switch]$EnableZeroCellAutofill = $true,
  [switch]$EnableHomesMediaRepair = $true,
  [switch]$EnableTransitSync = $true,
  [int]$TransitHomesBatch = 1,
  [int]$TransitSuumoBatch = 2,
  [int]$TransitMaxLines = 0,
  [switch]$EnableTransitBackfill = $true,
  [int]$TransitBackfillLimit = 1200,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$LogsDir = Join-Path $ProjectRoot "logs"
$VenvPython = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"
$FallbackPython = "python"

if (!(Test-Path $LogsDir)) {
  New-Item -Path $LogsDir -ItemType Directory | Out-Null
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogsDir ("hourly_maintenance_" + $stamp + ".log")

function Write-Log {
  param([string]$Message)
  $line = ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message)
  Add-Content -Path $LogFile -Value $line -Encoding UTF8
  Write-Host $line
}

function Write-ProcessOutput {
  param([object]$Output)
  $line = [string]$Output
  Add-Content -Path $LogFile -Value $line -Encoding UTF8
  Write-Host $line
}

function Get-PythonCommand {
  if (Test-Path $VenvPython) {
    return $VenvPython
  }
  return $FallbackPython
}

function Resolve-PerSourceLimit {
  param([int]$DefaultLimit)
  # Hourly maintenance should stay bounded; default to a smaller crawl volume even if config has a larger value.
  if ($DefaultLimit -gt 0) { return $DefaultLimit }
  return 24
}

function Invoke-Step {
  param(
    [string]$Name,
    [string]$Command,
    [string[]]$Arguments
  )
  Write-Log "Start step: $Name"
  if ($DryRun) {
    Write-Log "DryRun command: $Command $($Arguments -join ' ')"
    return $true
  }
  # Windows PowerShell 5.1 treats native stderr output as ErrorRecord objects.
  # With $ErrorActionPreference='Stop', redirecting (2>&1) can become script-terminating.
  # Temporarily relax error action preference while running the native command,
  # and rely on the process exit code for pass/fail.
  $prevErrorActionPreference = $ErrorActionPreference
  $nativeExitCode = 0
  try {
    $ErrorActionPreference = "Continue"
    & $Command @Arguments 2>&1 | ForEach-Object { Write-ProcessOutput $_ }
    $nativeExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $prevErrorActionPreference
  }
  if ($nativeExitCode -ne 0) {
    Write-Log "Step failed: $Name (exit code: $nativeExitCode)"
    return $false
  }
  Write-Log "Step finished: $Name"
  return $true
}

function Invoke-WithRetry {
  param(
    [string]$Name,
    [string]$Command,
    [string[]]$Arguments,
    [int]$RetryCount
  )
  for ($i = 0; $i -le $RetryCount; $i++) {
    if ($i -gt 0) {
      Write-Log "Retry #$i for $Name after waiting $RetryWaitSeconds seconds."
      Start-Sleep -Seconds $RetryWaitSeconds
    }
    $ok = Invoke-Step -Name $Name -Command $Command -Arguments $Arguments
    if ($ok) { return $true }
  }
  return $false
}

Set-Location $ProjectRoot
$env:PYTHONPATH = $ProjectRoot
$env:PYTHONUTF8 = "1"

# Speed-focused defaults: keep listings fresh and fill missing fields quickly.
$env:SCLAW_FAST_JP_LISTING_CONTENT = "1"
$env:SCLAW_PORTAL_HUB_CAP = "10"
$env:SCLAW_ATHOME_HUB_CAP = "18"
$env:SCLAW_REMAINING_PORTAL_HUB_CAP = "8"

# SUUMO throttling + depth caps
$env:SCLAW_SUUMO_REQUEST_INTERVAL_SEC = "1.5"
$env:SCLAW_SUUMO_HUB_CAP = "8"
$env:SCLAW_SUUMO_BUKKEN_MAX_PAGES = "4"
$env:SCLAW_SUUMO_CITY_ICHIRAN_CAP = "6"

$Python = Get-PythonCommand
$lim = Resolve-PerSourceLimit -DefaultLimit $PerSourceLimit

Write-Log "Hourly maintenance started in $ProjectRoot"
Write-Log "Using python: $Python"
Write-Log "PerSourceLimit=$lim QcLimit=$QcLimit BackfillLimit=$BackfillLimit HomesCleanLimit=$HomesCleanLimit HomesRepairLimit=$HomesRepairLimit"

$steps = @(
  @{ Name = "Clean broken media fragments"; Args = @("scripts/cleanup_broken_media_text.py", "--apply") },
  @{ Name = "Clean HOMES image_urls contamination"; Args = @("scripts/clean_homes_image_urls_by_tokens.py", "--clear-when-no-match", "--limit", [string]$HomesCleanLimit) },
  @{ Name = "Clean HOMES listing_media_json contamination"; Args = @("scripts/clean_homes_listing_media_json_by_tokens.py", "--clear-when-no-match", "--limit", [string]$HomesCleanLimit) },
  @{ Name = "Sync transit (rotate)"; Enabled = $EnableTransitSync; Args = @("scripts/bot_transit_sync_rotate.py", "--homes-batch", [string]$TransitHomesBatch, "--suumo-batch", [string]$TransitSuumoBatch, "--max-lines", [string]$TransitMaxLines); SoftFail = $true },
  @{ Name = "Backfill transit bindings (limited)"; Enabled = $EnableTransitBackfill; Args = @("scripts/backfill_jp_transit_bindings.py", "--limit", [string]$TransitBackfillLimit); SoftFail = $true },
  @{ Name = "Run crawl + pipeline"; Args = @("scripts/run_pipeline.py", "--per-source-limit", [string]$lim) },
  @{ Name = "Recrawl incomplete listings (QC)"; Args = @("scripts/batch_recrawl_qc.py", "--limit", [string]$QcLimit, "--skip-matrix") },
  @{ Name = "Backfill empty image_urls"; Args = @("scripts/batch_backfill_image_urls.py", "--limit", [string]$BackfillLimit, "--sleep", "0.35") },
  @{ Name = "Backfill + refresh image_urls (force)"; Args = @("scripts/batch_backfill_image_urls.py", "--force", "--limit", "40", "--sleep", "0.45"); SoftFail = $true }
)

if ($EnableZeroCellAutofill) {
  # Focus: SUUMO / HOME'S / at home coverage across all regions.
  $steps += @{ Name = "Autofill SUUMO coverage"; Args = @("scripts/bot_zero_cell_autofill.py", "--scope", "primary", "--only-host", "suumo.jp", "--min-count", "15", "--threshold", "15", "--max-cells", [string]$ZeroCellMaxCells); SoftFail = $true }
  $steps += @{ Name = "Autofill HOMES coverage"; Args = @("scripts/bot_zero_cell_autofill.py", "--scope", "primary", "--only-host", "homes.co.jp", "--min-count", "15", "--threshold", "15", "--max-cells", [string]$ZeroCellMaxCells); SoftFail = $true }
  $steps += @{ Name = "Autofill at home coverage"; Args = @("scripts/bot_zero_cell_autofill.py", "--scope", "primary", "--only-host", "athome.co.jp", "--min-count", "15", "--threshold", "15", "--max-cells", [string]$ZeroCellMaxCells); SoftFail = $true }
}

if ($EnableHomesMediaRepair) {
  # Playwright / WAF may fail depending on environment; treat as best-effort.
  $steps += @{ Name = "Repair HOMES media via Playwright"; Args = @("scripts/repair_homes_media.py", "--limit", [string]$HomesRepairLimit); SoftFail = $true }
}

foreach ($s in $steps) {
  if ($s.ContainsKey("Enabled") -and (-not [bool]$s.Enabled)) {
    Write-Log "Skip step: $($s.Name) (disabled)"
    continue
  }
  $ok = Invoke-WithRetry -Name $s.Name -Command $Python -Arguments $s.Args -RetryCount $MaxRetry
  if (-not $ok) {
    if ($s.SoftFail) {
      Write-Log "Soft-fail step: $($s.Name) (continue)"
      continue
    }
    Write-Log "Hourly maintenance failed at step: $($s.Name)"
    exit 1
  }
}

Write-Log "Hourly maintenance completed successfully."
exit 0
