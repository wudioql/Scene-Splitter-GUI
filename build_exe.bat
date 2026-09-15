@echo off
chcp 65001 >nul
title 打包为 EXE - PySceneDetect 视频分割器
cd /d "%~dp0"

rem --- 项目虚拟环境 .venv 优先 ---
set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY="%~dp0.venv\Scripts\python.exe""
if not defined PY (
    where py >nul 2>nul
    if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
)
if exist "%~dp0.venv\Scripts\python.exe" echo [.venv] 使用项目虚拟环境打包

echo [1/3] 安装/更新 PyInstaller……
%PY% -m pip install -U pyinstaller
if not %errorlevel%==0 (
    echo [错误] PyInstaller 安装失败。
    pause
    exit /b 1
)

echo.
echo [2/3] 开始打包（体积较大，约需 1-3 分钟）……
%PY% -m PyInstaller --noconfirm --clean scene_splitter.spec
if not %errorlevel%==0 (
    echo [错误] 打包失败，请查看上方日志。
    pause
    exit /b 1
)

echo.
echo [3/3] 完成！可执行文件位于 dist\PySceneDetect视频分割器\ 目录。
echo        注意：分发时需要把整个目录一起拷贝（其中已内置 ffmpeg）。
pause
