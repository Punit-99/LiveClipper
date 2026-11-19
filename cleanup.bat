@echo off
rem ==============================================================================
rem LiveClipper Local Development Clutter Cleanup Script (Windows)
rem
rem Cleans up local virtual environments, dependency caches, build outputs, and
rem temporary files since all services run in Docker containers.
rem
rem Preserves:
rem   - All source code (backend\app\, frontend\src\)
rem   - Configuration files (backend\.env, backend\.env.example)
rem   - Docker files (Dockerfile, docker-compose.yml, nginx.conf)
rem   - Authentication tokens (backend\.youtube_tokens.json, backend\.tokens.json)
rem   - Media outputs (backend\clips\, backend\shorts\, manifest.jsonl)
rem   - Log files (backend\highlights.jsonl)
rem   - Requirements (requirements.txt, package.json, package-lock.json)
rem   - Font files & documentation (backend\app\video\fonts\, README.md)
rem
rem Usage:
rem   cleanup.bat [--dry-run] [--keep-buffer]
rem ==============================================================================

setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

set "DRY_RUN=0"
set "KEEP_BUFFER=0"

:parse_args
if "%~1"=="" goto done_args
if /i "%~1"=="--dry-run" set "DRY_RUN=1"
if /i "%~1"=="-n" set "DRY_RUN=1"
if /i "%~1"=="/dry-run" set "DRY_RUN=1"
if /i "%~1"=="--keep-buffer" set "KEEP_BUFFER=1"
if /i "%~1"=="/keep-buffer" set "KEEP_BUFFER=1"
if /i "%~1"=="-h" goto show_help
if /i "%~1"=="--help" goto show_help
if /i "%~1"=="/?" goto show_help
shift
goto parse_args

:show_help
echo Usage: cleanup.bat [OPTIONS]
echo.
echo Options:
echo   --dry-run, -n     Preview files and folders that will be deleted without removing them
echo   --keep-buffer     Preserve the recording buffer directory (.buffer\)
echo   -h, --help, /?    Show this help message and exit
goto :eof

:done_args
echo ======================================================================
echo                 LiveClipper Local Clutter Cleanup (Windows)
echo ======================================================================

if "%DRY_RUN%"=="1" (
    echo [MODE: DRY RUN] No files or directories will be removed.
) else (
    echo [MODE: EXECUTION] Removing clutter files and directories...
)
echo.

set /a ITEMS_COUNT=0

rem Helper function to process directory
call :handle_dir "Virtualenv" "%ROOT_DIR%.venv"
call :handle_dir "Virtualenv" "%ROOT_DIR%venv"
call :handle_dir "Virtualenv" "%ROOT_DIR%backend\.venv"
call :handle_dir "Virtualenv" "%ROOT_DIR%backend\venv"
call :handle_dir "Frontend Modules" "%ROOT_DIR%frontend\node_modules"
call :handle_dir "Frontend Build" "%ROOT_DIR%frontend\dist"

if "%KEEP_BUFFER%"=="1" (
    echo   Preserved [Recording Buffer]  backend\.buffer (--keep-buffer)
) else (
    call :handle_dir "Recording Buffer" "%ROOT_DIR%backend\.buffer"
    call :handle_dir "Recording Buffer" "%ROOT_DIR%.buffer"
)

call :handle_dir "Scratch Directory" "%ROOT_DIR%backend\scratch"
call :handle_file "Test Artifact" "%ROOT_DIR%backend\test_captions.ass"

rem Recursive __pycache__ directories
for /d /r "%ROOT_DIR%" %%d in (__pycache__) do (
    call :handle_dir "Python Cache" "%%d"
)

rem Recursive .pytest_cache and .mypy_cache
for /d /r "%ROOT_DIR%" %%d in (.pytest_cache .mypy_cache) do (
    call :handle_dir "Test Cache" "%%d"
)

rem Recursive pyc/pyo files outside pycache
for /r "%ROOT_DIR%" %%f in (*.pyc *.pyo *.tmp) do (
    call :handle_file "Bytecode/Temp" "%%f"
)

echo ----------------------------------------------------------------------
if "%DRY_RUN%"=="1" (
    echo DRY RUN COMPLETE: Found !ITEMS_COUNT! item(s) to delete.
    echo Run cleanup.bat without --dry-run to permanently delete these items.
) else (
    echo CLEANUP COMPLETE: Successfully removed !ITEMS_COUNT! item(s).
)
goto :eof

:handle_dir
set "CAT=%~1"
set "DPATH=%~2"
if exist "%DPATH%" (
    set /a ITEMS_COUNT+=1
    if "%DRY_RUN%"=="1" (
        echo   [DRY-RUN] [%CAT%] %DPATH%
    ) else (
        echo   [DELETED] [%CAT%] %DPATH%
        rmdir /s /q "%DPATH%" 2>nul
    )
)
exit /b

:handle_file
set "CAT=%~1"
set "FPATH=%~2"
if exist "%FPATH%" (
    set /a ITEMS_COUNT+=1
    if "%DRY_RUN%"=="1" (
        echo   [DRY-RUN] [%CAT%] %FPATH%
    ) else (
        echo   [DELETED] [%CAT%] %FPATH%
        del /f /q "%FPATH%" 2>nul
    )
)
exit /b
