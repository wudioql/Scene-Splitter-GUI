# -*- coding: utf-8 -*-
"""应用配置：使用 qfluentwidgets 的 QConfig 持久化到程序目录（便携）。

注意：QConfig 基类自带 themeMode / themeColor 两个配置项（用于 setTheme/setThemeColor），
这里刻意使用 uiThemeMode / uiThemeColor 命名，避免与基类项冲突。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PySide6.QtGui import QColor
from qfluentwidgets import (
    ConfigItem,
    OptionsConfigItem,
    OptionsValidator,
    QConfig,
    Theme,
    qconfig,
    setTheme,
    setThemeColor,
)

APP_NAME = "PySceneDetect 视频分割器"
APP_VERSION = "1.2.2"
APP_SUBTITLE = "基于 PySceneDetect + ffmpeg 的批量视频切割工具"
def _project_root() -> Path:
    """程序根目录（源码运行=仓库根；打包后=exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _migrate_legacy_config(legacy: Path, target: Path) -> bool:
    """一次性迁移家目录旧配置→项目目录（目标已存在则不动）。返回是否迁移。"""
    try:
        if legacy.resolve() == target.resolve() or not legacy.exists() or target.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, target)
    except Exception:
        return False
    try:
        legacy.unlink()  # 搬家成功，顺手清理旧文件（删不掉也无妨）
    except Exception:
        pass
    return True


CONFIG_DIR = _project_root() / ".scene_splitter_gui"
CONFIG_FILE = CONFIG_DIR / "config.json"
LEGACY_CONFIG_FILE = Path.home() / ".scene_splitter_gui" / "config.json"

THEME_MODES = {
    "跟随系统": Theme.AUTO,
    "浅色": Theme.LIGHT,
    "深色": Theme.DARK,
}

THEME_COLORS = {
    "蓝色（默认）": "#0078d4",
    "紫色": "#8a2be2",
    "绿色": "#107c10",
    "橙色": "#ca5010",
    "红色": "#c50f1f",
    "青色": "#038387",
    "粉色": "#e3008c",
}


class AppConfig(QConfig):
    """全部可持久化的用户设置。"""

    # 外观
    uiThemeMode = OptionsConfigItem("外观", "主题", "跟随系统",
                                    OptionsValidator(list(THEME_MODES.keys())))
    uiThemeColor = OptionsConfigItem("外观", "主题色", "蓝色（默认）",
                                     OptionsValidator(list(THEME_COLORS.keys())))

    # 行为
    autoOpenOutput = ConfigItem("行为", "完成后打开输出目录", True)
    autoSwitchResult = ConfigItem("行为", "完成后自动切换到结果页", True)
    verboseLog = ConfigItem("行为", "输出详细日志", True)
    rememberParams = ConfigItem("行为", "记住上次使用的参数", True)

    # 预览与试看
    loopPreview = ConfigItem("预览", "试看循环播放", True)
    mutePreview = ConfigItem("预览", "试看静音", False)
    collapseThumbs = ConfigItem("预览", "收起画面预览", False)

    # 环境
    ffmpegPath = ConfigItem("环境", "ffmpeg 路径", "")

    # 路径
    lastInputDir = ConfigItem("路径", "上次输入目录", str(Path.home()))
    lastOutputDir = ConfigItem("路径", "上次输出目录", str(Path.home() / "Videos"))

    # 参数（字典）
    lastParams = ConfigItem("参数", "上次使用的参数", {})


CFG: AppConfig = AppConfig()


def init_config() -> AppConfig:
    """加载配置文件并应用外观设置（家目录旧配置一次性搬到项目目录）。"""
    _migrate_legacy_config(LEGACY_CONFIG_FILE, CONFIG_FILE)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        qconfig.load(str(CONFIG_FILE), CFG)
        if not CONFIG_FILE.exists():
            qconfig.save()  # 首用：在项目里落下默认配置
    except Exception:
        pass
    apply_appearance()
    return CFG


def save_config() -> None:
    try:
        qconfig.save()
    except Exception:
        pass


def apply_appearance() -> None:
    """应用主题模式与主题色。"""
    try:
        mode = qconfig.get(CFG.uiThemeMode)
        setTheme(THEME_MODES.get(mode, Theme.AUTO))
    except Exception:
        try:
            setTheme(Theme.AUTO)
        except Exception:
            pass
    try:
        name = qconfig.get(CFG.uiThemeColor)
        setThemeColor(QColor(THEME_COLORS.get(name, "#0078d4")))
    except Exception:
        pass


def store_params(params: dict) -> None:
    """保存当前界面参数，下次启动时恢复。"""
    try:
        if not qconfig.get(CFG.rememberParams):
            return
        clean = {}
        for key, value in params.items():
            try:
                json.dumps(value)
            except TypeError:
                continue
            clean[key] = value
        qconfig.set(CFG.lastParams, clean)
    except Exception:
        pass


def load_last_params() -> dict:
    try:
        value = qconfig.get(CFG.lastParams)
        if isinstance(value, str) and value.strip():
            value = json.loads(value)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def reset_config() -> None:
    """恢复出厂设置。"""
    try:
        for item, value in (
            (CFG.uiThemeMode, "跟随系统"),
            (CFG.uiThemeColor, "蓝色（默认）"),
            (CFG.autoOpenOutput, True),
            (CFG.autoSwitchResult, True),
            (CFG.verboseLog, True),
            (CFG.rememberParams, True),
            (CFG.loopPreview, True),
            (CFG.mutePreview, False),
            (CFG.collapseThumbs, False),
            (CFG.ffmpegPath, ""),
            (CFG.lastParams, {}),
        ):
            qconfig.set(item, value)
        save_config()
        apply_appearance()
    except Exception:
        pass
