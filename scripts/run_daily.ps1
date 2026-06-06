param(
  [int]$MaxRetry = 2,
  [int]$RetryWaitSeconds = 30,
  [int]$PerSourceLimit = 0,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$LogsDir = Join-Path $ProjectRoot "logs"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$FallbackPython = "python"

if (!(Test-Path $LogsDir)) {
  New-Item -Path $LogsDir -ItemType Directory | Out-Null
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogsDir ("pipeline_" + $stamp + ".log")

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

  & $Command @Arguments 2>&1 | ForEach-Object { Write-ProcessOutput $_ }
  if ($LASTEXITCODE -ne 0) {
    Write-Log "Step failed: $Name (exit code: $LASTEXITCODE)"
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
    if ($ok) {
      return $true
    }
  }
  return $false
}

Set-Location $ProjectRoot
$env:PYTHONPATH = $ProjectRoot
$Python = Get-PythonCommand

Write-Log "Pipeline started in $ProjectRoot"
Write-Log "Using python: $Python"

$RunPipelineArgs = @("scripts/run_pipeline.py")
if ($PerSourceLimit -gt 0) {
  $RunPipelineArgs += @("--per-source-limit", [string]$PerSourceLimit)
}

$steps = @(
  @{ Name = "Ingest manual links"; Args = @("scripts/ingest_manual_links.py") },
  @{ Name = "Run content pipeline"; Args = $RunPipelineArgs },
  @{ Name = "Run daily case image and quality maintenance"; Args = @("scripts/run_daily_case_quality.py") },
  @{ Name = "Generate SEO drafts from hot keywords"; Args = @("scripts/generate_seo_drafts.py") },
  @{ Name = "Export WordPress CSV"; Args = @("scripts/export_wordpress_csv.py") }
)

foreach ($s in $steps) {
  $ok = Invoke-WithRetry -Name $s.Name -Command $Python -Arguments $s.Args -RetryCount $MaxRetry
  if (-not $ok) {
    Write-Log "Pipeline failed at step: $($s.Name)"
    exit 1
  }
}

Write-Log "Pipeline completed successfully."
exit 0
