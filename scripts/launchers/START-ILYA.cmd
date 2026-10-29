@echo off
chcp 65001 >nul
set "GIM_ROOT=%~dp0"
if not exist "%GIM_ROOT%backend\app.py" set "GIM_ROOT=%~dp0..\.."
cd /d "%GIM_ROOT%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0portable-start.ps1"
if errorlevel 1 pause
