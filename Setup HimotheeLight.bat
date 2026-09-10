@echo off
setlocal
cd /d "%~dp0"
echo.
echo  ========================================
echo       HimotheeLight v0.8.0 Setup
echo  ========================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m venv .venv
) else (
  where python >nul 2>nul
  if not %errorlevel%==0 goto :nopython
  python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" goto :failed

echo Installing HimotheeLight dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Setup complete.
echo Run "Run HimotheeLight.bat" to start the app.
echo Your configuration is stored separately in %%APPDATA%%\HimotheeLight.
echo.
pause
exit /b 0

:nopython
echo Python was not found.
echo Install Python 3.11 or newer from python.org, then run this file again.
pause
exit /b 1

:failed
echo HimotheeLight setup failed. Check the output above.
pause
exit /b 1
