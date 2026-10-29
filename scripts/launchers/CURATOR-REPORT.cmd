@echo off
chcp 65001 >nul
set "GIM_ROOT=%~dp0"
if not exist "%GIM_ROOT%backend\app.py" set "GIM_ROOT=%~dp0..\.."
cd /d "%GIM_ROOT%"
if not exist ".venv\Scripts\python.exe" (
  echo First run START-ILYA.cmd.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" -m scripts.local_curator_report
if errorlevel 1 pause
