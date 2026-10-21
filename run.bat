@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
if exist .env (
  python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765 --env-file .env
) else (
  python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
)
