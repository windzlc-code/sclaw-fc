param(
    [int[]]$WaitForProcessIds = @(),
    [string]$PythonPath = ".\.venv\Scripts\python.exe",
    [int]$PollSeconds = 60,
    [int]$MaxWaitHours = 18
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$QueueTs = Get-Date -Format "yyyyMMdd_HHmmss"
$QueueLog = Join-Path $Root ("data\jp_followup_queue_{0}.log" -f $QueueTs)

function Write-QueueLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $Message
    Add-Content -Path $QueueLog -Value $line -Encoding UTF8
}

function Get-HomesExpansionProcesses {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "python.exe" -and
            ($_.CommandLine -like "*scripts\expand_homes_paged_snippets.py*" -or
             $_.CommandLine -like "*scripts/expand_homes_paged_snippets.py*")
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

function Wait-ForNoHomesExpansion {
    $deadline = (Get-Date).AddHours([Math]::Max(1, $MaxWaitHours))
    while ((Get-Date) -lt $deadline) {
        $running = @(Get-HomesExpansionProcesses)
        if ($running.Count -eq 0) {
            Write-QueueLog "no HOME'S expansion process is running"
            return
        }
        $ids = ($running | ForEach-Object { $_.ProcessId }) -join ","
        Write-QueueLog ("waiting HOME'S expansion pids={0}" -f $ids)
        Start-Sleep -Seconds ([Math]::Max(10, $PollSeconds))
    }
    throw "Timed out waiting for HOME'S expansion to finish."
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
Wait-ForNoHomesExpansion

$athomeReport = "data\expand_athome_buy_followup_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss")
Start-TrackedPython `
    -Name "expand_athome_buy_followup" `
    -Environment @{
        PYTHONUTF8 = "1"
        SCLAW_FAST_JP_LISTING_CONTENT = "1"
        SCLAW_PROCESS_CHUNK_SLEEP_SEC = "0.05"
    } `
    -Arguments @(
        "scripts\expand_three_portals.py",
        "--portals", "athome",
        "--modes", "mansion,house",
        "--per-target", "300",
        "--chunk-size", "80",
        "--sleep-sec", "0.03",
        "--target-sleep-sec", "1",
        "--skip-js-disabled",
        "--write-report", $athomeReport
    )

$suumoReport = "data\expand_suumo_lowfreq_followup_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss")
Start-TrackedPython `
    -Name "expand_suumo_lowfreq_followup" `
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

$diagReport = "data\site_diag_after_followups_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss")
Start-TrackedPython `
    -Name "site_diag_after_followups" `
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

Write-QueueLog "follow-up queue completed"
