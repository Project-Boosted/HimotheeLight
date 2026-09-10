@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo HimotheeLight has not been set up yet.
  echo Running setup first...
  call "Setup HimotheeLight.bat"
  if not exist ".venv\Scripts\python.exe" exit /b 1
)
set HIMOTHEELIGHT_CONSOLE_LOG=1
".venv\Scripts\python.exe" app.py
