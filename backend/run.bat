@echo off
setlocal
cd /d "%~dp0"

REM Prefer virtualenv python if available (.venv or venv)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py %*
    exit /b %ERRORLEVEL%
)
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" run.py %*
    exit /b %ERRORLEVEL%
)

REM Fall back to system python
python run.py %*
exit /b %ERRORLEVEL%
