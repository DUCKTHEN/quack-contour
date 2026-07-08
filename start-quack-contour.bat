@echo off
setlocal

set "APP_DIR=%~dp0"
set "APP_URL=http://127.0.0.1:8765/"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$portOpen = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; " ^
  "if (-not $portOpen) { Start-Process -FilePath python -ArgumentList 'app.py' -WorkingDirectory '%APP_DIR%' -WindowStyle Hidden; Start-Sleep -Seconds 2 }; " ^
  "Start-Process '%APP_URL%'"

endlocal
