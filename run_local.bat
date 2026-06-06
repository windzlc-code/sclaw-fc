@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_local.ps1" -InitData
endlocal
