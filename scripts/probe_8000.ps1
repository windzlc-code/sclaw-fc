# HTTP probe: see what actually answers on port 8000 (SCLAW returns JSON with service=sclaw).
$ErrorActionPreference = "Continue"
$base = "http://127.0.0.1:8000"
$paths = @("/api/health", "/sclaw-ping", "/docs", "/openapi.json")
Write-Host "=== HTTP probe $base ===" -ForegroundColor Cyan
foreach ($p in $paths) {
    $url = "$base$p"
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
        $body = $r.Content
        if ($body.Length -gt 240) { $body = $body.Substring(0, 240) + "..." }
        Write-Host "`n$p -> $($r.StatusCode)" -ForegroundColor Green
        Write-Host $body
    } catch {
        $resp = $_.Exception.Response
        if ($resp) {
            $code = [int]$resp.StatusCode
            Write-Host "`n$p -> $code" -ForegroundColor Yellow
            try {
                $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
                $txt = $sr.ReadToEnd()
                if ($txt.Length -gt 400) { $txt = $txt.Substring(0, 400) + "..." }
                Write-Host $txt
            } catch { Write-Host "(no body)" }
        } else {
            Write-Host "`n$p -> ERROR: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
}
Write-Host "`nIf /api/health is 404 but /docs exists, it is usually NOT this SCLAW app." -ForegroundColor Yellow
Write-Host "Free port 8000: .\scripts\stop_listen_8000.ps1  then start uvicorn." -ForegroundColor Yellow
