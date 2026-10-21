@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0team-start.ps1"
if errorlevel 1 (
  echo [ERROR] Could not launch local GIM. See error above.
  pause
)
