# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：pyinstaller --noconfirm --clean scene_splitter.spec

打包结果位于 dist/PySceneDetect视频分割器/，可整体拷贝到其他 Windows 电脑运行。
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

# 自带图标资源（任务栏/标题栏，main.py 按绝对路径加载）：必须打进包里，
# 否则 EXE 回退到运行时 FluentIcon，任务栏图标修复链失效。
datas, binaries, hiddenimports = [], [], []
datas += [("scene_splitter_gui/assets/*.ico", "scene_splitter_gui/assets"),
          ("scene_splitter_gui/assets/*.png", "scene_splitter_gui/assets")]
for package in ("qfluentwidgets", "scenedetect", "imageio_ffmpeg"):
    try:
        d, b, h = collect_all(package)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += collect_submodules("cv2")
# 分段试看播放器用到的多媒体模块：显式声明，避免个别环境漏收后端插件
hiddenimports += ["PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PySceneDetect视频分割器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PySceneDetect视频分割器",
)
