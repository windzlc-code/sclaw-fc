# Verify that the HTTP server on a port is THIS SCLAW (OpenAPI paths must include /api/health).
param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
$base = "http://127.0.0.1:$Port"
Write-Host "GET $base/openapi.json ..." -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "$base/openapi.json" -UseBasicParsing
    $txt = [string]$r.Content
} catch {
    Write-Host "FAIL: could not read OpenAPI: $_" -ForegroundColor Red
    exit 2
}
$hasHealth = $false
try {
    $obj = $txt | ConvertFrom-Json
    $names = @()
    if ($obj.paths -and $obj.paths.PSObject) {
        $names = @($obj.paths.PSObject.Properties | ForEach-Object { $_.Name })
    }
    $hasHealth = ($names -contains "/api/health") -or ($names -contains "/sclaw-ping") -or ($names -contains "/health")
} catch {
    $hasHealth = [bool](($txt -match '/api/health') -or ($txt -match 'api\\/health'))
}
if (-not $hasHealth) {
    Write-Host "FAIL: OpenAPI paths do not include /api/health — port $Port is NOT this SCLAW." -ForegroundColor Red
    exit 1
}
Write-Host "OK: OpenAPI lists SCLAW health path(s)." -ForegroundColor Green
try {
    $h = Invoke-RestMethod -Uri "$base/api/health" -Method Get
    Write-Host "OK: /api/health ->" ($h | ConvertTo-Json -Compress) -ForegroundColor Green
} catch {
    Write-Host "WARN: OpenAPI OK but /api/health request failed: $_" -ForegroundColor Yellow
    exit 3
}
exit 0
