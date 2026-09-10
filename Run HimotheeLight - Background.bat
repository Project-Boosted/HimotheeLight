@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  call "Setup HimotheeLight.bat"
  if not exist ".venv\Scripts\pythonw.exe" exit /b 1
)
start "HimotheeLight" ".venv\Scripts\pythonw.exe" app.py
