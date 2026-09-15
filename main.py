# -*- coding: utf-8 -*-
"""
PySceneDetect 视频分割器 —— 程序入口

运行方式：
    python main.py            （推荐；Windows 也可双击 启动.bat）

依赖：
    pip install PySide6 PySide6-Fluent-Widgets scenedetect imageio-ffmpeg

说明：用 pythonw.exe 启动时没有控制台，任何启动期异常都会「悄无声息地退出」。
因此这里做了兜底：把错误写进「启动日志.txt」，并用系统弹窗把原因显示出来。
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

if getattr(sys, "frozen", False):
    # PyInstaller 打包后：one-dir/one-file 布局不同，优先 _MEIPASS，其次 exe 同级目录
    _base = getattr(sys, "_MEIPASS", None)
    APP_DIR = Path(_base).resolve() if _base else Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
LOG_FILE = APP_DIR / "启动日志.txt"
# 与原始版本保持一致：任务栏图标使用 PySide6-Fluent-Widgets 的 FluentIcon。
APP_USER_MODEL_ID = "Arena.SceneSplitter.1"

# 保证从任意目录运行都能找到包
sys.path.insert(0, str(APP_DIR))


def show_native_error(title: str, message: str) -> None:
    """用系统弹窗显示错误（不依赖 Qt，Qt 起不来时也能用）。"""
    try:  # Windows
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
        return
    except Exception:
        pass
    try:  # macOS / Linux
        if sys.platform == "darwin":
            os.system(f'osascript -e \'display alert "{title}" message "{message}"\'')
    except Exception:
        pass
    print(f"{title}\n{message}", file=sys.stderr)


def write_log(text: str) -> None:
    """把启动信息写入日志文件（顺带保留上一次的日志）。"""
    for target in (LOG_FILE, Path(os.environ.get("TEMP", "/tmp")) / "scene_splitter_启动日志.txt"):
        try:
            if target.exists():
                try:
                    target.replace(target.with_suffix(".old.txt"))
                except Exception:
                    pass
            target.write_text(text, encoding="utf-8")
            return
        except Exception:
            continue


def setup_windows_taskbar() -> None:
    """Windows 任务栏图标归类（避免显示成 python.exe 的图标）。"""
    if os.name != "nt":
        return
    try:  # pragma: no cover - 仅 Windows
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def app_icon_variant() -> str:
    """App 主题对应的图标变体：深色 App 用白色字形，否则用黑色。

    用于标题栏小图标（标题栏颜色跟 App 主题）。必须在 init_config() 之后调用。
    """
    try:
        from qfluentwidgets import Theme, qconfig

        if qconfig.theme == Theme.DARK:
            return "white"
    except Exception:
        pass
    return "black"


def taskbar_icon_variant() -> str:
    """系统主题对应的图标变体：深色任务栏用白色字形，否则用黑色。

    注意：跟的是*系统*主题（任务栏颜色），不是 App 内主题——两者可独立设置。
    """
    try:  # Windows：任务栏跟随“系统”主题，直接读注册表最准
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            if winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 0:
                return "white"
            return "black"
    except Exception:
        pass
    try:  # 跨平台回退：Qt 6.5+ 的系统配色提示
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        hints = QApplication.instance().styleHints()
        if hints is not None and hints.colorScheme() == Qt.ColorScheme.Dark:
            return "white"
    except Exception:
        pass
    return "black"


def load_app_icon():
    """加载程序图标，返回 (QIcon, 来源描述)。

    优先使用程序自带的图标文件（原 FluentIcon.MOVIE 场记板字形预渲染，
    绝对路径，PNG/ICO 由 Qt 内建解码，不依赖 QtSvg 插件与运行时主题解析）。
    这里选的是 App 主题变体（标题栏用）；任务栏大图标由
    apply_native_window_icon 按系统主题单独设置。文件缺失或损坏时回退到
    FluentIcon.MOVIE，保证任何情况下 setWindowIcon 拿到的都不是空图标。
    """
    from PySide6.QtGui import QIcon

    variant = app_icon_variant()
    for name in (f"movie_{variant}.ico", f"movie_{variant}.png"):
        path = APP_DIR / "scene_splitter_gui" / "assets" / name
        if not path.exists():
            continue
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon, str(path)
    from qfluentwidgets import FluentIcon

    return FluentIcon.MOVIE.icon(), "FluentIcon.MOVIE（图标文件缺失，回退）"


def apply_native_window_icon(window) -> str:
    """Win32 原生兜底：直接给窗口 HWND 发 WM_SETICON，返回诊断字符串。

    某些 PySide6/Win11 组合 + pythonw 启动时，Qt 的 setWindowIcon 转换结果
    到不了任务栏（窗口图标为空 → 任务栏显示空白按钮）。这里用 LoadImageW 从
    自带 .ico 加载 HICON 并直发窗口，绕过 Qt。必须在 show() 之后调用，否则
    Qt 建窗时会覆盖掉。任何失败都只返回说明，不抛异常。

    大小图标分开设置：大图标（任务栏/Alt-Tab）跟系统主题，小图标（标题栏左上角）
    跟 App 主题——两个位置背景不同，各跟各的才都看得见。
    """
    if os.name != "nt":
        return "非 Windows，跳过"
    try:
        import ctypes
        from ctypes import wintypes

        assets = APP_DIR / "scene_splitter_gui" / "assets"
        big_variant = taskbar_icon_variant()
        small_variant = app_icon_variant()
        big_ico = assets / f"movie_{big_variant}.ico"
        small_ico = assets / f"movie_{small_variant}.ico"
        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                      wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                      wintypes.UINT]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]
        user32.SendMessageW.restype = wintypes.LPARAM
        hwnd = int(window.winId())
        # IMAGE_ICON=1, LR_LOADFROMFILE=0x10；文件缺失则该尺寸保持 Qt 设置的不动
        hbig = user32.LoadImageW(None, str(big_ico), 1, 32, 32, 0x10) if big_ico.exists() else 0
        hsmall = user32.LoadImageW(None, str(small_ico), 1, 16, 16, 0x10) if small_ico.exists() else 0
        if hbig:
            user32.SendMessageW(hwnd, 0x0080, 1, hbig)    # WM_SETICON, ICON_BIG
        if hsmall:
            user32.SendMessageW(hwnd, 0x0080, 0, hsmall)  # WM_SETICON, ICON_SMALL
        # 故意不 DestroyIcon：HICON 归窗口所有，随进程退出释放（与 Qt 行为一致）
        apply_native_window_icon._handles = (hbig, hsmall)
        return f"ok(big={hbig}/{big_variant}, small={hsmall}/{small_variant})"
    except Exception as exc:  # noqa: BLE001 —— 兜底逻辑绝不能影响启动
        return f"失败：{type(exc).__name__}: {exc}"


def refresh_window_icons(window, _theme=None) -> None:
    """App 内切换主题时刷新窗口图标：Qt 图标与标题栏小图标跟 App 主题，
    任务栏大图标跟系统主题。挂在 qconfig.themeChanged 上，内部全保护，
    绝不影响主题切换本身。"""
    try:
        icon, _src = load_app_icon()
        window.setWindowIcon(icon)
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.setWindowIcon(icon)
        except Exception:
            pass
        apply_native_window_icon(window)
    except Exception:
        pass


def install_excepthook(app) -> None:
    """运行期未捕获异常：记录日志 + 弹窗，而不是直接闪退。"""
    from PySide6.QtWidgets import QMessageBox

    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stderr.write(detail)
        write_log(f"运行期异常：\n{detail}")
        try:
            box = QMessageBox()
            box.setWindowTitle("程序出现异常")
            box.setIcon(QMessageBox.Critical)
            box.setText(f"发生未预期的错误：\n{exc_value}\n\n详细信息已写入：\n{LOG_FILE}")
            box.setDetailedText(detail)
            box.exec()
        except Exception:
            pass

    sys.excepthook = hook


def diagnose() -> str:
    """收集环境信息，用于出错时一并展示。"""
    lines = [
        f"程序目录：{APP_DIR}",
        f"Python：{sys.version}",
        f"解释器：{sys.executable}",
        f"工作目录：{os.getcwd()}",
        f"无控制台启动：{sys.executable.lower().endswith('pythonw.exe')}",
    ]
    for name in ("PySide6", "qfluentwidgets", "scenedetect", "imageio_ffmpeg", "cv2"):
        try:
            module = __import__(name)
            version = getattr(module, "__version__", "?")
            lines.append(f"{name}：已安装 v{version}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{name}：导入失败 -> {type(exc).__name__}: {exc}")
    return "\n".join(lines)


def main() -> int:
    setup_windows_taskbar()

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("PySceneDetect 视频分割器")
    app.setApplicationDisplayName("PySceneDetect 视频分割器")
    app.setOrganizationName("SceneSplitter")

    from scene_splitter_gui.config import init_config
    from scene_splitter_gui.main_window import MainWindow

    init_config()

    # 需要同时设置 QApplication 和具体窗口：前者是 Qt 默认窗口图标，
    # 后者是 Windows 任务栏/窗口标题栏实际读取的图标。
    # 在 init_config() 之后创建；两种启动方式共用同一 QIcon。
    icon, _icon_src = load_app_icon()
    app.setWindowIcon(icon)
    install_excepthook(app)

    window = MainWindow()
    window.setWindowIcon(icon)
    # App 内切换主题时同步刷新窗口图标（标题栏跟 App 主题，任务栏跟系统主题）
    try:
        from qfluentwidgets import qconfig

        qconfig.themeChanged.connect(lambda theme=None: refresh_window_icons(window, theme))
    except Exception:
        pass
    window.show()
    # Win32 原生兜底（见 apply_native_window_icon）：show 之后直发 WM_SETICON。
    apply_native_window_icon(window)
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 —— 启动期任何异常都要让用户看到
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        report = (
            "…………… 启动失败 ……………\n"
            f"原因：{type(exc).__name__}: {exc}\n\n"
            f"{diagnose()}\n\n"
            f"完整堆栈：\n{detail}\n"
            f"（已保存到 {LOG_FILE}）"
        )
        write_log(report)
        show_native_error(
            "PySceneDetect 视频分割器 - 启动失败",
            f"{type(exc).__name__}: {exc}\n\n"
            "常见原因：\n"
            "1) 依赖未安装或不完整 —— 请双击「安装依赖.bat」；\n"
            "2) 使用了 pythonw 启动但缺少模块 —— 可双击「调试启动.bat」查看详细报错；\n"
            f"3) 其他问题请查看日志文件：\n{LOG_FILE}\n\n"
            f"环境信息：\n{diagnose()}")
        sys.exit(1)
