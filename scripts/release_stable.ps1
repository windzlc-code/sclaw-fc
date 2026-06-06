param(
  [string]$Label = "stable-release",
  [int]$HomesLimit = 200000,
  [switch]$SkipBackup,
  [switch]$IncludeData
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$VenvPython = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"

Set-Location $ProjectRoot
$env:PYTHONPATH = $ProjectRoot
$env:PYTHONIOENCODING = "utf-8"

$py = "python"
if (Test-Path $VenvPython) {
  $py = $VenvPython
}

Write-Host "SCLAW release preflight starting..." -ForegroundColor Cyan
Write-Host ("Project: {0}" -f $ProjectRoot)
Write-Host ("Python:   {0}" -f $py)
Write-Host ""

Write-Host "1) Cleaning HOMES media contamination..." -ForegroundColor Cyan
& $py scripts/clean_homes_image_urls_by_tokens.py --clear-when-no-match --limit $HomesLimit
& $py scripts/clean_homes_listing_media_json_by_tokens.py --clear-when-no-match --limit $HomesLimit

Write-Host ""
Write-Host "2) Running release preflight checks..." -ForegroundColor Cyan
$preflightJson = & $py scripts/preflight_release.py --json
if (-not $preflightJson) { throw "preflight_release.py returned empty output" }
$preflight = $preflightJson | ConvertFrom-Json
Write-Host $preflightJson
if (-not $preflight.ok) {
  throw "Preflight failed (ok=false). Fix findings before going live."
}

Write-Host ""
Write-Host "3) Creating backup artifact..." -ForegroundColor Cyan
if ($SkipBackup) {
  Write-Host "SkipBackup set; no zip created." -ForegroundColor Yellow
} else {
  $backupArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/backup_version.ps1", "-Label", $Label)
  if ($IncludeData) { $backupArgs += "-IncludeData" }
  & powershell @backupArgs
}

Write-Host ""
Write-Host "Release preflight complete (ok=true). Ready to deploy." -ForegroundColor Green

