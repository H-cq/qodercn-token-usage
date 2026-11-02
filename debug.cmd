@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
where python >nul 2>nul
if not errorlevel 1 (
  python "%~dp0start.py" %*
  pause
  exit /b
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0start.py" %*
  pause
  exit /b
)
echo Python 3.8+ not found. Install Python and add it to PATH.
pause
