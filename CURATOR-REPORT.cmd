@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run START-ILYA.cmd.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" -m scripts.local_curator_report
if errorlevel 1 pause
