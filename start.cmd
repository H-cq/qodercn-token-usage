@echo off
setlocal
cd /d "%~dp0"
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "%~dp0start.py"
  exit /b 0
)
where py >nul 2>nul
if not errorlevel 1 (
  start "" py -3w "%~dp0start.py"
  exit /b 0
)
echo Python 3.8+ not found. Install Python and add it to PATH.
pause
exit /b 1
