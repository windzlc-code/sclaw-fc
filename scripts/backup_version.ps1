param(
  [string]$Label = "",
  [switch]$IncludeData
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$BackupsDir = Join-Path $ProjectRoot "backups"
$StatePath = Join-Path $BackupsDir "version_state.json"
$ManifestPath = Join-Path $BackupsDir "backup_manifest.csv"
$TempRoot = Join-Path $BackupsDir ".tmp_stage"

if (!(Test-Path $BackupsDir)) {
  New-Item -Path $BackupsDir -ItemType Directory | Out-Null
}

if (Test-Path $TempRoot) {
  Remove-Item -Path $TempRoot -Recurse -Force
}
New-Item -Path $TempRoot -ItemType Directory | Out-Null

$state = @{
  current_version = 0
  last_backup_at = ""
  last_backup_file = ""
}

if (Test-Path $StatePath) {
  try {
    $loaded = Get-Content -Path $StatePath -Raw | ConvertFrom-Json
    $state.current_version = [int]($loaded.current_version)
    $state.last_backup_at = [string]($loaded.last_backup_at)
    $state.last_backup_file = [string]($loaded.last_backup_file)
  } catch {
    # Keep default state if file parsing fails.
  }
}

$nextVersion = $state.current_version + 1
$versionTag = "v{0:D4}" -f $nextVersion
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$safeLabel = ($Label -replace "[^a-zA-Z0-9_-]", "-").Trim("-")
$nameParts = @($versionTag, $stamp)
if ($safeLabel) {
  $nameParts += $safeLabel
}
$backupBaseName = ($nameParts -join "_")
$zipPath = Join-Path $BackupsDir ($backupBaseName + ".zip")
$stagePath = Join-Path $TempRoot $backupBaseName
New-Item -Path $stagePath -ItemType Directory | Out-Null

$excludeDirs = @(
  ".git",
  ".venv",
  "__pycache__",
  "backups",
  "logs"
)
if (-not $IncludeData) {
  $excludeDirs += @("data", "exports")
}
$source = (Resolve-Path $ProjectRoot).Path
$destination = (Resolve-Path $stagePath).Path
& robocopy $source $destination /E /NFL /NDL /NJH /NJS /NP /XD $excludeDirs /XF "*.pyc" | Out-Null
if ($LASTEXITCODE -gt 7) {
  throw "robocopy failed with exit code $LASTEXITCODE"
}

Compress-Archive -Path (Join-Path $stagePath "*") -DestinationPath $zipPath -Force

$state.current_version = $nextVersion
$state.last_backup_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
$state.last_backup_file = [IO.Path]::GetFileName($zipPath)
$state | ConvertTo-Json | Set-Content -Path $StatePath -Encoding UTF8

if (!(Test-Path $ManifestPath)) {
  "version,timestamp,file,label,include_data" | Set-Content -Path $ManifestPath -Encoding UTF8
}
$line = "{0},{1},{2},{3},{4}" -f $versionTag, (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), [IO.Path]::GetFileName($zipPath), $safeLabel, [bool]$IncludeData
Add-Content -Path $ManifestPath -Value $line -Encoding UTF8

if (Test-Path $TempRoot) {
  Remove-Item -Path $TempRoot -Recurse -Force
}

Write-Host ("Backup completed: {0}" -f $zipPath)
Write-Host ("Version: {0}" -f $versionTag)
