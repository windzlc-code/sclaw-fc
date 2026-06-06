# Diagnose which process listens on TCP port 8000 (any address; avoids IPv4 vs IPv6 mismatch).
$ErrorActionPreference = "Continue"
Write-Host "=== TCP LISTEN on port 8000 (all addresses) ===" -ForegroundColor Cyan
$rows = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $rows) {
    Write-Host "(No LISTEN on 8000, or admin rights required to see connections.)" -ForegroundColor Yellow
    exit 0
}
$rows | Select-Object LocalAddress, LocalPort, OwningProcess, State | Format-Table -AutoSize
$pids = $rows | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $pids) {
    Write-Host ""
    Write-Host "PID: $procId" -ForegroundColor Green
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($p) {
        Write-Host "  ProcessName: $($p.ProcessName)"
        Write-Host "  Path: $($p.Path)"
    } else {
        Write-Host "  (Get-Process failed for this PID.)" -ForegroundColor DarkYellow
    }
    $filter = "ProcessId = $procId"
    $cim = $null
    try {
        $cim = Get-CimInstance -ClassName Win32_Process -Filter $filter -ErrorAction Stop
    } catch {
        Write-Host "  CommandLine: (CIM error: $($_.Exception.Message))" -ForegroundColor DarkYellow
    }
    if ($cim -and $cim.CommandLine) {
        Write-Host "  CommandLine:"
        Write-Host "  $($cim.CommandLine)"
    } elseif ($cim -and -not $cim.CommandLine) {
        Write-Host "  CommandLine: (empty)" -ForegroundColor DarkYellow
    } elseif (-not $cim) {
        Write-Host "  CommandLine: (not available; try elevated PowerShell)" -ForegroundColor DarkYellow
    }
}
Write-Host ""
Write-Host "If CommandLine is not this project's .venv + uvicorn app:app, port 8000 is another app." -ForegroundColor Yellow
Write-Host "To free the port, run (copy one line per PID):" -ForegroundColor Yellow
foreach ($procId in $pids) {
    Write-Host ("  Stop-Process -Id {0} -Force" -f $procId) -ForegroundColor Gray
}
Write-Host ""
Write-Host "Then start from project folder:" -ForegroundColor Cyan
$proj = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $proj "app.py"))) {
    $proj = "C:\Users\User\Desktop\房地產\SCLAW"
}
Write-Host "  cd `"$proj`"" -ForegroundColor Gray
Write-Host '  $env:PYTHONPATH = (Get-Location).Path' -ForegroundColor Gray
Write-Host '  .\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000' -ForegroundColor Gray
