@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Build Release Package.ps1"
if errorlevel 1 (
  echo.
  echo Failed to rebuild the HimotheeLight v0.8.0 release package.
  pause
  exit /b 1
)
echo.
echo Release package verified successfully.
pause
