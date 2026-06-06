param(
    [int[]]$WaitForProcessIds = @(),
    [string]$PythonPath = ".\.venv\Scripts\python.exe",
    [string]$Modes = "mansion,house",
    [int]$StartIndex = 1,
    [int]$Pages = 20,
    [int]$PerTarget = 1500,
    [int]$ChunkSize = 150,
    [double]$ChunkSleepSec = 0.05,
    [double]$TargetSleepSec = 1.0,
    [string]$LogPrefix = "expand_homes_paged_deep"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$SupervisorTs = Get-Date -Format "yyyyMMdd_HHmmss"
$SupervisorLog = Join-Path $Root ("data\{0}_supervisor_{1}.log" -f $LogPrefix, $SupervisorTs)

function Write-SupervisorLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $Message
    Add-Content -Path $SupervisorLog -Value $line -Encoding UTF8
}

Write-SupervisorLog ("root={0}" -f $Root)
foreach ($pidToWait in $WaitForProcessIds) {
    $proc = Get-Process -Id $pidToWait -ErrorAction SilentlyContinue
    if ($null -ne $proc) {
        Write-SupervisorLog ("waiting pid={0} started={1}" -f $pidToWait, $proc.StartTime)
        Wait-Process -Id $pidToWait
        Write-SupervisorLog ("finished pid={0}" -f $pidToWait)
    }
}

$Ts = Get-Date -Format "yyyyMMdd_HHmmss"
$env:PYTHONUTF8 = "1"
$env:SCLAW_HOMES_LIST_PAGES = [string]$Pages
$env:SCLAW_FAST_JP_LISTING_CONTENT = "1"
$env:SCLAW_HOMES_PROGRESS_LOG = "1"

$OutLog = Join-Path $Root ("data\{0}_{1}.out.log" -f $LogPrefix, $Ts)
$ErrLog = Join-Path $Root ("data\{0}_{1}.err.log" -f $LogPrefix, $Ts)
$Report = Join-Path $Root ("data\{0}_{1}.json" -f $LogPrefix, $Ts)

$Args = @(
    "scripts\expand_homes_paged_snippets.py",
    "--modes", $Modes,
    "--start-index", [string]$StartIndex,
    "--per-target", [string]$PerTarget,
    "--chunk-size", [string]$ChunkSize,
    "--chunk-sleep-sec", [string]$ChunkSleepSec,
    "--target-sleep-sec", [string]$TargetSleepSec,
    "--write-report", $Report
)

$child = Start-Process `
    -FilePath $PythonPath `
    -ArgumentList $Args `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru `
    -WindowStyle Hidden

Write-SupervisorLog ("started pid={0} pages={1} per_target={2} out={3} err={4} report={5}" -f $child.Id, $Pages, $PerTarget, $OutLog, $ErrLog, $Report)
