@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

cd /d "%~dp0"

REM ---------------------------------------------------------
REM 1. Find Python executable
REM ---------------------------------------------------------
set "PYTHON_EXE="

REM Try 'python' in PATH
where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=python"
    )
)

REM Try 'py' launcher if python failed
if not defined PYTHON_EXE (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -c "import sys" >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_EXE=py -3"
        )
    )
)

REM Search standard local appdata installations
if not defined PYTHON_EXE (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%D\python.exe" (
            set "PYTHON_EXE=%%D\python.exe"
        )
    )
)

REM Search Program Files and system drives
if not defined PYTHON_EXE (
    for /d %%D in ("%ProgramFiles%\Python3*", "%SystemDrive%\Python3*") do (
        if exist "%%D\python.exe" (
            set "PYTHON_EXE=%%D\python.exe"
        )
    )
)

if not defined PYTHON_EXE (
    echo [FAIL] Python 3 ?? ?????? ? PATH ??? ??????????? ?????????.
    echo ??????????, ?????????? Python 3.11+ ? ???????? ??? ? ?????????? ????? PATH.
    exit /b 1
)

REM ---------------------------------------------------------
REM 2. Verify required dependencies (fastapi, uvicorn, pydantic)
REM ---------------------------------------------------------
%PYTHON_EXE% -c "import fastapi, uvicorn, pydantic" >nul 2>&1
if errorlevel 1 (
    echo [INFO] ????????? ???????????? ?? requirements.txt...
    %PYTHON_EXE% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [FAIL] ?? ??????? ?????????? ??????????? ?? requirements.txt.
        exit /b 1
    )
)

REM ---------------------------------------------------------
REM 3. Run autonomous smoke test runner
REM ---------------------------------------------------------
%PYTHON_EXE% tests\run_local_smoke.py %*
set "TEST_EXIT_CODE=%ERRORLEVEL%"

exit /b %TEST_EXIT_CODE%
