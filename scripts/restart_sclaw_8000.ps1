# Free TCP port 8000, then start this repo's uvicorn with explicit --app-dir (avoids wrong app on :8000).
$ErrorActionPreference = "Continue"
$projRoot = Split-Path -Parent $PSScriptRoot
$py = Join-Path $projRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ERROR: venv not found: $py" -ForegroundColor Red
    exit 1
}
$appPy = Join-Path $projRoot "app.py"
if (-not (Test-Path $appPy)) {
    Write-Host "ERROR: app.py not found under $projRoot" -ForegroundColor Red
    exit 1
}

function Get-ListenPids8000 {
    $rows = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if (-not $rows) { return @() }
    return @($rows | Select-Object -ExpandProperty OwningProcess -Unique)
}

Write-Host "Stopping processes listening on TCP port 8000 (up to 3 rounds)..." -ForegroundColor Cyan
for ($round = 1; $round -le 3; $round++) {
    $pids = Get-ListenPids8000
    if (-not $pids.Count) {
        Write-Host "Port 8000 is free." -ForegroundColor Green
        break
    }
    Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
        Select-Object LocalAddress, OwningProcess | Format-Table -AutoSize
    foreach ($procId in $pids) {
        Write-Host "  Stopping PID $procId ..." -ForegroundColor Yellow
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        & taskkill.exe /F /PID $procId 2>$null | Out-Null
    }
    Start-Sleep -Milliseconds 900
}

$left = Get-ListenPids8000
if ($left.Count) {
    Write-Host "" 
    Write-Host "ERROR: Port 8000 is still in use. Remaining PID(s): $($left -join ', ')" -ForegroundColor Red
    Write-Host "Try: close Docker Desktop / WSL services using 8000, or run this script in an elevated PowerShell (Run as administrator)." -ForegroundColor Yellow
    Write-Host "Check manually: Get-NetTCPConnection -LocalPort 8000 -State Listen" -ForegroundColor Gray
    exit 2
}

Set-Location $projRoot
$env:PYTHONPATH = $projRoot
Write-Host "Starting: $py -m uvicorn app:app --host 127.0.0.1 --port 8000 --app-dir $projRoot" -ForegroundColor Green
Write-Host "Browser: http://127.0.0.1:8000/  (terminal should print: SCLAW ... loaded app.py path)" -ForegroundColor Green
& $py -m uvicorn app:app --host 127.0.0.1 --port 8000 --app-dir $projRoot
