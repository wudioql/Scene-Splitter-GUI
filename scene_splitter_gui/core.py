# -*- coding: utf-8 -*-
"""
core.py —— 视频场景检测与分割的核心逻辑

本模块**不依赖任何 GUI 框架**，只负责：
  1. 读取视频信息（分辨率 / 帧率 / 时长 / 总帧数）
  2. 调用 PySceneDetect 做场景检测
  3. 按“最短/最长时长(秒)”与“最少/最多帧数”对检测结果做二次整理
  4. 调用 ffmpeg 按场景切割并输出成片
  5. 导出 CSV / HTML 报告、保存缩略图

作者：Arena Agent  许可：MIT
"""

from __future__ import annotations

import csv
import os
import re
from html import escape as html_escape
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from .smartcut import FrameSignal, adaptive_floor, dp_merge_bounds, place_smart_cuts

# --------------------------------------------------------------------------------------
# 依赖探测（PySceneDetect 缺失时 GUI 仍可启动，并给出安装提示）
# --------------------------------------------------------------------------------------
SCENEDETECT_IMPORT_ERROR = ""
SCENEDETECT_VERSION = ""
FFMPEG_PACKAGE_AVAILABLE = False
try:
    import scenedetect  # noqa: F401
    from scenedetect import (
        AdaptiveDetector,
        ContentDetector,
        FrameTimecode,
        HashDetector,
        HistogramDetector,
        SceneManager,
        ThresholdDetector,
        open_video,
    )

    SCENEDETECT_VERSION = getattr(scenedetect, "__version__", "?")
    SCENEDETECT_AVAILABLE = True
except Exception as _exc:  # pragma: no cover - 仅在缺依赖时触发
    SCENEDETECT_AVAILABLE = False
    SCENEDETECT_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

try:  # 0.7.x 叫 scenedetect.detector，0.6.x 叫 scenedetect.scene_detector
    from scenedetect.detector import FlashFilter  # type: ignore
except Exception:  # pragma: no cover
    try:
        from scenedetect.scene_detector import FlashFilter  # type: ignore
    except Exception:
        FlashFilter = None  # type: ignore

try:
    from scenedetect.scene_manager import save_images as _sd_save_images  # type: ignore
except Exception:  # pragma: no cover
    _sd_save_images = None

try:
    from scenedetect.video_splitter import get_ffmpeg_path as _sd_get_ffmpeg_path  # type: ignore
except Exception:  # pragma: no cover
    try:
        from scenedetect import get_ffmpeg_path as _sd_get_ffmpeg_path  # type: ignore
    except Exception:
        _sd_get_ffmpeg_path = None

try:
    from imageio_ffmpeg import get_ffmpeg_exe  # type: ignore
    FFMPEG_PACKAGE_AVAILABLE = True
except Exception:  # pragma: no cover
    get_ffmpeg_exe = None  # type: ignore

__version__ = "1.2.2"


# --------------------------------------------------------------------------------------
# 参数规格（GUI 依据这些规格自动生成控件，core 依据这些规格生成检测器参数）
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ParamSpec:
    """检测器参数描述。"""

    key: str
    label: str
    kind: str  # float / int / bool / choice
    default: object
    minimum: float = 0.0
    maximum: float = 100.0
    step: float = 1.0
    decimals: int = 2
    choices: tuple = ()          # choice: ((值, 显示名), ...)
    tip: str = ""                # 提示（中文）
    suffix: str = ""


# 检测器公共参数：画面变化权重
_WEIGHT_SPECS = (
    ParamSpec("weight_lum", "亮度权重", "float", 1.0, 0.0, 4.0, 0.1, 1,
              tip="相邻帧亮度差在打分中的权重，0 表示忽略该项"),
    ParamSpec("weight_hue", "色相权重", "float", 1.0, 0.0, 4.0, 0.1, 1,
              tip="相邻帧色相差在打分中的权重，0 表示忽略该项"),
    ParamSpec("weight_sat", "饱和度权重", "float", 1.0, 0.0, 4.0, 0.1, 1,
              tip="相邻帧饱和度差在打分中的权重，0 表示忽略该项"),
    ParamSpec("weight_edges", "边缘权重", "float", 0.0, 0.0, 4.0, 0.1, 1,
              tip="相邻帧边缘差在打分中的权重（需要 Kernel 尺寸 > 0），对运动敏感的素材可调低"),
)

# 每种检测器对应的参数
DETECTOR_PARAMS: dict[str, tuple[ParamSpec, ...]] = {
    "content": (
        ParamSpec("threshold", "检测阈值", "float", 27.0, 1.0, 255.0, 1.0, 1,
                  tip="画面变化打分超过该值即判定为切换。数值越小越敏感（切片更多），越大越迟钝（切片更少）"),
        ParamSpec("min_scene_len", "内部最短场景(帧)", "int", 15, 0, 100000, 1, 0,
                  tip="检测器自身的最小场景长度限制（帧）。小于该长度的镜头会被合并/抑制，0 表示不限制"),
        ParamSpec("luma_only", "仅比较亮度", "bool", False,
                  tip="只比较亮度变化，速度更快，但对颜色变化明显的素材可能漏检"),
        ParamSpec("kernel_size", "边缘检测窗口", "int", 0, 0, 64, 1, 0,
                  tip="Sobel 算子窗口大小，0 表示关闭边缘检测（当“边缘权重”>0 时才生效）"),
        ParamSpec("flash_mode", "闪光处理", "choice", "merge",
                  choices=(("merge", "合并闪光帧（推荐）"), ("suppress", "抑制短镜头")),
                  tip="处理闪光灯/爆炸等瞬时亮暗造成的误切分"),
        *_WEIGHT_SPECS,
    ),
    "adaptive": (
        ParamSpec("adaptive_threshold", "自适应阈值", "float", 3.0, 0.1, 20.0, 0.1, 1,
                  tip="相对于周围帧平均得分的倍数阈值，越小越敏感"),
        ParamSpec("min_scene_len", "内部最短场景(帧)", "int", 15, 0, 100000, 1, 0,
                  tip="检测器自身的最小场景长度限制（帧），0 表示不限制"),
        ParamSpec("window_width", "参考窗口(帧)", "int", 2, 1, 30, 1, 0,
                  tip="用于计算平均得分的窗口宽度，数值越大越平滑（适合手持抖动素材）"),
        ParamSpec("min_content_val", "最低内容得分", "float", 15.0, 0.0, 255.0, 1.0, 1,
                  tip="低于该得分的候选切换点会被忽略，用于抑制噪点抖动"),
        ParamSpec("luma_only", "仅比较亮度", "bool", False, tip="只比较亮度变化，速度更快"),
        ParamSpec("kernel_size", "边缘检测窗口", "int", 0, 0, 64, 1, 0, tip="0 表示关闭边缘检测"),
        *_WEIGHT_SPECS,
    ),
    "threshold": (
        ParamSpec("threshold", "亮度阈值", "float", 12.0, 1.0, 255.0, 1.0, 1,
                  tip="判定“黑场/淡出”的亮度阈值，越大越容易触发"),
        ParamSpec("min_scene_len", "内部最短场景(帧)", "int", 15, 0, 100000, 1, 0,
                  tip="检测器自身的最小场景长度限制（帧），0 表示不限制"),
        ParamSpec("fade_bias", "淡入淡出偏置", "float", 0.0, -1.0, 1.0, 0.05, 2,
                  tip="正数把切换点后移（偏淡入），负数前移（偏淡出），适合处理渐隐渐显转场"),
        ParamSpec("add_final_scene", "补全末尾场景", "bool", False,
                  tip="若视频以淡出结束，勾选后会把最后一段也作为场景输出"),
        ParamSpec("method", "检测方式", "choice", "floor",
                  choices=(("floor", "亮于阈值(floor)"), ("ceiling", "暗于阈值(ceiling)")),
                  tip="floor：检测画面变亮（如黑场结束）；ceiling：检测画面变暗（如淡出到黑场）"),
    ),
    "hash": (
        ParamSpec("threshold", "感知哈希阈值", "float", 0.395, 0.0, 1.0, 0.005, 3,
                  tip="汉明距离归一化阈值，越小越敏感"),
        ParamSpec("size", "哈希尺寸", "int", 16, 1, 64, 1, 0,
                  tip="计算哈希时缩放的边长，越大越精确但越慢"),
        ParamSpec("lowpass", "低通滤波强度", "int", 2, 0, 16, 1, 0,
                  tip="抑制高频细节（噪点、文字）的强度，0 表示不过滤"),
        ParamSpec("min_scene_len", "内部最短场景(帧)", "int", 15, 0, 100000, 1, 0,
                  tip="检测器自身的最小场景长度限制（帧），0 表示不限制"),
    ),
    "histogram": (
        ParamSpec("histogram_threshold", "直方图差异阈值", "float", 0.05, 0.0, 1.0, 0.005, 3,
                  tip="直方图差异大于该比例即判定为切换，越小越敏感"),
        ParamSpec("bins", "直方图柱数", "int", 256, 8, 1024, 8, 0,
                  tip="颜色直方图的分箱数量，越多越精确但越慢"),
        ParamSpec("min_scene_len", "内部最短场景(帧)", "int", 15, 0, 100000, 1, 0,
                  tip="检测器自身的最小场景长度限制（帧），0 表示不限制"),
    ),
}

DETECTOR_LABELS: dict[str, str] = {
    "content": "内容检测 ContentDetector（通用，推荐）",
    "adaptive": "自适应检测 AdaptiveDetector（含运动/抖动镜头）",
    "threshold": "阈值检测 ThresholdDetector（黑场/淡入淡出）",
    "hash": "感知哈希 HashDetector（快速、抗噪）",
    "histogram": "直方图检测 HistogramDetector（颜色分布变化）",
}

CONTAINERS = (("mp4", "MP4 (.mp4)"), ("mkv", "MKV (.mkv)"), ("mov", "MOV (.mov)"))

TEMPLATE_VARS = (
    "$VIDEO_NAME", "$SCENE_NUMBER", "$START_TIME", "$END_TIME",
    "$DURATION", "$START_FRAME", "$END_FRAME", "$DATE",
)

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}


# --------------------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------------------
@dataclass
class VideoInfo:
    path: str
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0        # 秒
    frame_count: int = 0

    @property
    def size_text(self) -> str:
        return f"{self.width}×{self.height}"


@dataclass
class SceneSpan:
    """一个切片（场景）的起止位置。end_frame 为开区间（不含）。"""

    index: int
    start_frame: int
    end_frame: int
    fps: float

    @property
    def frame_count(self) -> int:
        return max(0, self.end_frame - self.start_frame)

    @property
    def start_sec(self) -> float:
        return self.start_frame / self.fps if self.fps else 0.0

    @property
    def end_sec(self) -> float:
        return self.end_frame / self.fps if self.fps else 0.0

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec

    @property
    def start_tc(self) -> str:
        return format_timecode(self.start_sec, self.fps)

    @property
    def end_tc(self) -> str:
        return format_timecode(self.end_sec, self.fps)


@dataclass
class TaskParams:
    """一次完整任务的全部参数（GUI 收集后交给 core 执行）。"""

    video_path: str = ""
    output_dir: str = ""

    # —— 检测 ——
    detector: str = "content"
    detector_params: dict = field(default_factory=dict)
    start_time: float = 0.0          # 秒，0 表示从开头
    end_time: float = 0.0            # 秒，0 表示到结尾
    downscale: int = 0               # 0 = 自动
    frame_skip: int = 0
    crop: str = ""                   # "x0,y0,x1,y1"，空表示不裁剪

    # —— 分段长度限制 ——
    min_scene_sec: float = 0.0       # 0 = 不限制
    max_scene_sec: float = 0.0       # 0 = 不限制
    min_scene_frames: int = 0        # 0 = 不限制
    max_scene_frames: int = 0        # 0 = 不限制
    prefer_scene_sec: float = 0.0    # 0 = 平均切分；>0 时按该时长均匀切分（仅在触发最长限制时）
    smart_cut: bool = True           # 智能择优：合并/切分时优先保留画面变化最大的切点

    # —— 输出 ——
    template: str = "$VIDEO_NAME_$SCENE_NUMBER"
    start_number: int = 1
    pad: int = 3
    container: str = "mp4"
    reencode: bool = True
    crf: int = 22
    preset: str = "veryfast"
    audio: str = "copy"              # copy / aac / none
    extra_args: str = ""
    overwrite: bool = False

    # —— 报告 ——
    save_csv: bool = True
    save_html: bool = False
    save_images: bool = False
    image_count: int = 3

    do_split: bool = True            # False = 仅检测
    open_output_dir: bool = True     # 完成后自动打开输出目录


@dataclass
class TaskResult:
    video_path: str = ""
    info: Optional[VideoInfo] = None
    scenes: list = field(default_factory=list)     # list[SceneSpan]
    outputs: list = field(default_factory=list)    # 实际成功生成的文件 list[str]
    scene_outputs: list = field(default_factory=list)  # 与 scenes 一一对应，失败/跳过项为空串
    skipped: list = field(default_factory=list)
    csv_path: str = ""
    html_path: str = ""
    image_dir: str = ""
    elapsed: float = 0.0
    cancelled: bool = False
    clips_ready: bool = False      # True 表示已经完成切割（输出文件已生成）
    smart_used: bool = False       # 本次分段是否启用了智能择优
    log_lines: list = field(default_factory=list)

    @property
    def scene_count(self) -> int:
        return len(self.scenes)

    @property
    def total_seconds(self) -> float:
        return sum(s.duration for s in self.scenes)


# --------------------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------------------
def format_timecode(seconds: float, fps: float = 0.0) -> str:
    """把秒格式化成 HH:MM:SS.mmm（帧率大于 0 时附带帧号）。"""
    if seconds < 0:
        seconds = 0.0
    msec = int(round(seconds * 1000))
    h, rem = divmod(msec, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    base = f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    if fps:
        base += f" (第 {int(round(seconds * fps))} 帧)"
    return base


def format_duration(seconds: float) -> str:
    """人类友好的时长文本：1小时23分45.6秒 / 2分03.4秒 / 12.3秒。"""
    seconds = max(0.0, float(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h >= 1:
        return f"{int(h)} 小时 {int(m):02d} 分 {s:04.1f} 秒"
    if m >= 1:
        return f"{int(m)} 分 {s:04.1f} 秒"
    return f"{s:.2f} 秒"


def safe_filename(name: str, fallback: str = "output") -> str:
    """清理文件名中的非法字符（Windows 兼容）。"""
    name = (name or "").strip()
    name = _ILLEGAL.sub("_", name)
    name = name.rstrip(" .")
    if not name:
        return fallback
    if name.split(".")[0].upper() in _WIN_RESERVED:
        name = "_" + name
    return name[:180]


def default_detector_params(detector: str) -> dict:
    specs = DETECTOR_PARAMS.get(detector, ())
    out = {}
    for spec in specs:
        out[spec.key] = spec.default
    # 直方图检测器的 key 名与参数名统一
    return out


def all_default_params() -> dict:
    return {name: default_detector_params(name) for name in DETECTOR_PARAMS}


def validate_params(params: TaskParams) -> list[str]:
    """返回错误信息列表，空列表表示参数合法。"""
    errors: list[str] = []
    if not params.video_path:
        errors.append("请先选择要处理的视频文件。")
    elif not os.path.isfile(params.video_path):
        errors.append(f"视频文件不存在：{params.video_path}")

    if params.min_scene_sec < 0 or params.max_scene_sec < 0:
        errors.append("最短/最长时长不能为负数。")
    if params.max_scene_sec and params.min_scene_sec and params.max_scene_sec < params.min_scene_sec:
        errors.append("最长时长不能小于最短时长。")
    if params.max_scene_frames and params.min_scene_frames and params.max_scene_frames < params.min_scene_frames:
        errors.append("最多帧数不能少于最少帧数。")
    if params.start_time < 0 or params.end_time < 0:
        errors.append("起始/结束时间不能为负数。")
    if params.end_time and params.start_time and params.end_time <= params.start_time:
        errors.append("结束时间必须大于起始时间。")
    if params.frame_skip < 0:
        errors.append("跳帧间隔不能为负数。")
    if params.crop:
        if not re.fullmatch(r"\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+\s*", params.crop):
            errors.append("裁剪区域格式应为 x0,y0,x1,y1（例如 0,0,1919,1079）。")
        else:
            x0, y0, x1, y1 = (int(value) for value in params.crop.split(","))
            if x1 <= x0 or y1 <= y0:
                errors.append("裁剪区域必须满足 x1 > x0 且 y1 > y0。")
    if params.min_scene_frames < 0 or params.max_scene_frames < 0:
        errors.append("最少/最多帧数不能为负数。")
    if params.reencode and not (0 <= params.crf <= 51):
        errors.append("CRF 取值范围为 0~51。")
    if not params.do_split:
        pass
    elif not params.output_dir:
        errors.append("请选择输出目录。")
    if params.do_split and not params.template.strip():
        errors.append("文件名模板不能为空。")
    return errors


# --------------------------------------------------------------------------------------
# ffmpeg 定位
# --------------------------------------------------------------------------------------
def resolve_ffmpeg(user_path: str = "") -> Optional[str]:
    """按 用户指定 → 系统 PATH → imageio-ffmpeg 的顺序查找 ffmpeg。"""
    if user_path:
        p = Path(user_path)
        if p.is_file():
            return str(p)
        if p.is_dir():
            for exe in ("ffmpeg.exe", "ffmpeg"):
                if (p / exe).is_file():
                    return str(p / exe)
        return None
    if _sd_get_ffmpeg_path is not None:
        try:
            found = _sd_get_ffmpeg_path()
            if found:
                return found
        except Exception:
            pass
    found = shutil.which("ffmpeg")
    if found:
        return found
    if FFMPEG_PACKAGE_AVAILABLE and get_ffmpeg_exe is not None:
        try:
            return get_ffmpeg_exe()
        except Exception:
            return None
    return None


def _no_window_kwargs() -> dict:
    """Windows 下隐藏子进程黑窗口。"""
    if os.name == "nt":  # pragma: no cover - 仅在 Windows 执行
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"startupinfo": si, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def open_path_in_explorer(path: str) -> None:
    """在系统文件管理器中打开目录/文件。"""
    try:
        if os.name == "nt":  # pragma: no cover
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":  # pragma: no cover
            subprocess.Popen(["open", path])
        else:  # pragma: no cover
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


# --------------------------------------------------------------------------------------
# 视频信息
# --------------------------------------------------------------------------------------
def probe_video(path: str) -> VideoInfo:
    """读取视频基础信息。"""
    if not SCENEDETECT_AVAILABLE:
        raise RuntimeError("未安装 PySceneDetect，无法读取视频信息。")
    video = open_video(path)
    try:
        fps = float(video.frame_rate) if video.frame_rate else 0.0
        duration = float(video.duration.get_seconds()) if video.duration is not None else 0.0
        frame_count = int(video.duration.get_frames()) if video.duration is not None else 0
        width, height = (int(video.frame_size[0]), int(video.frame_size[1]))
    finally:
        try:
            video.reset()  # 释放句柄
        except Exception:
            pass
    if duration <= 0 and fps > 0 and frame_count > 0:
        duration = frame_count / fps
    return VideoInfo(path=path, width=width, height=height, fps=fps,
                     duration=duration, frame_count=frame_count)


# --------------------------------------------------------------------------------------
# 检测器构建
# --------------------------------------------------------------------------------------
def _filter_mode(value):
    if FlashFilter is None:
        return None
    return FlashFilter.Mode.SUPPRESS if value == "suppress" else FlashFilter.Mode.MERGE


def _components(dp: dict):
    """构建画面分量权重；老版本无 Components 时返回 None。"""
    try:
        return ContentDetector.Components(
            delta_hue=float(dp.get("weight_hue", 1.0)),
            delta_sat=float(dp.get("weight_sat", 1.0)),
            delta_lum=float(dp.get("weight_lum", 1.0)),
            delta_edges=float(dp.get("weight_edges", 0.0)),
        )
    except Exception:
        return None


def build_detector(params: TaskParams):
    """依据参数创建 PySceneDetect 检测器实例。"""
    if not SCENEDETECT_AVAILABLE:
        raise RuntimeError("未安装 PySceneDetect，无法执行场景检测。")
    name = params.detector
    dp = dict(default_detector_params(name))
    dp.update(params.detector_params or {})
    min_len = int(dp.get("min_scene_len", 15) or 0)
    mode = _filter_mode(dp.get("flash_mode", "merge"))
    comps = _components(dp)
    ksize = int(dp.get("kernel_size", 0) or 0)

    def content_kwargs() -> dict:
        kw = {"threshold": float(dp.get("threshold", 27.0)), "min_scene_len": min_len}
        if comps is not None:
            kw["weights"] = comps
        if "luma_only" in dp:
            kw["luma_only"] = bool(dp.get("luma_only"))
        if ksize:
            kw["kernel_size"] = ksize
        if mode is not None:
            kw["filter_mode"] = mode
        return kw

    if name == "content":
        return ContentDetector(**content_kwargs())
    if name == "adaptive":
        kw = {
            "adaptive_threshold": float(dp.get("adaptive_threshold", 3.0)),
            "min_scene_len": min_len,
            "window_width": int(dp.get("window_width", 2)),
            "min_content_val": float(dp.get("min_content_val", 15.0)),
        }
        if comps is not None:
            kw["weights"] = comps
        if "luma_only" in dp:
            kw["luma_only"] = bool(dp.get("luma_only"))
        if ksize:
            kw["kernel_size"] = ksize
        return AdaptiveDetector(**kw)
    if name == "threshold":
        kw = {
            "threshold": float(dp.get("threshold", 12.0)),
            "min_scene_len": min_len,
            "fade_bias": float(dp.get("fade_bias", 0.0)),
            "add_final_scene": bool(dp.get("add_final_scene", False)),
        }
        method = dp.get("method", "floor")
        try:
            kw["method"] = ThresholdDetector.Method.FLOOR if method == "floor" else ThresholdDetector.Method.CEILING
        except Exception:
            pass
        return ThresholdDetector(**kw)
    if name == "hash":
        return HashDetector(
            threshold=float(dp.get("threshold", 0.395)),
            size=int(dp.get("size", 16)),
            lowpass=int(dp.get("lowpass", 2)),
            min_scene_len=min_len,
        )
    if name == "histogram":
        return HistogramDetector(
            threshold=float(dp.get("histogram_threshold", 0.05)),
            bins=int(dp.get("bins", 256)),
            min_scene_len=min_len,
        )
    raise ValueError(f"未知的检测器类型：{name}")


# --------------------------------------------------------------------------------------
# 场景检测
# --------------------------------------------------------------------------------------
class Reporter:
    """轻量回调接口（GUI 用子类接管信号，命令行/测试可直接打印）。"""

    def log(self, message: str, level: str = "info") -> None:
        print(f"[{level}] {message}")

    def stage(self, text: str) -> None:
        self.log(text)

    def progress(self, current: int, total: int) -> None:
        pass

    def cancelled(self) -> bool:
        return False


class Cancelled(Exception):
    """用户取消任务。"""


def _make_scene_manager(on_frame=None, cancelled=None, on_image=None):
    """构造带“逐帧回调”的 SceneManager（用于实时进度、即时取消与变化信号记录）。
    on_image(frame_no, image) 在每帧处理前触发，供智能择优记录画面变化信号。

    PySceneDetect 的公开回调只在检测到切点时触发，若视频长时间没有切点，
    进度条与取消按钮就会失灵。这里继承 SceneManager 并重写逐帧方法 `_process_frame`，
    保证每一帧都能汇报进度、响应取消。若版本不支持该方法则退回原生实现。
    """
    base = SceneManager
    if not hasattr(base, "_process_frame"):
        return base()

    class _ProgressSceneManager(base):  # type: ignore[misc, valid-type]
        def __init__(self):
            super().__init__()
            self._on_frame = on_frame
            self._cancel_cb = cancelled
            self._on_image = on_image
            self._frame_counter = 0

        def _process_frame(self, *args, **kwargs):  # noqa: D401
            if self._cancel_cb is not None and self._cancel_cb():
                self.stop()
                return False
            self._frame_counter += 1
            if self._on_frame is not None:
                position = args[0] if args else None
                try:
                    self._on_frame(self._frame_counter, position)
                except Exception:
                    pass
            if self._on_image is not None and len(args) >= 2:
                try:
                    self._on_image(args[0], args[1])
                except Exception:
                    pass
            return super()._process_frame(*args, **kwargs)  # type: ignore[misc]

    try:
        return _ProgressSceneManager()
    except Exception:
        return base()


def _seconds_of(position, fps: float) -> float:
    """兼容不同版本：0.7.x 传 FrameTimecode，0.6.x 传帧号(int)。"""
    if position is None:
        return 0.0
    try:
        return float(position.get_seconds())
    except AttributeError:
        try:
            return float(position) / (fps or 1.0)
        except Exception:
            return 0.0


def _make_signal_recorder(change_signal: FrameSignal):
    """构造逐帧信号灌入回调；numpy 缺失时返回 None（调用方回退老逻辑）。"""
    import importlib.util
    if importlib.util.find_spec("numpy") is None:
        return None
    state: dict = {"prev": None}

    def record(frame_no, image) -> None:
        try:
            if image is None or getattr(image, "ndim", 0) < 2:
                return
            # 取 1/4 稀疏像素算平均帧差：相对排序不变，每帧仅几十微秒
            small = image[::4, ::4]
            prev = state["prev"]
            if prev is not None and prev.shape == small.shape:
                diff = small.astype("int16") - prev.astype("int16")
                score = float(abs(diff).mean())
            else:
                score = 0.0
            state["prev"] = small.copy()
            change_signal.add(frame_no, score)
        except Exception:
            pass

    return record


def detect(params: TaskParams, reporter: Reporter):
    """执行场景检测，返回 (视频信息, 原始场景列表, 视频流对象, 变化信号)。

    变化信号为 FrameSignal 或 None：只在开了智能开关且设了长度限制时才记录，
    供 apply_limits 做择优合并/切分；为空时自动回退老逻辑。
    """
    if not SCENEDETECT_AVAILABLE:
        raise RuntimeError(
            "未安装 PySceneDetect，请先执行：\n python -m pip install -U scenedetect\n"
            f"（导入错误：{SCENEDETECT_IMPORT_ERROR}）"
        )

    reporter.stage("打开视频…")
    video = open_video(params.video_path)
    fps = float(video.frame_rate) if video.frame_rate else 25.0
    duration = float(video.duration.get_seconds()) if video.duration is not None else 0.0
    frame_count = int(video.duration.get_frames()) if video.duration is not None else 0
    info = VideoInfo(params.video_path, int(video.frame_size[0]), int(video.frame_size[1]),
                     fps, duration, frame_count)
    reporter.log(f"视频信息：{info.size_text}，{fps:.3f} fps，时长 {format_duration(duration)}，"
                 f"共 {frame_count} 帧")

    # 起始 / 结束时间
    if params.start_time > 0:
        if params.start_time >= duration > 0:
            raise ValueError("起始时间超出了视频总时长。")
        video.seek(FrameTimecode(params.start_time, fps=fps))
        reporter.log(f"从 {format_timecode(params.start_time, fps)} 开始处理")
    end_time = params.end_time if params.end_time > 0 else None
    if end_time is not None:
        end_time = min(end_time, duration) if duration else end_time
        if end_time <= params.start_time:
            raise ValueError("结束时间必须大于起始时间，且不能超出有效视频范围。")
        reporter.log(f"处理到 {format_timecode(end_time, fps)} 为止")

    reporter.stage("正在检测场景…")
    total_frames = int(((end_time or duration) or 0) * fps) or 0
    counters = {"cuts": 0, "frames": 0}

    # 智能择优信号：只在“开了开关且设了长度限制”时记录，避免无谓开销
    want_signal = bool(getattr(params, "smart_cut", False))
    if want_signal:
        _need_min, _need_max = _limits_to_frames(fps, params.min_scene_sec, params.max_scene_sec,
                                                 params.min_scene_frames, params.max_scene_frames)
        want_signal = bool(_need_min or _need_max)
    change_signal = FrameSignal() if want_signal else None
    signal_recorder = _make_signal_recorder(change_signal) if change_signal is not None else None
    if change_signal is not None and signal_recorder is None:
        change_signal = None  # numpy 不可用：放弃记录，后续自动回退老逻辑

    def on_frame(frame_no: int, position) -> None:
        # 每 5 帧汇报一次，避免信号过于频繁影响性能
        if frame_no % 5 == 0:
            reporter.progress(frame_no, total_frames or frame_no)

    def on_cut(_frame, position) -> None:
        counters["cuts"] += 1
        seconds = _seconds_of(position, fps)
        counts = counters["cuts"]
        if counts == 1 or counts % 25 == 0:
            reporter.log(f"已检测到 {counts} 处切换（当前 {format_timecode(seconds)}）")

    manager = _make_scene_manager(on_frame=on_frame, cancelled=reporter.cancelled,
                                  on_image=signal_recorder)
    if params.downscale and params.downscale > 0:
        manager.downscale = int(params.downscale)
        manager.auto_downscale = False
        reporter.log(f"下采样倍率：{params.downscale}（越小越精确，越大越快）")
    else:
        manager.auto_downscale = True
        reporter.log("下采样：自动（大于 1080p 的素材会按需缩小，加快检测）")

    if params.crop:
        try:
            x0, y0, x1, y1 = (int(v) for v in params.crop.split(","))
            manager.crop = (x0, y0, x1, y1)
            reporter.log(f"裁剪区域：({x0},{y0}) - ({x1},{y1})")
        except Exception:
            reporter.log("裁剪区域解析失败，已忽略。", "warn")

    detector = build_detector(params)
    manager.add_detector(detector)
    reporter.log(f"检测器：{DETECTOR_LABELS.get(params.detector, params.detector)}")
    if params.frame_skip:
        reporter.log(f"跳帧间隔：{params.frame_skip}（每 {params.frame_skip + 1} 帧处理 1 帧）", "warn")

    try:
        processed = manager.detect_scenes(
            video=video,
            end_time=end_time,
            frame_skip=int(params.frame_skip),
            show_progress=False,
            callback=on_cut,
        )
    except Exception as exc:
        if reporter.cancelled():
            raise Cancelled() from exc
        raise

    if reporter.cancelled():
        raise Cancelled()

    raw = manager.get_scene_list(start_in_scene=True)
    reporter.log(f"检测完成，共处理 {processed} 帧，发现 {counters['cuts']} 处切换，原始场景数：{len(raw)}")

    scenes: list[SceneSpan] = []
    for i, (start_tc, end_tc) in enumerate(raw, start=1):
        try:
            start_frame = int(start_tc.frame_num)
            end_frame = int(end_tc.frame_num)
        except AttributeError:  # 老版本
            start_frame = int(start_tc.get_frames())
            end_frame = int(end_tc.get_frames())
        scenes.append(SceneSpan(index=i, start_frame=start_frame, end_frame=end_frame, fps=fps))
    if info.frame_count:
        # 结束帧不要超过视频总帧数
        for s in scenes:
            s.end_frame = min(s.end_frame, info.frame_count)
    scenes = [s for s in scenes if s.frame_count > 0]
    reporter.progress(total_frames, total_frames)
    return info, scenes, video, change_signal


# --------------------------------------------------------------------------------------
# 场景二次整理：最短/最长时长 + 最少/最多帧数
# --------------------------------------------------------------------------------------
def _limits_to_frames(fps: float, min_sec: float, max_sec: float,
                      min_frames: int, max_frames: int) -> tuple[int, int]:
    """把“秒”和“帧”两组限制换算成统一的帧数限制（取更严格的一方）。"""
    min_final = int(min_frames or 0)
    max_final = int(max_frames or 0)
    if min_sec and fps:
        min_final = max(min_final, int(round(min_sec * fps)))
    if max_sec and fps:
        by_sec = int(round(max_sec * fps))
        max_final = min(max_final, by_sec) if max_final else by_sec
    return min_final, max_final


def _smart_merge(items, change_signal, min_f, reporter):
    """DP 合并：在满足最短限制的前提下保留置信度和最高的切点。"""
    bounds = [items[0][0]] + [end for _, end in items]
    scores = [0.0] + [change_signal.score_at(b) for b in bounds[1:-1]] + [0.0]
    kept = dp_merge_bounds(bounds, scores, min_f)
    if len(kept) < 2:
        kept = [bounds[0], bounds[-1]]
    merged = [[kept[i], kept[i + 1]] for i in range(len(kept) - 1)]
    merged = [[start, end] for start, end in merged if end > start]
    if len(merged) != len(items):
        reporter.log(f"智能合并：{len(items)} 段 → {len(merged)} 段"
                     f"（保留变化最明显的切点，最短 {min_f} 帧）")
    return merged, True


def _even_cuts(start, end, pieces, min_f):
    """老式均匀切分（含尾部碎片规则），智能模式用它做候选兜底。"""
    bounds = [start]
    step = (end - start) / max(1, pieces)
    cursor = start
    for k in range(pieces):
        seg_end = end if k == pieces - 1 else int(round(start + step * (k + 1)))
        if seg_end <= cursor:
            continue
        if k == pieces - 2 and (end - seg_end) < min_f:
            seg_end = end
        bounds.append(seg_end)
        cursor = seg_end
        if cursor >= end:
            break
    if bounds[-1] != end:
        bounds.append(end)
    return sorted(set(bounds))


def _smart_split_bounds(start, end, pieces, even_bounds, change_signal, min_f, max_f):
    """把均匀理想点吸附到信号峰值；无峰/不可行返回 None（调用方用均匀）。"""
    samples = change_signal.window_scores(start + 1, end)
    if len(samples) < 3:
        return None
    floor = adaptive_floor([score for _, score in samples])
    separation = max(5, min_f // 8) if min_f else 5
    peaks = change_signal.peaks_in(start + 1, end, floor, separation)
    if not peaks:
        return None
    if len(peaks) > 120:  # 峰太多时只留最高的，控制 DP 规模
        peaks = sorted(peaks, key=lambda p: -p[1])[:120]
    # 候选 = 均匀理想点（0 分兜底）+ 峰（后放，同帧时峰分覆盖 0 分）
    candidates = [(b, 0.0) for b in even_bounds[1:-1]] + list(peaks)
    return place_smart_cuts(start, end, pieces, candidates, min_f, max_f)


def apply_limits(scenes: Sequence[SceneSpan], fps: float, params: TaskParams,
                 reporter: Reporter, total_frames: int = 0,
                 range_start_frame: int = 0, range_end_frame: int = 0,
                 change_signal: Optional[FrameSignal] = None) -> tuple[list[SceneSpan], bool]:
    """把检测结果整理成满足限制的连续切片列表。

    change_signal 非空且 params.smart_cut 为真时启用智能择优（合并/切分优先保留
    高置信切点），返回 (结果, True)；否则走老逻辑并返回 (结果, False)。

    ``range_start_frame`` / ``range_end_frame`` 使用同样的半开区间约定。
    处理范围不是从 0 开始时，不能再无条件把第一段改成 0，否则用户选择
    “从第 N 秒开始”后会把范围外的前半段错误地重新加入输出。
    """
    if not scenes:
        return [], False

    min_f, max_f = _limits_to_frames(fps, params.min_scene_sec, params.max_scene_sec,
                                     params.min_scene_frames, params.max_scene_frames)
    # 智能分支总闸：开关开 + 信号非空才进入，否则下面全部走老逻辑
    use_smart = (bool(getattr(params, "smart_cut", False)) and change_signal is not None
                 and len(change_signal) > 0)
    smart_used = False
    range_start = max(0, int(range_start_frame or 0))
    range_end = int(range_end_frame or 0)
    if range_end <= range_start:
        range_end = int(total_frames or 0)
    items = [[s.start_frame, s.end_frame] for s in sorted(scenes, key=lambda x: x.start_frame)]
    items[0][0] = range_start
    for i in range(1, len(items)):               # 首尾相接，保证范围内不漏帧
        items[i][0] = items[i - 1][1]
    if range_end > 0:
        items[-1][1] = min(items[-1][1], range_end)
    items = [[start, end] for start, end in items if end > start]
    if not items:
        return [], False

    # 1) 合并过短的片段
    if min_f > 0:
        if use_smart:
            items, _mg = _smart_merge(items, change_signal, min_f, reporter)
            smart_used = smart_used or _mg
        else:
            merged: list[list[int]] = []
            pending = None
            for start, end in items:
                if pending is None:
                    pending = [start, end]
                else:
                    pending[1] = end
                if pending[1] - pending[0] >= min_f:
                    merged.append(pending)
                    pending = None
            if pending is not None:
                if merged and pending[1] - pending[0] < min_f:
                    merged[-1][1] = pending[1]          # 尾部太短 → 并入上一段
                elif pending[1] > pending[0]:
                    merged.append(pending)
            items = merged

    # 2) 切分过长的片段
    if max_f > 0:
        split: list[list[int]] = []
        prefer_f = int(round(params.prefer_scene_sec * fps)) if (params.prefer_scene_sec and fps) else 0
        snapped_count = 0
        for start, end in items:
            length = end - start
            if length <= max_f:
                split.append([start, end])
                continue
            target = max_f
            if prefer_f:
                target = min(max_f, max(prefer_f, min_f or 1))
            pieces = max(1, -(-length // target))     # 向上取整
            if min_f > 0:
                # 「最少帧数」与「最多帧数」可能互相矛盾（例如都为 60），
                # 此时宁可每段略超上限，也不要切出一堆不达标的短片段
                pieces = max(1, min(pieces, max(1, length // min_f)))
            bounds = _even_cuts(start, end, pieces, min_f)
            if use_smart and pieces > 1:
                smart_bounds = _smart_split_bounds(start, end, pieces, bounds,
                                                   change_signal, min_f, max_f)
                if smart_bounds is not None:
                    if smart_bounds != bounds:
                        snapped_count += 1
                    bounds = smart_bounds
                    smart_used = True
            for i in range(len(bounds) - 1):
                if bounds[i + 1] > bounds[i]:
                    split.append([bounds[i], bounds[i + 1]])
        if snapped_count:
            reporter.log(f"智能切分：{snapped_count} 个超长段已吸附到画面变化峰值处")
        items = split

    result = [
        SceneSpan(index=i, start_frame=start, end_frame=end, fps=fps)
        for i, (start, end) in enumerate(items, start=1)
        if end > start
    ]
    if len(result) != len(scenes):
        reporter.log(f"片段整理：{len(scenes)} → {len(result)} 段"
                     f"（最短 {min_f if min_f else '不限'} 帧 / 最长 {max_f if max_f else '不限'} 帧）")
    return result, smart_used


# --------------------------------------------------------------------------------------
# 输出命名
# --------------------------------------------------------------------------------------
def render_filename(template: str, video_path: str, scene: SceneSpan,
                    number: int, pad: int) -> str:
    """按模板生成文件名（不含扩展名）。"""
    stem = Path(video_path).stem
    mapping = {
        "$VIDEO_NAME": stem,
        "$SCENE_NUMBER": str(number).zfill(max(1, pad)),
        "$START_TIME": _tc_for_name(scene.start_sec),
        "$END_TIME": _tc_for_name(scene.end_sec),
        "$DURATION": _tc_for_name(scene.duration),
        "$START_FRAME": str(scene.start_frame),
        "$END_FRAME": str(scene.end_frame),
        "$DATE": time.strftime("%Y%m%d"),
    }
    text = template or "$VIDEO_NAME_$SCENE_NUMBER"
    for key, value in mapping.items():
        text = text.replace(key, value)
    return safe_filename(text, f"{stem}_{number:03d}")


def _tc_for_name(seconds: float) -> str:
    msec = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(msec, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}-{m:02d}-{s:02d}-{ms:03d}"


def preview_filename(template: str, pad: int) -> str:
    """给 GUI 用的命名预览。"""
    scene = SceneSpan(index=1, start_frame=0, end_frame=0, fps=25.0)
    return render_filename(template, "我的视频.mp4", scene, 1, pad)


# --------------------------------------------------------------------------------------
# 调用 ffmpeg 切分
# --------------------------------------------------------------------------------------
def build_ffmpeg_args(ffmpeg: str, src: str, dst: str, start: float, duration: float,
                      params: TaskParams, overwrite: bool, frame_count: int = 0,
                      fps: float = 0.0) -> list[str]:
    """构造单段 ffmpeg 命令。

    ``SceneSpan`` 使用半开区间 [start_frame, end_frame)。因此重编码时不能
    只用 ``-t``：时长是浮点数，经过输入/输出时间基换算后可能在边界多解码一帧，
    表现为上一段的尾帧变成下一段的首帧。重编码采用输入后的精确 ``-ss``，并
    同时用 ``-frames:v`` 作为视频帧数上限；无重编码仍保留快速的关键帧切法。
    """
    precise_start = f"{max(0.0, start):.9f}"
    precise_duration = f"{max(0.001, duration):.9f}"
    args = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-y" if overwrite else "-n"]

    if params.reencode:
        # -ss 放在 -i 后面是准确寻址（代价是需要解码并丢弃起点前的帧）。
        # -frames:v 是硬边界，保证输出最多包含目标区间的 N 帧。
        args += ["-i", src, "-ss", precise_start, "-t", precise_duration]
        if frame_count > 0:
            args += ["-frames:v", str(int(frame_count))]
    else:
        # 复制流时优先速度；关键帧限制属于无重编码模式的已知取舍。
        args += ["-ss", precise_start, "-i", src, "-t", precise_duration]

    args += ["-map", "0:v:0", "-map", "0:a?"]
    if params.container == "mkv":
        args += ["-map", "0:s?"]
    if params.reencode:
        args += ["-c:v", "libx264", "-preset", params.preset, "-crf", str(int(params.crf)),
                 "-pix_fmt", "yuv420p"]
        if params.audio == "aac":
            args += ["-c:a", "aac", "-b:a", "192k"]
        elif params.audio == "none":
            args += ["-an"]
        else:
            args += ["-c:a", "copy"]
    else:
        args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
        if params.audio == "none":
            args += ["-an"]
        elif params.audio == "aac":
            args += ["-c:a", "aac", "-b:a", "192k"]
    if params.extra_args.strip():
        import shlex
        args += shlex.split(params.extra_args, posix=True)
    args += [dst]
    return args


def split_video(params: TaskParams, scenes: Sequence[SceneSpan], info: VideoInfo,
                ffmpeg: str, reporter: Reporter,
                out_dir: str, prev_outputs: Sequence[str] = (),
                scene_outputs: Optional[list[str]] = None) -> tuple[list[str], list[str]]:
    """逐个场景调用 ffmpeg。

    返回实际成功生成的文件与问题列表；若传入 ``scene_outputs``，会额外填充
    一个与 scenes 等长的映射，跳过/失败项为空串，避免 CSV/结果表把第 N 个
    成功文件错误地标到第 N 个场景上。
    """
    os.makedirs(out_dir, exist_ok=True)
    ext = params.container
    outputs: list[str] = []
    problems: list[str] = []
    used: set[str] = set()
    if scene_outputs is not None:
        scene_outputs.clear()
    total = len(scenes)
    reporter.stage("正在切割视频…")

    for i, scene in enumerate(scenes, start=1):
        if reporter.cancelled():
            raise Cancelled()
        number = params.start_number + i - 1
        name = render_filename(params.template, params.video_path, scene, number, params.pad)
        dst = os.path.join(out_dir, f"{name}.{ext}")

        # 同一次任务内重名（例如模板里没有 $SCENE_NUMBER）自动加序号，避免互相覆盖
        # 注意：used 里存的是完整路径，比较时也要用完整路径，否则第 3 个起的重名仍会覆盖
        if dst in used:
            base_name = name
            k = 1
            while os.path.join(out_dir, f"{base_name}_{k}.{ext}") in used:
                k += 1
            name = f"{base_name}_{k}"
            dst = os.path.join(out_dir, f"{name}.{ext}")
        if os.path.exists(dst) and not params.overwrite:
            problems.append(f"第 {i} 段目标文件已存在，已跳过：{os.path.basename(dst)}")
            if scene_outputs is not None:
                scene_outputs.append("")
            reporter.log(problems[-1], "warn")
            reporter.progress(i, total)
            continue
        used.add(dst)

        args = build_ffmpeg_args(ffmpeg, params.video_path, dst, scene.start_sec,
                                 scene.duration, params, params.overwrite,
                                 frame_count=scene.frame_count, fps=info.fps)
        reporter.log(f"[{i}/{total}] {os.path.basename(dst)}  "
                     f"{format_timecode(scene.start_sec)} → {format_timecode(scene.end_sec)}"
                     f"（{format_duration(scene.duration)}，{scene.frame_count} 帧）")
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    **_no_window_kwargs())
        except FileNotFoundError as exc:
            raise RuntimeError(f"无法启动 ffmpeg：{exc}") from exc
        while True:
            try:
                proc.wait(timeout=0.3)
                break
            except subprocess.TimeoutExpired:
                if reporter.cancelled():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise Cancelled()
        _, err = proc.communicate()
        if proc.returncode != 0:
            message = (err or b"").decode("utf-8", "ignore").strip().splitlines()
            detail = message[-1] if message else f"ffmpeg 返回码 {proc.returncode}"
            if os.path.exists(dst) and os.path.getsize(dst) == 0:
                os.remove(dst)
            if scene_outputs is not None:
                scene_outputs.append("")
            problems.append(f"第 {i} 段分割失败：{detail}")
            reporter.log(f"第 {i} 段分割失败：{detail}", "error")
        else:
            outputs.append(dst)
            if scene_outputs is not None:
                scene_outputs.append(dst)
            reporter.log(f"    完成：{format_bytes(os.path.getsize(dst)) if os.path.exists(dst) else '?'}")
        reporter.progress(i, total)

    return outputs, problems


def extract_preview_clip(video_path: str, start_sec: float, duration_sec: float,
                         ffmpeg: str = "") -> str:
    """用 -c copy 快速截取 [start, start+duration) 为临时 mp4，用于外部试看。

    只做流复制、不重编码，速度取决于磁盘 IO，通常几秒内完成。临时文件放在系统
    临时目录下的 scene_splitter_preview/ 中，超过 24 小时的旧文件会被顺手清理。
    返回生成文件的完整路径，失败时抛 RuntimeError。
    """
    import tempfile

    ff = ffmpeg or resolve_ffmpeg("")
    if not ff:
        raise RuntimeError("未找到 ffmpeg，无法截取试看片段。")
    tmp_dir = Path(tempfile.gettempdir()) / "scene_splitter_preview"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for child in tmp_dir.glob("*"):
        try:
            if child.is_file() and now - child.stat().st_mtime > 86400:
                child.unlink()
        except Exception:
            pass
    stem = safe_filename(Path(video_path).stem)
    start = max(0.0, float(start_sec))
    duration = max(0.1, float(duration_sec))
    out = tmp_dir / f"{stem}_{start:.2f}-{start + duration:.2f}_preview.mp4"
    args = [ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-i", video_path,
            "-t", f"{duration:.3f}", "-c", "copy",
            "-avoid_negative_ts", "make_zero", str(out)]
    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=180, **_no_window_kwargs())
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("截取超时（超过 180 秒），该段可能过长。") from exc
    if proc.returncode != 0 or not out.exists():
        detail = (proc.stderr or b"").decode("utf-8", "ignore").strip().splitlines()
        raise RuntimeError(f"截取试看片段失败：{detail[-1] if detail else '未知错误'}")
    return str(out)


def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


# --------------------------------------------------------------------------------------
# 报告导出
# --------------------------------------------------------------------------------------
def export_csv(path: str, scenes: Sequence[SceneSpan], outputs: Sequence[str],
               video_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["源视频", os.path.basename(video_path)])
        writer.writerow(["序号", "起始帧", "结束帧", "起始时间", "结束时间", "时长(秒)",
                         "帧数", "起始时码", "结束时码", "输出文件"])
        for i, scene in enumerate(scenes):
            out = os.path.basename(outputs[i]) if i < len(outputs) else ""
            writer.writerow([scene.index, scene.start_frame, scene.end_frame,
                             f"{scene.start_sec:.3f}", f"{scene.end_sec:.3f}",
                             f"{scene.duration:.3f}", scene.frame_count,
                             scene.start_tc, scene.end_tc, out])
    return path


_HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>场景分割报告 - {title}</title>
<style>
 body {{ font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
        background:#f3f3f3; color:#1b1b1b; margin:0; padding:24px; }}
 h1 {{ font-size:20px; font-weight:600; margin:0 0 6px; }}
 .meta {{ color:#616161; font-size:13px; margin-bottom:18px; }}
 table {{ border-collapse:collapse; width:100%; background:#fff; font-size:13px;
          box-shadow:0 2px 6px rgba(0,0,0,.08); border-radius:6px; overflow:hidden; }}
 th, td {{ padding:8px 12px; text-align:left; border-bottom:1px solid #ececec; }}
 th {{ background:#0067c0; color:#fff; font-weight:600; }}
 tr:hover td {{ background:#f0f6fc; }}
 .thumbs img {{ height:90px; margin:2px; border-radius:4px; }}
 code {{ background:#eee; padding:1px 4px; border-radius:3px; }}
</style></head><body>
<h1>视频场景分割报告</h1>
<div class="meta">源视频：<code>{src}</code>　|　共 <b>{count}</b> 段　|　总时长 {total}
　|　生成时间 {now}</div>
<table><thead><tr>
<th>#</th><th>起始时间</th><th>结束时间</th><th>时长</th><th>帧数</th><th>输出文件</th>{thumb_head}
</tr></thead><tbody>
"""


def export_html(path: str, scenes: Sequence[SceneSpan], video_path: str,
                outputs: Sequence[str] = (), images: Optional[dict] = None) -> str:
    """生成中文 HTML 报告（可选内嵌缩略图）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    images = images or {}
    show_thumbs = bool(images)
    source_name = html_escape(os.path.basename(video_path))
    html = [_HTML_HEAD.format(
        title=source_name, src=source_name,
        count=len(scenes), total=format_duration(sum(s.duration for s in scenes)),
        now=time.strftime("%Y-%m-%d %H:%M:%S"),
        thumb_head="<th>预览</th>" if show_thumbs else "")]
    for i, scene in enumerate(scenes):
        out = html_escape(os.path.basename(outputs[i])) if i < len(outputs) and outputs[i] else ""
        row = (f"<tr><td>{scene.index}</td><td>{scene.start_tc}</td><td>{scene.end_tc}</td>"
               f"<td>{scene.duration:.2f} 秒</td><td>{scene.frame_count}</td>"
               f"<td>{out}</td>")
        if show_thumbs:
            imgs = images.get(scene.index, [])
            row += "<td class='thumbs'>" + "".join(
                f'<img src="{html_escape(os.path.relpath(p, os.path.dirname(os.path.abspath(path))).replace(os.sep, "/"), quote=True)}">'
                for p in imgs) + "</td>"
        html.append(row + "</tr>")
    html.append("</tbody></table></body></html>")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(html))
    return path


def save_thumbnails(scenes: Sequence[SceneSpan], video, out_dir: str, count: int,
                    reporter: Reporter) -> dict:
    """保存每个场景的缩略图，返回 {场景序号: [图片路径]}。"""
    if _sd_save_images is None:
        reporter.log("当前 PySceneDetect 版本不支持缩略图导出，已跳过。", "warn")
        return {}
    pairs = [(FrameTimecode(s.start_frame, fps=s.fps), FrameTimecode(s.end_frame, fps=s.fps))
             for s in scenes]
    image_dir = os.path.join(out_dir, "缩略图")
    os.makedirs(image_dir, exist_ok=True)
    reporter.stage("正在导出缩略图…")
    result = _sd_save_images(
        scene_list=pairs,
        video=video,
        num_images=max(1, int(count)),
        frame_margin=1,
        image_extension="jpg",
        encoder_param=90,
        image_name_template="$VIDEO_NAME-$SCENE_NUMBER-$IMAGE_NUMBER",
        output_dir=image_dir,
        show_progress=False,
        scale=0.4,
    )
    return result or {}


# --------------------------------------------------------------------------------------
# 一个完整的单文件任务
# --------------------------------------------------------------------------------------
def plan_task(params: TaskParams, reporter: Reporter,
              result: TaskResult | None = None) -> TaskResult:
    """第一阶段：只做「检测 + 分段整理」，不切割视频、不写报告。

    用于「先预览、确认后再分割」的工作流：预览到的段列表会被原样交给
    :func:`execute_plan` 执行，保证“看到的”和“切出来的”完全一致。
    """
    started = time.time()
    result = result or TaskResult(video_path=params.video_path)
    result.clips_ready = False

    def log(message: str, level: str = "info"):
        reporter.log(message, level)
        result.log_lines.append(message)

    errors = validate_params(params)
    if errors:
        raise ValueError("\n".join(errors))

    info, scenes, video, change_signal = detect(params, reporter)
    result.info = info

    range_start = int(round(max(0.0, params.start_time) * info.fps)) if info.fps else 0
    range_end = int(round((params.end_time if params.end_time > 0 else info.duration) * info.fps)) \
        if info.fps else info.frame_count
    if info.frame_count:
        range_start = min(max(0, range_start), info.frame_count)
        range_end = min(max(range_start, range_end), info.frame_count)

    reporter.stage("整理片段…")
    scenes, smart_used = apply_limits(scenes, info.fps, params, reporter, info.frame_count,
                                      range_start, range_end, change_signal)
    result.smart_used = smart_used
    if not scenes:
        log("未检测到任何场景切换（当前处理范围视为一个镜头）。", "warn")
        fallback_end = range_end or info.frame_count or max(range_start + 1, 1)
        if fallback_end > range_start:
            scenes = [SceneSpan(1, range_start, fallback_end, info.fps)]
    result.scenes = scenes
    log(f"分段规划完成：{len(scenes)} 段，总时长 {format_duration(sum(s.duration for s in scenes))}")
    try:
        video.reset()
    except Exception:
        pass
    result.elapsed = time.time() - started
    return result


def execute_plan(params: TaskParams, result: TaskResult, reporter: Reporter,
                 ffmpeg_path: str = "") -> TaskResult:
    """第二阶段：按给定（已预览/已确认）的段列表切割视频并导出报告。"""
    started = time.time()
    scenes = list(result.scenes)
    # execute_plan 可能被同一份预览结果重复执行；每次执行都应从干净的
    # 输出状态开始，不能把上一次运行的文件/报告带进本次结果。
    result.outputs = []
    result.scene_outputs = []
    result.skipped = []
    result.csv_path = ""
    result.html_path = ""
    result.image_dir = ""
    info = result.info
    if info is None or not scenes:
        raise ValueError("没有可执行的分段列表，请先执行「预览分段」。")

    def log(message: str, level: str = "info"):
        reporter.log(message, level)
        result.log_lines.append(message)

    out_dir = params.output_dir or os.path.dirname(os.path.abspath(params.video_path))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    stem = Path(params.video_path).stem
    images: dict = {}

    if params.save_csv:
        result.csv_path = export_csv(
            os.path.join(out_dir, f"{stem}_场景列表_{stamp}.csv"), scenes, (), params.video_path)
        log(f"已导出 CSV：{result.csv_path}")

    if params.save_images or params.save_html:
        try:
            video = open_video(params.video_path)
            images = save_thumbnails(scenes, video, out_dir, params.image_count, reporter)
            result.image_dir = os.path.join(out_dir, "缩略图")
            log(f"缩略图已保存到：{result.image_dir}")
            try:
                video.reset()
            except Exception:
                pass
        except Exception as exc:  # 缩略图失败不影响主流程
            log(f"缩略图导出失败：{exc}", "warn")

    if params.do_split:
        if reporter.cancelled():
            raise Cancelled()
        ffmpeg = resolve_ffmpeg(ffmpeg_path)
        if not ffmpeg:
            raise RuntimeError(
                "未找到 ffmpeg，无法分割视频。\n"
                "解决办法（任选其一）：\n"
                "  1) 执行：python -m pip install -U imageio-ffmpeg\n"
                "  2) 下载 ffmpeg 官方 Windows 版并把 ffmpeg.exe 所在目录加入 PATH\n"
                "  3) 在「设置」页手动指定 ffmpeg.exe 的路径"
            )
        log(f"使用 ffmpeg：{ffmpeg}")
        scene_outputs: list[str] = []
        outputs, problems = split_video(params, scenes, info, ffmpeg, reporter, out_dir,
                                        scene_outputs=scene_outputs)
        result.outputs = outputs
        result.scene_outputs = scene_outputs
        result.skipped = problems
        for item in problems:
            log(item, "warn")
        log(f"分割完成：成功 {len(outputs)} 段，异常 {len(problems)} 段。")
    else:
        result.scene_outputs = [""] * len(scenes)
        log("已按“仅检测”模式运行，未输出视频文件。")

    # 此时已知每个场景对应的输出文件，重新导出 CSV。
    output_map = result.scene_outputs or result.outputs
    if params.save_csv and result.csv_path:
        export_csv(result.csv_path, scenes, output_map, params.video_path)
    if params.save_html:
        try:
            result.html_path = export_html(
                os.path.join(out_dir, f"{stem}_场景报告_{stamp}.html"),
                scenes, params.video_path, output_map, images)
            log(f"已导出 HTML 报告：{result.html_path}")
        except Exception as exc:
            log(f"HTML 报告导出失败：{exc}", "warn")

    result.clips_ready = True
    result.elapsed = time.time() - started
    log(f"全部完成，耗时 {format_duration(result.elapsed)}。")
    return result


def run_task(params: TaskParams, reporter: Reporter, ffmpeg_path: str = "") -> TaskResult:
    """一次性完成「检测 → 整理 → 分割 → 导出报告」（批量处理/命令行调用使用）。"""
    result = plan_task(params, reporter)
    result.clips_ready = False
    return execute_plan(params, result, reporter, ffmpeg_path)


# --------------------------------------------------------------------------------------
# 分段结果统计与预览取帧
# --------------------------------------------------------------------------------------
def summarize(scenes: Sequence[SceneSpan], params: TaskParams | None = None,
              fps: float = 0.0) -> dict:
    """统计分段结果，供预览面板展示与校验提示使用。"""
    if not scenes:
        return {"count": 0, "total": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0,
                "warnings": [], "total_frames": 0}
    durations = [s.duration for s in scenes]
    frames = [s.frame_count for s in scenes]
    total = sum(durations)
    warnings: list[str] = []

    if params is not None:
        min_f, max_f = _limits_to_frames(fps or scenes[0].fps, params.min_scene_sec,
                                         params.max_scene_sec, params.min_scene_frames,
                                         params.max_scene_frames)
        if min_f > 0:
            short = [s.index for s in scenes if s.frame_count < min_f]
            if short:
                warnings.append(f"有 {len(short)} 段短于设定的最少 {min_f} 帧（已合并到极限，"
                                f"第 {short[0]} 段仍偏短）")
        if max_f > 0:
            long = [s.index for s in scenes if s.frame_count > max_f]
            if long:
                warnings.append(f"有 {len(long)} 段超过设定的最多 {max_f} 帧（受最短限制约束无法再切）")
        if len(scenes) == 1 and len(durations) == 1:
            warnings.append("整段视频只规划出 1 段，可尝试调小检测阈值或减小「内部最短场景」")

    # 连续性检查：正常情况下切片首尾相接；手动删除片段后会留下空档
    gaps = []
    for previous, current in zip(scenes, scenes[1:]):
        if previous.end_frame != current.start_frame:
            missing = current.start_frame - previous.end_frame
            if missing > 0:
                gaps.append((previous.index, current.index, missing))
    if gaps:
        total_missing = sum(g[2] for g in gaps)
        warnings.append(f"第 {gaps[0][0]} 段与第 {gaps[0][1]} 段之间有 {gaps[0][2]} 帧空档"
                        + (f"（另有 {len(gaps) - 1} 处，共跳过 {total_missing} 帧）" if len(gaps) > 1 else
                           "（已手动删除片段，这部分画面不会输出）"))

    return {
        "count": len(scenes),
        "total": total,
        "min": min(durations),
        "max": max(durations),
        "avg": total / len(durations),
        "min_frames": min(frames),
        "max_frames": max(frames),
        "avg_frames": sum(frames) / len(frames),
        "total_frames": sum(frames),
        "warnings": warnings,
    }


def grab_frames(video_path: str, start_frame: int, end_frame: int, count: int = 3,
                max_width: int = 320) -> list:
    """抓取某一段的首/中/尾画面，用于预览（返回 BGR numpy 数组列表）。"""
    count = max(1, int(count))
    try:
        import cv2
    except Exception:
        return []
    length = max(1, end_frame - start_frame)
    # 预览的目的之一是检查切点，所以首/尾必须是真实边界帧：
    # 场景区间为 [start_frame, end_frame)，尾帧就是 end_frame - 1。
    positions = []
    for i in range(count):
        if count == 1:
            position = start_frame + (length - 1) // 2
        else:
            position = start_frame + int(round((length - 1) * i / (count - 1)))
        positions.append(min(max(position, start_frame), max(start_frame, end_frame - 1)))
    frames = []
    capture = None
    try:
        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            return []
        for position in positions:
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(position))
            ok, frame = capture.read()
            if ok and frame is not None:
                height, width = frame.shape[:2]
                if width > max_width:
                    scale = max_width / float(width)
                    frame = cv2.resize(frame, (int(width * scale), int(height * scale)),
                                       interpolation=cv2.INTER_AREA)
                frames.append(frame)
    except Exception:
        pass
    finally:
        if capture is not None:
            capture.release()
    return frames
