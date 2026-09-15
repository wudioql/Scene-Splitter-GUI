@echo off
rem ===================================================================
rem  PySceneDetect Video Splitter - launcher
rem  NOTE: This file is saved in GBK (cp936) so Chinese text shows
rem        correctly on Chinese Windows without switching codepage.
rem        All commands are ASCII-only to avoid any parsing trouble.
rem ===================================================================
setlocal enabledelayedexpansion
title PySceneDetect Video Splitter
cd /d "%~dp0"

rem --- 0. prefer project venv (.venv) when present ---
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYEXE=%~dp0.venv\Scripts\python.exe"
    if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYW=%~dp0.venv\Scripts\pythonw.exe"
    echo Using project venv: %~dp0.venv
    goto :venv_ready
)
if exist "%~dp0.venv\pyvenv.cfg" (
    echo [WARN] .venv folder exists but python.exe is missing - broken venv?
    echo        using system Python; delete .venv and re-run install-deps bat to fix it.
)

set "PY="
set "PYW="

rem --- 1. locate python: prefer the py launcher, then python on PATH ---
where py >nul 2>nul
if not errorlevel 1 (
    set "PY=py -3"
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    where python3 >nul 2>nul
    if not errorlevel 1 set "PY=python3"
)

if not defined PY (
    echo ============================================================
    echo [ERROR] Python not found.
    echo         Please install Python 3.9+ from python.org and make
    echo         sure "Add python.exe to PATH" is checked during setup.
    echo ============================================================
    echo.
    pause
    exit /b 1
)

rem --- 2. full path of python.exe, and the matching pythonw.exe ---
for /f "delims=" %%i in ('%PY% -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE (
    echo [ERROR] Cannot query Python executable. Check your Python install.
    echo.
    pause
    exit /b 1
)
for %%i in ("%PYEXE%") do set "PYDIR=%%~dpi"
if exist "%PYDIR%pythonw.exe" set "PYW=%PYDIR%pythonw.exe"

:venv_ready
echo Using Python: %PYEXE%
if defined PYW (echo Windowless launcher: %PYW%) else (echo pythonw.exe not found, will use python.exe)

rem --- require Python 3.9+ (older Pythons can only install old PySide6 and crash at startup) ---
"%PYEXE%" -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] No usable Python 3.9+ found, aborting.
    echo         Install it from python.org, then re-run the dependency installer bat.
    echo         If .venv already exists, delete it first so it can be rebuilt.
    echo.
    pause
    exit /b 1
)

rem --- 3. dependency check (missing modules -> auto install) ---
"%PYEXE%" -c "import scenedetect, PySide6, qfluentwidgets, imageio_ffmpeg" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [INFO] Dependencies missing, installing now...
    "%PYEXE%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [INFO] Retrying with --user ...
        "%PYEXE%" -m pip install --user -r requirements.txt
    )
    "%PYEXE%" -c "import scenedetect, PySide6, qfluentwidgets, imageio_ffmpeg" >nul 2>nul
    if errorlevel 1 (
        echo.
        echo [ERROR] Dependency installation failed.
        echo         Run 安装依赖.bat manually and read its messages,
        echo         or check the network / proxy settings.
        echo.
        pause
        exit /b 1
    )
)

rem --- 4. launch. pythonw keeps the console hidden; python.exe shows logs ---
echo.
echo Starting application...
if defined PYW (
    start "" "%PYW%" "%~dp0main.py"
) else (
    "%PYEXE%" "%~dp0main.py"
    if errorlevel 1 (
        echo.
        echo [ERROR] Application exited with an error. See messages above,
        echo         or check 启动日志.txt in this folder.
        echo.
        pause
    )
)
exit /b 0
