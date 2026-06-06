param(
    [int[]]$WaitForProcessIds = @(),
    [string]$PythonPath = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe",
    [int]$TargetCount = 50000
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$QueueTs = Get-Date -Format "yyyyMMdd_HHmmss"
$QueueLog = Join-Path $Root ("data\jp_after_homes_manual_queue_{0}.log" -f $QueueTs)

function Write-QueueLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $Message
    Add-Content -Path $QueueLog -Value $line -Encoding UTF8
}

function Set-RunEnvironment {
    param([hashtable]$Environment = @{})
    foreach ($entry in $Environment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable([string]$entry.Key, [string]$entry.Value, "Process")
    }
}

function Get-JpListingCount {
    $code = "import sqlite3; conn=sqlite3.connect('data/jp_real_estate.sqlite3', timeout=60); print(conn.execute(""select count(*) from source_items where content_kind='jp_listing'"").fetchone()[0])"
    $raw = & $PythonPath -c $code
    return [int]($raw | Select-Object -Last 1)
}

function Invoke-LoggedPython {
    param(
        [string]$Name,
        [string[]]$Arguments,
        [hashtable]$Environment = @{}
    )
    Set-RunEnvironment $Environment
    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    $outLog = Join-Path $Root ("data\{0}_{1}.out.log" -f $Name, $ts)
    $errLog = Join-Path $Root ("data\{0}_{1}.err.log" -f $Name, $ts)
    Write-QueueLog ("running {0} out={1} err={2}" -f $Name, $outLog, $errLog)
    & $PythonPath @Arguments > $outLog 2> $errLog
    $exitCode = $LASTEXITCODE
    Write-QueueLog ("finished {0} exit={1}" -f $Name, $exitCode)
    if ($exitCode -ne 0) {
        Write-QueueLog ("nonzero exit for {0}; continuing to next safe step" -f $Name)
    }
}

Write-QueueLog ("root={0}" -f $Root)
foreach ($pidToWait in $WaitForProcessIds) {
    $proc = Get-Process -Id $pidToWait -ErrorAction SilentlyContinue
    if ($null -ne $proc) {
        Write-QueueLog ("waiting pid={0} started={1}" -f $pidToWait, $proc.StartTime)
        Wait-Process -Id $pidToWait
        Write-QueueLog ("finished pid={0}" -f $pidToWait)
    }
}

Invoke-LoggedPython `
    -Name "seed_japan_shopping_knowledge_after_homes" `
    -Environment @{ PYTHONUTF8 = "1" } `
    -Arguments @(
        "scripts\seed_japan_shopping_knowledge.py",
        "--target-count", "420",
        "--batch-size", "36",
        "--skip-init"
    )

Invoke-LoggedPython `
    -Name "expand_suumo_lowfreq_after_homes" `
    -Environment @{
        PYTHONUTF8 = "1"
        SCLAW_FAST_JP_LISTING_CONTENT = "1"
        SCLAW_PROCESS_CHUNK_SLEEP_SEC = "0.2"
        SCLAW_SUUMO_IGNORE_COOLDOWN = "1"
        SCLAW_SUUMO_LISTING_SNIPPET_ONLY = "1"
        SCLAW_SUUMO_REQUEST_INTERVAL_SEC = "8"
        SCLAW_SUUMO_PAGE_SLEEP = "2"
    } `
    -Arguments @(
        "scripts\expand_three_portals.py",
        "--portals", "suumo",
        "--modes", "mansion,house",
        "--per-target", "80",
        "--max-targets", "6",
        "--chunk-size", "20",
        "--sleep-sec", "0.3",
        "--target-sleep-sec", "8",
        "--suumo-pref-limit", "3",
        "--suumo-child-pages", "12",
        "--skip-js-disabled",
        "--write-report", ("data\expand_suumo_lowfreq_after_homes_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
    )

$totalAfterCore = Get-JpListingCount
Write-QueueLog ("total_after_core={0} target={1}" -f $totalAfterCore, $TargetCount)
if ($totalAfterCore -lt $TargetCount) {
    Invoke-LoggedPython `
        -Name "expand_yahoo_buy_after_homes" `
        -Environment @{
            PYTHONUTF8 = "1"
            SCLAW_FAST_JP_LISTING_CONTENT = "1"
        } `
        -Arguments @(
            "scripts\expand_yahoo_targets.py",
            "--types", "new-house,used-house,used-mansion",
            "--codes", "all",
            "--per-source", "900",
            "--chunk-size", "60",
            "--sleep-sec", "0.02",
            "--write-report", ("data\expand_yahoo_buy_after_homes_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
        )
}

Invoke-LoggedPython `
    -Name "site_diag_after_homes_queue" `
    -Environment @{
        PYTHONUTF8 = "1"
        SCLAW_FAST_JP_LISTING_CONTENT = "1"
    } `
    -Arguments @(
        "scripts\site_intelligent_diagnosis.py",
        "--sync-media-limit", "300",
        "--repair-text-limit", "300",
        "--rebuild-fts",
        "--write-report", ("data\site_diag_after_homes_queue_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
    )

Write-QueueLog "after-HOME'S manual queue completed"
