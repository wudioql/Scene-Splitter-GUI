@echo off
rem ===================================================================
rem  Debug launcher - keeps the console open and shows all messages.
rem  Use this if 启动.bat flashes and disappears, then send us the
rem  output so the problem can be located.
rem  This file is saved in GBK (cp936).
rem ===================================================================
title PySceneDetect Video Splitter - DEBUG
cd /d "%~dp0"
setlocal

rem --- prefer project venv (.venv) when present ---
if exist "%~dp0.venv\Scripts\python.exe" (
    echo Using project venv: %~dp0.venv
    set "PY="%~dp0.venv\Scripts\python.exe""
    goto :have_py
)
if exist "%~dp0.venv\pyvenv.cfg" (
    echo [WARN] .venv folder exists but python.exe is missing - broken venv?
    echo        using system Python; delete .venv and re-run install-deps bat to fix it.
)

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [ERROR] Python not found. Install Python 3.9+ and add it to PATH.
    pause
    exit /b 1
)
:have_py

echo ============================================================
echo  DEBUG mode - the window will stay open after exit.
echo  Python: %PY%
echo ============================================================
echo.

%PY% -c "import PySide6, qfluentwidgets, scenedetect, imageio_ffmpeg" 2>nul
if errorlevel 1 (
    echo [WARN] Dependencies missing, installing...
    %PY% -m pip install -r requirements.txt
)

echo --- Starting application (console output below) ---
echo.
%PY% "%~dp0main.py"
set "RC=%errorlevel%"
echo.
echo ============================================================
echo  Application exited with code %RC%
if not "%RC%"=="0" (
    echo  Please read the messages above; the same info is also
    echo  written to 启动日志.txt in this folder.
)
echo ============================================================
pause
