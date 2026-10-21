@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
