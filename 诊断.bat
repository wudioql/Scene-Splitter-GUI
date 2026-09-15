@echo off
rem ===================================================================
rem  Environment diagnostics - prints everything needed to troubleshoot
rem  This file is saved in GBK (cp936).
rem ===================================================================
title Diagnostics - PySceneDetect Video Splitter
cd /d "%~dp0"
setlocal

echo ============================================================
echo  PySceneDetect Video Splitter - environment check
echo ============================================================
echo.
echo [Folder] %~dp0
echo [Windows] 
ver
echo.

echo --- Python ---
where py 2>nul
where python 2>nul
py -3 -c "import sys;print('py launcher ->', sys.executable); print('version ->', sys.version)" 2>nul
python -c "import sys;print('python ->', sys.executable); print('version ->', sys.version)" 2>nul
echo.

echo --- Project venv (.venv) ---
if exist "%~dp0.venv\Scripts\python.exe" (
    echo [OK] .venv found - launchers will prefer it
    "%~dp0.venv\Scripts\python.exe" -c "import sys;print('venv python:', sys.executable)" 2>nul
    "%~dp0.venv\Scripts\python.exe" -c "import PySide6, qfluentwidgets, scenedetect, imageio_ffmpeg; print('venv modules: OK')" 2>nul
    if errorlevel 1 echo [WARN] venv misses some modules - run install-deps bat to fix
) else (
    if exist "%~dp0.venv\pyvenv.cfg" (
        echo [WARN] .venv folder exists but python.exe is missing - broken venv
        echo        delete the .venv folder and re-run install-deps bat to recreate it
    ) else (
        echo [INFO] no .venv - launchers use system Python
    )
)
echo.

echo --- Dependencies ---
py -3 -c "import PySide6, qfluentwidgets, scenedetect, imageio_ffmpeg; print('all modules OK'); print('PySide6', PySide6.__version__); print('scenedetect', scenedetect.__version__)" 2>nul
python -c "import PySide6, qfluentwidgets, scenedetect, imageio_ffmpeg; print('all modules OK'); print('PySide6', PySide6.__version__); print('scenedetect', scenedetect.__version__)" 2>nul
if errorlevel 1 echo [WARN] some modules are missing - run 安装依赖.bat
echo.

echo --- ffmpeg ---
where ffmpeg 2>nul
py -3 -c "from imageio_ffmpeg import get_ffmpeg_exe; print('bundled ffmpeg ->', get_ffmpeg_exe())" 2>nul
echo.

echo --- Qt platform test (renders a hidden window) ---
py -3 -c "import sys;from PySide6.QtWidgets import QApplication;app=QApplication(sys.argv);print('Qt OK, screen =', app.primaryScreen().size())" 2>nul
python -c "import sys;from PySide6.QtWidgets import QApplication;app=QApplication(sys.argv);print('Qt OK, screen =', app.primaryScreen().size())" 2>nul
echo.

echo ============================================================
echo  If everything above looks fine but the app still does not
echo  start, run 启动.bat and read 启动日志.txt.
echo ============================================================
pause
