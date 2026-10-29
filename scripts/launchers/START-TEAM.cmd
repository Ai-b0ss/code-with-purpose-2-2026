@echo off
chcp 65001 >nul 2>&1
set "GIM_ROOT=%~dp0"
if not exist "%GIM_ROOT%backend\app.py" set "GIM_ROOT=%~dp0..\.."
cd /d "%GIM_ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0team-start.ps1" %*
if errorlevel 1 (
  echo [ERROR] Could not launch local GIM. See error above.
  pause
)

