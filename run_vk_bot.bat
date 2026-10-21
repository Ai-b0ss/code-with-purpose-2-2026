@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "..\.venv\Scripts\python.exe" set "PYTHON_EXE=..\.venv\Scripts\python.exe"
if not defined PYTHON_EXE (
    where python >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=python"
)
if not defined PYTHON_EXE (
    where py >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=py -3"
)
if not defined PYTHON_EXE (
    echo [ERROR] Python 3 not found. Please install Python 3.11+.
    pause
    exit /b 1
)

%PYTHON_EXE% -c "import vk_api, requests" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing requirements...
    %PYTHON_EXE% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install requirements.
        pause
        exit /b 1
    )
)

echo [INFO] Running VK Volunteer Bot (vk_bot/bot.py)...
echo.
%PYTHON_EXE% -m vk_bot.bot
if errorlevel 1 (
    echo.
    echo [INFO] Bot process exited with error.
    pause
)
