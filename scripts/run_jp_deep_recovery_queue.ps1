param(
    [int[]]$WaitForProcessIds = @(),
    [string]$PythonPath = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe",
    [int]$PollSeconds = 60,
    [int]$MaxWaitHours = 18
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$QueueTs = Get-Date -Format "yyyyMMdd_HHmmss"
$QueueLog = Join-Path $Root ("data\jp_deep_recovery_queue_{0}.log" -f $QueueTs)

function Write-QueueLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $Message
    Add-Content -Path $QueueLog -Value $line -Encoding UTF8
}

function Get-JpListingCount {
    $code = "import sqlite3; conn=sqlite3.connect('data/jp_real_estate.sqlite3', timeout=60); print(conn.execute(""select count(*) from source_items where content_kind='jp_listing'"").fetchone()[0])"
    $raw = & $PythonPath -c $code
    return [int]($raw | Select-Object -Last 1)
}

function Get-RunningWriterProcesses {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "python.exe" -and
            ($_.CommandLine -like "*scripts\expand_homes_paged_snippets.py*" -or
             $_.CommandLine -like "*scripts/expand_homes_paged_snippets.py*" -or
             $_.CommandLine -like "*scripts\expand_three_portals.py*" -or
             $_.CommandLine -like "*scripts/expand_three_portals.py*" -or
             $_.CommandLine -like "*scripts\expand_yahoo_targets.py*" -or
             $_.CommandLine -like "*scripts/expand_yahoo_targets.py*" -or
             $_.CommandLine -like "*scripts\seed_japan_shopping_knowledge.py*" -or
             $_.CommandLine -like "*scripts/seed_japan_shopping_knowledge.py*")
        }
}

function Wait-ForProcessIds {
    foreach ($pidToWait in $WaitForProcessIds) {
        $proc = Get-Process -Id $pidToWait -ErrorAction SilentlyContinue
        if ($null -ne $proc) {
            Write-QueueLog ("waiting pid={0} started={1}" -f $pidToWait, $proc.StartTime)
            Wait-Process -Id $pidToWait
            Write-QueueLog ("finished pid={0}" -f $pidToWait)
        }
    }
}

function Wait-ForNoWriters {
    param([int[]]$IgnoreProcessIds = @())
    $deadline = (Get-Date).AddHours([Math]::Max(1, $MaxWaitHours))
    while ((Get-Date) -lt $deadline) {
        $running = @(Get-RunningWriterProcesses | Where-Object { $IgnoreProcessIds -notcontains $_.ProcessId })
        if ($running.Count -eq 0) {
            Write-QueueLog "no expansion/seed writer process is running"
            return
        }
        $ids = ($running | ForEach-Object { $_.ProcessId }) -join ","
        Write-QueueLog ("waiting writer pids={0}" -f $ids)
        Start-Sleep -Seconds ([Math]::Max(10, $PollSeconds))
    }
    throw "Timed out waiting for writer processes to finish."
}

function Start-TrackedPython {
    param(
        [string]$Name,
        [string[]]$Arguments,
        [hashtable]$Environment = @{}
    )

    foreach ($entry in $Environment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable([string]$entry.Key, [string]$entry.Value, "Process")
    }

    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    $outLog = Join-Path $Root ("data\{0}_{1}.out.log" -f $Name, $ts)
    $errLog = Join-Path $Root ("data\{0}_{1}.err.log" -f $Name, $ts)

    Write-QueueLog ("starting {0} out={1} err={2}" -f $Name, $outLog, $errLog)
    $child = Start-Process `
        -FilePath $PythonPath `
        -ArgumentList $Arguments `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru `
        -WindowStyle Hidden
    Write-QueueLog ("started {0} pid={1}" -f $Name, $child.Id)
    Wait-Process -Id $child.Id
    $child.Refresh()
    Write-QueueLog ("finished {0} pid={1} exit={2}" -f $Name, $child.Id, $child.ExitCode)
}

Write-QueueLog ("root={0}" -f $Root)
Wait-ForProcessIds
Wait-ForNoWriters

$homesReport = "data\expand_homes_paged_deep_recovery_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss")
Start-TrackedPython `
    -Name "expand_homes_paged_deep_recovery" `
    -Environment @{
        PYTHONUTF8 = "1"
        SCLAW_HOMES_LIST_PAGES = "20"
        SCLAW_HOMES_PROGRESS_LOG = "1"
        SCLAW_FAST_JP_LISTING_CONTENT = "1"
    } `
    -Arguments @(
        "scripts\expand_homes_paged_snippets.py",
        "--modes", "mansion,house",
        "--start-index", "1",
        "--per-target", "1500",
        "--chunk-size", "150",
        "--chunk-sleep-sec", "0.05",
        "--target-sleep-sec", "1",
        "--write-report", $homesReport
    )

Start-TrackedPython `
    -Name "seed_japan_shopping_knowledge_recovery" `
    -Environment @{ PYTHONUTF8 = "1" } `
    -Arguments @(
        "scripts\seed_japan_shopping_knowledge.py",
        "--target-count", "420",
        "--batch-size", "36",
        "--skip-init"
    )

$suumoReport = "data\expand_suumo_lowfreq_recovery_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss")
Start-TrackedPython `
    -Name "expand_suumo_lowfreq_recovery" `
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
        "--write-report", $suumoReport
    )

$totalAfterCore = Get-JpListingCount
Write-QueueLog ("total_after_core={0}" -f $totalAfterCore)
if ($totalAfterCore -lt 30000) {
    $yahooReport = "data\expand_yahoo_buy_recovery_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss")
    Start-TrackedPython `
        -Name "expand_yahoo_buy_recovery" `
        -Environment @{
            PYTHONUTF8 = "1"
            SCLAW_FAST_JP_LISTING_CONTENT = "1"
        } `
        -Arguments @(
            "scripts\expand_yahoo_targets.py",
            "--types", "new-house,used-house,used-mansion",
            "--codes", "major",
            "--per-source", "900",
            "--chunk-size", "60",
            "--sleep-sec", "0.02",
            "--write-report", $yahooReport
        )
}

$diagReport = "data\site_diag_after_deep_recovery_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss")
Start-TrackedPython `
    -Name "site_diag_after_deep_recovery" `
    -Environment @{
        PYTHONUTF8 = "1"
        SCLAW_FAST_JP_LISTING_CONTENT = "1"
    } `
    -Arguments @(
        "scripts\site_intelligent_diagnosis.py",
        "--sync-media-limit", "300",
        "--repair-text-limit", "300",
        "--rebuild-fts",
        "--write-report", $diagReport
    )

Write-QueueLog "deep recovery queue completed"
