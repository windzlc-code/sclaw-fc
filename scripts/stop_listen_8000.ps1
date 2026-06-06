# Stop every process that is LISTENing on TCP port 8000 (any bind address: 127.0.0.1, ::1, 0.0.0.0, ::).
$ErrorActionPreference = "Continue"
$rows = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $rows) {
    Write-Host "Nothing is listening on port 8000." -ForegroundColor Green
    exit 0
}
Write-Host "Listeners on :8000:" -ForegroundColor Cyan
$rows | Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table -AutoSize
$pids = $rows | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $pids) {
    Write-Host "Stopping PID $procId ..." -ForegroundColor Yellow
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}
Write-Host "Done. Start uvicorn again." -ForegroundColor Green
