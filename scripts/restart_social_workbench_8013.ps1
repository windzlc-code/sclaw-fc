param(
  [int]$Port = 8013
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"

if (-not (Test-Path (Join-Path $ProjectRoot "app.py"))) {
  Write-Host "ERROR: app.py not found under $ProjectRoot" -ForegroundColor Red
  exit 1
}

if (-not (Test-Path $VenvPython)) {
  Write-Host "ERROR: venv python not found: $VenvPython" -ForegroundColor Red
  Write-Host "Run run_local.ps1 once to create the local environment." -ForegroundColor Yellow
  exit 1
}

if (-not (Test-Path $LogDir)) {
  New-Item -ItemType Directory -Path $LogDir | Out-Null
}

function Get-PortPids {
  param([int]$TargetPort)
  $rows = Get-NetTCPConnection -LocalPort $TargetPort -ErrorAction SilentlyContinue
  if (-not $rows) { return @() }
  return @(
    $rows |
      Where-Object { $_.OwningProcess -and $_.OwningProcess -ne 0 } |
      Select-Object -ExpandProperty OwningProcess -Unique
  )
}

function Get-UvicornPortPids {
  param([int]$TargetPort)
  $escapedPort = [regex]::Escape([string]$TargetPort)
  return @(
    Get-CimInstance Win32_Process |
      Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "uvicorn\s+app:app" -and
        $_.CommandLine -match "--port\s+$escapedPort(\s|$)"
      } |
      Select-Object -ExpandProperty ProcessId -Unique
  )
}

function Get-DescendantPids {
  param([int[]]$RootPids)
  if (-not $RootPids -or -not $RootPids.Count) { return @() }
  $all = @(Get-CimInstance Win32_Process)
  $found = New-Object "System.Collections.Generic.HashSet[int]"
  $front = @($RootPids | Where-Object { $_ -and $_ -ne $PID } | Select-Object -Unique)
  while ($front.Count -gt 0) {
    $next = @()
    foreach ($rootPid in $front) {
      foreach ($child in ($all | Where-Object { [int]$_.ParentProcessId -eq [int]$rootPid })) {
        $childPid = [int]$child.ProcessId
        if ($childPid -ne $PID -and $found.Add($childPid)) {
          $next += $childPid
        }
      }
    }
    $front = @($next | Select-Object -Unique)
  }
  return @($found)
}

function Stop-PortWorkers {
  param([int]$TargetPort)
  for ($round = 1; $round -le 4; $round++) {
    $direct = @(Get-PortPids -TargetPort $TargetPort) + @(Get-UvicornPortPids -TargetPort $TargetPort)
    $direct = @($direct | Where-Object { $_ -and $_ -ne $PID } | Select-Object -Unique)
    if (-not $direct.Count) {
      Write-Host "Port $TargetPort has no matching workers." -ForegroundColor Green
      return
    }

    $descendants = @(Get-DescendantPids -RootPids $direct)
    $targets = @($descendants + $direct | Where-Object { $_ -and $_ -ne $PID } | Select-Object -Unique)
    Write-Host "Stopping port $TargetPort workers (round $round): $($targets -join ', ')" -ForegroundColor Yellow

    foreach ($procId in $targets) {
      Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    foreach ($procId in $targets) {
      try {
        & taskkill.exe /F /PID $procId 2>$null | Out-Null
      } catch {
        # The process may already be gone after Stop-Process.
      }
    }

    Start-Sleep -Milliseconds 900
  }
}

Stop-PortWorkers -TargetPort $Port

$left = @(Get-PortPids -TargetPort $Port)
if ($left.Count) {
  Write-Host "ERROR: Port $Port is still in use by PID(s): $($left -join ', ')" -ForegroundColor Red
  Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, State, OwningProcess |
    Format-Table -AutoSize
  exit 2
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$OutLog = Join-Path $LogDir "social_workbench_8013_$stamp.out.log"
$ErrLog = Join-Path $LogDir "social_workbench_8013_$stamp.err.log"
$Args = @(
  "-m", "uvicorn", "app:app",
  "--host", "0.0.0.0",
  "--port", [string]$Port
)

Set-Location $ProjectRoot
$env:PYTHONPATH = $ProjectRoot

Write-Host "Starting social workbench without reload on port $Port..." -ForegroundColor Cyan
$proc = Start-Process `
  -FilePath $VenvPython `
  -ArgumentList $Args `
  -WorkingDirectory $ProjectRoot `
  -RedirectStandardOutput $OutLog `
  -RedirectStandardError $ErrLog `
  -WindowStyle Hidden `
  -PassThru

Start-Sleep -Milliseconds 1400
$owners = @(Get-PortPids -TargetPort $Port)
if (-not $owners.Count) {
  Write-Host "ERROR: service did not start listening on port $Port." -ForegroundColor Red
  Write-Host "STDOUT: $OutLog" -ForegroundColor Yellow
  Write-Host "STDERR: $ErrLog" -ForegroundColor Yellow
  exit 3
}

Write-Host "OK: http://127.0.0.1:$Port/social-case-workbench" -ForegroundColor Green
Write-Host "PID: $($owners -join ', ')  started_process=$($proc.Id)" -ForegroundColor Green
Write-Host "Logs: $OutLog ; $ErrLog" -ForegroundColor DarkGray
exit 0
