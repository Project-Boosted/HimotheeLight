@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call "Setup HimotheeLight.bat"
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean HimotheeLight.spec
if errorlevel 1 goto :failed
echo.
echo Build complete: dist\HimotheeLight\HimotheeLight.exe
echo.
pause
exit /b 0
:failed
echo EXE build failed. Check the output above.
pause
exit /b 1
