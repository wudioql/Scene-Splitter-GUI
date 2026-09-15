# -*- coding: utf-8 -*-
"""PySceneDetect 视频分割器 —— 应用包。"""

# core.py 是可独立运行/测试的纯逻辑模块，不应因为 GUI 依赖未安装而无法导入。
# 完整 GUI 启动时会正常从 config.py 读取同一组常量；缺少 PySide6 时仅提供
# 最小回退值，让 ``from scene_splitter_gui import core`` 仍然可用。
try:
    from .config import APP_NAME, APP_SUBTITLE, APP_VERSION
except ImportError:  # pragma: no cover - 仅在纯核心测试环境触发
    APP_NAME = "PySceneDetect 视频分割器"
    APP_SUBTITLE = "基于 PySceneDetect + ffmpeg 的批量视频切割工具"
    APP_VERSION = "1.0.1"

__all__ = ["APP_NAME", "APP_SUBTITLE", "APP_VERSION", "__version__"]
__version__ = APP_VERSION
