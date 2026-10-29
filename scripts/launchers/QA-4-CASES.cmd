@echo off
chcp 65001 >nul
set "GIM_ROOT=%~dp0"
if not exist "%GIM_ROOT%backend\app.py" set "GIM_ROOT=%~dp0..\.."
cd /d "%GIM_ROOT%"
if not exist ".venv\Scripts\python.exe" (
  echo First run START-ILYA.cmd to install dependencies and start the server.
  pause
  exit /b 2
)
set "GIM_PORT="
set /p GIM_PORT=Running GIM server port [default 8876]: 
if not defined GIM_PORT set "GIM_PORT=8876"
".venv\Scripts\python.exe" -m scripts.qa_four_live --base-url "http://127.0.0.1:%GIM_PORT%"
echo.
echo Reports in reports\ : JSON and readable HTML. Give the results to Eva.
pause
