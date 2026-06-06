param(
  [int]$Port = 8000,
  [switch]$InitData
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Split-Path -Parent $MyInvocation.MyCommand.Path)
$VenvDir = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

Set-Location $ProjectRoot
$env:PYTHONPATH = $ProjectRoot

if (!(Test-Path $VenvPython)) {
  Write-Host "Creating local virtual environment..."
  python -m venv .venv
}

Write-Host "Installing dependencies..."
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt

Write-Host "Installing Playwright Chromium (portal JS / anti-bot fallback). Skip with SCLAW_PLAYWRIGHT=0 ..."
if ($env:SCLAW_PLAYWRIGHT -eq "0") {
  Write-Host "Skipping Playwright install (SCLAW_PLAYWRIGHT=0)."
} else {
  try {
    & $VenvPython -m playwright install chromium
  } catch {
    Write-Host "playwright install chromium failed or skipped; install manually: python -m playwright install chromium" -ForegroundColor Yellow
  }
}

if ($InitData) {
  Write-Host "Running initial data pipeline..."
  & $VenvPython scripts/ingest_manual_links.py
  if ($LASTEXITCODE -ne 0) { throw "ingest_manual_links.py failed." }
  & $VenvPython scripts/run_pipeline.py
  if ($LASTEXITCODE -ne 0) { throw "run_pipeline.py failed." }
  & $VenvPython scripts/export_wordpress_csv.py
  if ($LASTEXITCODE -ne 0) { throw "export_wordpress_csv.py failed." }
}

Write-Host "Launching app on port $Port ..."
Write-Host ""
# ASCII-only URLs avoid misparsing when the script is read as a non-UTF-8 code page.
Write-Host "Browser: http://127.0.0.1:$Port/  (do not open templates/index.html from Explorer)" -ForegroundColor Cyan
Write-Host "Health:  http://127.0.0.1:$Port/api/health  (expect ok true)" -ForegroundColor DarkGray
Write-Host ""
& $VenvPython -m uvicorn app:app --host 0.0.0.0 --port $Port --reload
