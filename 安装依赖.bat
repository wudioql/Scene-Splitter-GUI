@echo off
chcp 65001 >nul
title 安装依赖 - PySceneDetect 视频分割器
cd /d "%~dp0"

rem --- 项目虚拟环境 .venv 优先：存在则直接用，不存在则询问是否创建 ---
if exist "%~dp0.venv\Scripts\python.exe" (
    echo [.venv] 检测到项目虚拟环境，依赖将安装到其中
    set "PY="%~dp0.venv\Scripts\python.exe""
    goto :have_py
)

echo ============================================================
echo   正在为「PySceneDetect 视频分割器」安装依赖
echo   使用系统 Python：优先 py 启动器，其次 python
echo ============================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY=python"
    ) else (
        echo [错误] 没有找到 Python！
        echo        请先到 https://www.python.org/downloads/windows/ 安装 Python 3.9 及以上版本，
        echo        安装时务必勾选 “Add python.exe to PATH”。
        echo.
        pause
        exit /b 1
    )
)

echo.
echo [.venv] 未发现项目虚拟环境 .venv（推荐：依赖装在项目内，不污染系统 Python）。
set "MKVENV=Y"
set /p "MKVENV=是否现在创建 .venv？(Y/n，直接回车=创建)： "
if /i "%MKVENV%"=="n" goto :have_py
if /i "%MKVENV%"=="N" goto :have_py
if /i "%MKVENV%"=="no" goto :have_py
echo [.venv] 正在创建 .venv（约需一分钟）...
%PY% -m venv "%~dp0.venv"
if errorlevel 1 (
    echo [WARN] .venv 创建失败。
    goto :confirm_sys
)
set "PY="%~dp0.venv\Scripts\python.exe""
"%~dp0.venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo [WARN] .venv 中 pip 不可用。
    goto :confirm_sys
)
goto :have_py
:confirm_sys
set "USESYS="
set /p "USESYS=是否改用系统 Python 继续安装？(y/N，直接回车=退出)： "
if /i "%USESYS%"=="y" (
    echo [WARN] 将使用系统 Python 继续安装。
    goto :have_py
)
echo 已取消，未做任何安装。
pause
exit /b 1
:have_py

%PY% -c "import sys; print('使用 Python：', sys.version.split()[0], sys.executable)"
echo.

echo [1/3] 升级 pip……
%PY% -m pip install --upgrade pip

echo.
echo [2/3] 安装依赖（首次约需几分钟，下载量较大请耐心等待）……
%PY% -m pip install -r requirements.txt
if not %errorlevel%==0 (
    echo.
    echo [提示] 直接安装失败（可能是没有管理员权限），改用「当前用户目录」安装……
    %PY% -m pip install --user -r requirements.txt
    if not %errorlevel%==0 (
        echo.
        echo [错误] 安装仍然失败。常见原因：
        echo        1. 网络不通 —— 可改用国内镜像，例如：
        echo           %PY% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        echo        2. 被杀毒/代理软件拦截 —— 请临时关闭后重试。
        pause
        exit /b 1
    )
)

echo.
echo [3/3] 校验依赖……
%PY% -c "import scenedetect, PySide6, qfluentwidgets, imageio_ffmpeg; print('依赖校验通过')"
if not %errorlevel%==0 (
    echo [警告] 依赖校验未通过，请查看上方信息。
)

echo.
echo ============================================================
echo   依赖安装完成。现在可以双击「启动.bat」运行程序。
echo ============================================================
pause
