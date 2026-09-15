# -*- coding: utf-8 -*-
"""分段视频试看播放器：选中预览表格中的某一段，直接播放该段的区间画面（含声音）。

两套能力：
  · 内嵌播放 —— QMediaPlayer + QVideoWidget，选中即载入该段区间，可播放/循环/
    拖进度，还能「播切点前后 ±2 秒」检查转场是否干净；
  · 外部试看 —— 用 ffmpeg 以 -c copy 秒级截取该段为临时文件，再用系统默认播放器
    打开（所见即所得；内嵌播不了的格式也可用它兜底）。

注意：QtMultimedia 按需导入，缺失时内嵌播放区域会降级为提示文本，外部试看不受影响。
"""

from __future__ import annotations

import math
import os

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtWidgets import (QAbstractSlider, QApplication, QHBoxLayout, QLabel, QSlider,
                               QStyle, QStyleOptionSlider, QVBoxLayout, QWidget)
from qfluentwidgets import CaptionLabel, FluentIcon, PushButton, StrongBodyLabel, ToolButton

try:  # 旧版/精简版 PySide6 可能没有 CheckBox，用 Qt 原生复选框兜底
    from qfluentwidgets import CheckBox as _CheckBox
except Exception:  # pragma: no cover
    from PySide6.QtWidgets import QCheckBox as _CheckBox

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    MULTIMEDIA_AVAILABLE = True
except Exception:  # pragma: no cover - 缺多媒体后端时降级
    MULTIMEDIA_AVAILABLE = False
    QAudioOutput = None  # type: ignore
    QMediaPlayer = None  # type: ignore
    QVideoWidget = None  # type: ignore

VIDEO_HEIGHT = 300
AROUND_SECONDS = 2.0
PULSE_TICKS = 2      # 刷新点播收到几个进度 tick 就停
PULSE_TIMEOUT = 600  # tick 不来时的兜底（毫秒）


def _ceil_ms(seconds: float) -> int:
    """秒→毫秒（向上取整，容忍浮点误差）：seek 必须落在目标帧区间内。

    直接 int() 截断在 NTSC（29.97fps）等非整数帧率下会掉进上一帧，
    表现为选中段未播时显示的是上段尾帧。
    """
    return max(0, int(math.ceil(seconds * 1000 - 1e-6)))


class _SeekSlider(QSlider):
    """点按即跳到点击处的进度条（QSlider 默认点按只按页步进）。"""

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)  # 先保留 sliderPressed/拖拽状态
        if event.button() == Qt.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            groove = self.style().subControlRect(QStyle.CC_Slider, opt,
                                                 QStyle.SC_SliderGroove, self)
            handle = self.style().subControlRect(QStyle.CC_Slider, opt,
                                                 QStyle.SC_SliderHandle, self)
            span = groove.width() - handle.width()
            if span > 0:
                x = int(event.position().x()) - groove.x() - handle.width() // 2
                self.setValue(QStyle.sliderValueFromPosition(
                    self.minimum(), self.maximum(), x, span))


class ExtractThread(QThread):
    """后台截取试看片段（避免大文件/慢磁盘时卡住界面）。"""

    done = Signal(str)    # 成功：临时文件路径
    failed = Signal(str)  # 失败：错误信息

    def __init__(self, src: str, start: float, duration: float, ffmpeg: str, parent=None):
        super().__init__(parent)
        self._src = src
        self._start = start
        self._duration = duration
        self._ffmpeg = ffmpeg

    def run(self) -> None:  # noqa: D401
        try:
            from . import core
            path = core.extract_preview_clip(self._src, self._start, self._duration, self._ffmpeg)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        else:
            self.done.emit(path)


class SegmentPlayer(QWidget):
    """预览页内的区间播放器，一次只关心「当前选中的一段」。"""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self._video_path = ""
        self._row = -1
        self._seg_start = 0.0
        self._seg_end = 0.0
        self._play_start = 0   # 当前播放区间（毫秒，可能是本段，也可能是切点前后）
        self._play_end = 0
        self._pending_seek: int | None = None
        self._pulse_active = False  # 刷新点播中（暂停寻址后静音点播一下逼出目标帧）
        self._pulse_ticks = 0
        self._pulse_pos = 0
        self._volume = 0.8  # 非静音时的音量（静音靠拉到 0 实现，见 _apply_mute）
        self._duration_ms = 0
        self._extract_thread: ExtractThread | None = None
        _app = QApplication.instance()
        if _app is not None:
            _app.aboutToQuit.connect(self._stop_extract_thread)
        self._player = None
        self._audio = None
        # PAUSE 图标在不同版本中名字可能有差异，取不到就沿用 PLAY，保证不崩
        self._pause_icon = getattr(FluentIcon, "PAUSE", FluentIcon.PLAY)
        self._step_callback = None  # 由 split_page 注入：切段时改表格选中行
        self._ops_callback = None  # 由 split_page 注入：本段删除/合并
        self._build_ui()
        if MULTIMEDIA_AVAILABLE:
            self._build_media()
        else:
            self._set_status("当前环境缺少 QtMultimedia，内嵌播放不可用，"
                                     "请使用「外部播放器试看」。")
        self._set_controls_enabled(False)
        self._fit_height()  # 先按估算宽度定高，显示/缩放时再按真实宽度重算

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 4)
        layout.setSpacing(8)

        self.titleLabel = StrongBodyLabel("片段视频试看", self)
        self.hintLabel = CaptionLabel(
            "选中预览表格中的某一段，这里会载入该段区间，可直接播放确认（含声音、可循环）；"
            "「播切点前后 ±2 秒」适合检查转场切得干不干净。", self)
        self.hintLabel.setWordWrap(True)
        layout.addWidget(self.titleLabel)
        layout.addWidget(self.hintLabel)

        if MULTIMEDIA_AVAILABLE:
            self.videoWidget = QVideoWidget(self)
            self.videoWidget.setFixedHeight(VIDEO_HEIGHT)
        else:
            self.videoWidget = QLabel("内嵌播放不可用（缺少 QtMultimedia）", self)
            self.videoWidget.setAlignment(Qt.AlignCenter)
            self.videoWidget.setFixedHeight(120)
        layout.addWidget(self.videoWidget)

        # —— 播放控制行 ——
        self._controlsRow = QWidget(self)
        controls = self._controlsRow
        row = QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.playBtn = ToolButton(FluentIcon.PLAY, self)
        self.playBtn.setToolTip("播放 / 暂停")
        self.playBtn.setFixedSize(36, 32)
        self.timeLabel = CaptionLabel("0.0 / 0.0 秒", self)
        self.timeLabel.setMinimumWidth(110)
        self.slider = _SeekSlider(Qt.Horizontal, self)
        self.slider.setRange(0, 0)
        self.slider.setRepeatAction(QAbstractSlider.SliderNoAction)  # 按住沟槽不自动连播页步进
        _loop, _mute = self._load_play_prefs()
        self.loopBox = _CheckBox("循环", self)
        self.loopBox.setChecked(_loop)
        self.loopBox.setToolTip("播到段尾后自动回到段首继续播")
        self.muteBox = _CheckBox("静音", self)
        self.muteBox.setChecked(_mute)
        row.addWidget(self.playBtn)
        row.addWidget(self.timeLabel)
        row.addWidget(self.slider, 1)
        row.addWidget(self.loopBox)
        row.addWidget(self.muteBox)
        layout.addWidget(controls)

        # —— 试看动作行 ——
        self._actionsRow = QWidget(self)
        actions = self._actionsRow
        arow = QHBoxLayout(actions)
        arow.setContentsMargins(0, 0, 0, 0)
        arow.setSpacing(8)
        self.aroundBtn = PushButton("播切点前后 ±2 秒", self)
        self.aroundBtn.setToolTip("播放「本段起点前 2 秒 → 本段终点后 2 秒」，检查转场")
        self.prevBtn = self._nav_button("LEFT_ARROW", "◀", "上一段",
                                        "切换到上一段（与预览表格联动；在播的话新段接着播）")
        self.wholeBtn = PushButton("只播本段", self)
        self.wholeBtn.setToolTip("把播放区间恢复为当前选中的一段")
        self.nextBtn = self._nav_button("RIGHT_ARROW", "▶", "下一段",
                                        "切换到下一段（与预览表格联动；在播的话新段接着播）")
        self.externalBtn = PushButton(FluentIcon.MOVIE, "外部播放器试看", self)
        self.externalBtn.setToolTip("用 ffmpeg 秒级截取该段为临时文件，再用系统默认播放器打开")
        arow.addWidget(self.aroundBtn)
        arow.addWidget(self.prevBtn)
        arow.addWidget(self.wholeBtn)
        arow.addWidget(self.nextBtn)
        arow.addWidget(self.externalBtn)
        arow.addStretch(1)
        layout.addWidget(actions)

        # —— 本段操作行（看完即点，不用滚回表格上方） ——
        self._opsRow = QWidget(self)
        ops = self._opsRow
        orow = QHBoxLayout(ops)
        orow.setContentsMargins(0, 0, 0, 0)
        orow.setSpacing(8)
        self.deleteBtn = PushButton(FluentIcon.DELETE, "删除本段", self)
        self.deleteBtn.setToolTip("删除当前选中的段（快捷键 Delete）")
        self.mergeBtn = PushButton(FluentIcon.MOVE, "与上一段合并", self)
        self.mergeBtn.setToolTip("将当前段与上一段合并（快捷键 M）")
        orow.addWidget(self.deleteBtn)
        orow.addWidget(self.mergeBtn)
        orow.addStretch(1)
        layout.addWidget(ops)

        self.statusLabel = CaptionLabel("", self)
        self.statusLabel.setWordWrap(True)
        layout.addWidget(self.statusLabel)

        self.playBtn.clicked.connect(self._toggle_play)
        self.slider.valueChanged.connect(self._on_slider_changed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.muteBox.checkStateChanged.connect(self._on_mute_changed)
        self.loopBox.checkStateChanged.connect(lambda _s: self._save_play_prefs())
        self.aroundBtn.clicked.connect(self.play_around_cut)
        self.prevBtn.clicked.connect(lambda: self._request_step(-1))
        self.wholeBtn.clicked.connect(self.play_whole_segment)
        self.nextBtn.clicked.connect(lambda: self._request_step(1))
        self.externalBtn.clicked.connect(self._extract_external)
        self.deleteBtn.clicked.connect(lambda: self._request_op("delete"))
        self.mergeBtn.clicked.connect(lambda: self._request_op("merge"))

    @staticmethod
    def _nav_button(icon_name: str, fallback_arrow: str, text: str, tip: str) -> PushButton:
        """上下段按钮：老版本 FluentIcon 缺箭头图标时退化为「◀ 上一段」纯文本。"""
        icon = getattr(FluentIcon, icon_name, None)
        btn = PushButton(icon, text) if icon is not None \
            else PushButton(fallback_arrow + " " + text)
        btn.setToolTip(tip)
        return btn

    def _build_media(self) -> None:
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(self._volume)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self.videoWidget)
        if hasattr(self._player, "setNotifyInterval"):  # Qt5 有，Qt6 已移除
            self._player.setNotifyInterval(50)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_error)
        self._apply_mute()  # 让记住的静音在首次载入前就生效

    # ------------------------------------------------------------------ 对外接口
    def segment_index(self) -> int:
        """当前载入的是预览表格的第几行（-1 表示未载入）。"""
        return self._row

    def set_step_callback(self, callback) -> None:
        """注入切段回调（split_page 接管：改表格选中行，播放器随信号跟随）。"""
        self._step_callback = callback

    def is_playing(self) -> bool:
        """当前是否正在播放（供切段时决定新段是否接着播）。"""
        return (MULTIMEDIA_AVAILABLE and self._player is not None
                and self._player.playbackState() == QMediaPlayer.PlayingState)

    def _request_step(self, delta: int) -> None:
        if callable(self._step_callback):
            self._step_callback(delta)

    def set_ops_callback(self, callback) -> None:
        """注入本段操作回调（split_page 接管：复用表格选中行的删除/合并）。"""
        self._ops_callback = callback

    def _request_op(self, action: str) -> None:
        if callable(self._ops_callback):
            self._ops_callback(action)

    def load_segment(self, video_path: str, row: int, start_sec: float, end_sec: float) -> None:
        """载入某一段（切到段首并暂停显示首帧，不自动播放）。"""
        if end_sec <= start_sec:
            end_sec = start_sec + 0.5
        self._video_path = video_path or ""
        self._row = row
        self._seg_start = max(0.0, start_sec)
        self._seg_end = max(self._seg_start + 0.1, end_sec)
        self._set_interval(_ceil_ms(self._seg_start), _ceil_ms(self._seg_end))
        self.titleLabel.setText(
            f"片段视频试看 — 第 {row + 1} 段（{self._seg_start:.2f}s → {self._seg_end:.2f}s）")
        self._set_status("")
        self._set_controls_enabled(True)
        if not MULTIMEDIA_AVAILABLE or self._player is None:
            return
        url = QUrl.fromLocalFile(os.path.abspath(video_path))
        if self._player.source() != url:
            self._pending_seek = self._play_start
            self._player.setSource(url)
            self._update_time_label(self._play_start)
        else:
            self._paused_seek(self._play_start)

    def play_whole_segment(self) -> None:
        """把播放区间恢复为本段并从段首播。」"""
        if self._row < 0:
            return
        self._set_interval(_ceil_ms(self._seg_start), _ceil_ms(self._seg_end))
        self._set_status("")
        self._seek_and_play(self._play_start)

    def play_around_cut(self) -> None:
        """播放「段首前 2 秒 → 段尾后 2 秒」，检查转场。"""
        if self._row < 0:
            return
        start = max(0.0, self._seg_start - AROUND_SECONDS)
        end = self._seg_end + AROUND_SECONDS
        if self._duration_ms > 0:
            end = min(end, self._duration_ms / 1000.0)
        self._set_interval(_ceil_ms(start), _ceil_ms(end))
        self._set_status(f"正在播放切点前后 ±{AROUND_SECONDS:.0f} 秒"
                                 f"（{start:.2f}s → {end:.2f}s），点「只播本段」可恢复。")
        self._seek_and_play(self._play_start)

    def clear(self) -> None:
        """清空播放器（预览失效/切换视频时调用，同时释放文件占用）。"""
        self._pulse_active = False
        self._pulse_ticks = 0
        self._pulse_pos = 0
        self._apply_mute()  # 脉冲中被清空：静音恢复勾选值，避免下次有声变无声
        if self._player is not None:
            try:
                self._player.pause()
                self._player.setSource(QUrl())
            except Exception:
                pass
        self._video_path = ""
        self._row = -1
        self._seg_start = self._seg_end = 0.0
        self._play_start = self._play_end = 0
        self._pending_seek = None
        self._duration_ms = 0
        self.slider.setRange(0, 0)
        self.titleLabel.setText("片段视频试看")
        self._set_status("")
        self._update_time_label(0)
        self._set_controls_enabled(False)

    # ------------------------------------------------------------------ 内部：区间与播放
    def _set_interval(self, start_ms: int, end_ms: int) -> None:
        if end_ms <= start_ms:
            end_ms = start_ms + 100
        self._play_start = max(0, start_ms)
        self._play_end = end_ms
        self.slider.blockSignals(True)
        self.slider.setRange(0, self._play_end - self._play_start)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self._update_time_label(self._play_start)

    def _seek_and_play(self, pos_ms: int) -> None:
        if not MULTIMEDIA_AVAILABLE or self._player is None or self._row < 0:
            return
        self._end_pulse(pause=False)  # 显式播放接管：关掉刷新点播，保持播
        self._preset_volume_for_play()
        if self._player.source().toLocalFile() == "":
            self.load_segment(self._video_path, self._row, self._seg_start, self._seg_end)
            return
        self._player.setPosition(pos_ms)
        self._player.play()

    def _paused_seek(self, pos_ms: int) -> None:
        """暂停态寻址：seek 后静音点播一下，逼后端把目标帧送上画面。

        部分后端暂停时 seek 不刷新画面，会残留旧帧；点播收到几个进度
        tick（解码已流动）立刻停，600ms 兜底，用户无感知。
        """
        if not MULTIMEDIA_AVAILABLE or self._player is None:
            return
        self._pulse_pos = int(pos_ms)
        self._player.setPosition(int(pos_ms))
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._pulse_ticks = 0  # 在播（含脉冲中）：新位置已 set，只重置计数
            return
        self._pulse_active = True
        self._pulse_ticks = 0
        self._apply_mute()  # 脉冲期间强制静音
        self._player.play()
        QTimer.singleShot(PULSE_TIMEOUT, self._end_pulse)

    def _end_pulse(self, pause: bool = True) -> None:
        """结束刷新点播（定时兜底也会调到，已结束则无操作）。"""
        if not self._pulse_active:
            return
        self._pulse_active = False
        self._pulse_ticks = 0
        if not pause:
            self._apply_mute()  # 显式播放接管：按勾选给音量
        if (pause and self._player is not None
                and self._player.playbackState() == QMediaPlayer.PlayingState):
            self._player.pause()  # 音量已是 0 且保持 0，停稳后状态信号再确认一次
        # 点播期间界面静默：结束时把进度条/时间拨到目标处
        if self._player is not None and self._row >= 0:
            target = max(self._play_start, min(self._pulse_pos, self._play_end))
            if not self.slider.isSliderDown():
                self.slider.blockSignals(True)
                self.slider.setValue(target - self._play_start)
                self.slider.blockSignals(False)
            self._update_time_label(target)

    def _toggle_play(self) -> None:
        if not MULTIMEDIA_AVAILABLE or self._player is None or self._row < 0:
            return
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            if self._pulse_active:
                # 用户在刷新点播中按了播放：转为正常播放（不停）
                self._end_pulse(pause=False)
                return
            self._player.pause()
            return
        self._end_pulse(pause=False)
        self._preset_volume_for_play()
        pos = self._player.position()
        if pos < self._play_start or pos >= self._play_end - 120:
            self._player.setPosition(self._play_start)
        self._player.play()

    def _apply_mute(self) -> None:
        """重算静音：flag=勾选/点播；gain 在暂停态也压到 0。

        暂停态常驻 0 音量：点播 play 时后端音量早已 settling，从构造上无声，
        不赌“音量指令先于播放生效”的时序；只在显式播放时给音量。
        """
        if self._player is None or self._audio is None:
            return
        try:
            flag = self.muteBox.isChecked() or self._pulse_active
            playing = self._player.playbackState() == QMediaPlayer.PlayingState
            self._audio.setMuted(flag)
            # 部分后端（Windows）的 setMuted 无效：以音量 0 为准
            self._audio.setVolume(0.0 if (flag or not playing) else self._volume)
        except Exception:
            pass

    def _preset_volume_for_play(self) -> None:
        """显式播放前预置音量（命令排在 play 之前，尽量让后端先落地）。"""
        if self._audio is not None:
            try:
                self._audio.setVolume(0.0 if self.muteBox.isChecked() else self._volume)
            except Exception:
                pass

    @staticmethod
    def _load_play_prefs() -> tuple:
        """读取记住的循环/静音（读不到就用默认值：循环开、静音关）。"""
        try:
            from .config import CFG, qconfig
            return bool(qconfig.get(CFG.loopPreview)), bool(qconfig.get(CFG.mutePreview))
        except Exception:
            return True, False

    def _on_mute_changed(self, _state=None) -> None:
        self._apply_mute()
        self._save_play_prefs()

    def _save_play_prefs(self) -> None:
        """记住循环/静音，下次启动保持。"""
        try:
            from .config import CFG, qconfig, save_config
            qconfig.set(CFG.loopPreview, self.loopBox.isChecked())
            qconfig.set(CFG.mutePreview, self.muteBox.isChecked())
            save_config()
        except Exception:
            pass

    def _on_state_changed(self, _state) -> None:
        if self._player is None:
            return
        self._apply_mute()  # 状态变了就重算 gain（暂停⇒0，播⇒按勾选）
        if self._pulse_active:
            return  # 刷新点播期间不翻播放键（静默）
        playing = self._player.playbackState() == QMediaPlayer.PlayingState
        self.playBtn.setIcon(self._pause_icon if playing else FluentIcon.PLAY)

    def _on_position(self, pos: int) -> None:
        if self._play_end <= self._play_start:
            return
        total = self._play_end - self._play_start
        if self._pulse_active:
            # 刷新点播：界面静默（不拨进度条/时间），收到几个 tick 或到区间尾就停
            self._apply_mute()  # 重复断言静音（防后端指令丢失/覆盖）
            self._pulse_ticks += 1
            if self._pulse_ticks >= PULSE_TICKS or pos >= self._play_end - 40:
                self._end_pulse()
            return
        # 播到区间尾：循环 or 停住（留一点余量，避免通知间隔粗导致冲过头）
        if self._player is not None and \
                self._player.playbackState() == QMediaPlayer.PlayingState:
            margin = max(20, min(80, total // 4))
            if pos >= self._play_end - margin:
                if self.loopBox.isChecked():
                    self._player.setPosition(self._play_start)
                else:
                    self._player.pause()
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(max(0, min(total, pos - self._play_start)))
            self.slider.blockSignals(False)
        self._update_time_label(pos)

    def _update_time_label(self, pos_ms: int) -> None:
        total = max(0, self._play_end - self._play_start)
        rel = max(0, min(total, pos_ms - self._play_start))
        self.timeLabel.setText(f"{rel / 1000:.1f} / {total / 1000:.1f} 秒")

    def _on_slider_changed(self, value: int) -> None:
        """进度条数值变化：用户拨动即寻址（程序回填已 blockSignals，不会到这里）。

        注意 Qt 行为：isSliderDown 只属于手柄拖拽，沟槽点按不置位、也不发
        sliderPressed/Released——所以点按在这里直接寻址+点播，不等松手。
        """
        if self._player is None or self._row < 0:
            return
        if self._pulse_active:
            self._end_pulse(pause=True)  # 拨动撞上点播：先落回暂停，再处理新位置
        target = self._play_start + int(value)
        if (self.slider.isSliderDown()
                or self._player.playbackState() == QMediaPlayer.PlayingState):
            self._player.setPosition(target)  # 手柄拖拽/在播点按：实时生效，不点播
        else:
            self._paused_seek(target)  # 暂停态点按：寻址并点播刷新

    def _on_slider_released(self) -> None:
        """松开手柄：暂停态下补一次刷新点播（沟槽点按在 changed 里已直接点播）。"""
        if self._player is None or self._row < 0:
            return
        if self._pulse_active:
            return
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            return  # 在播：拖动已实时生效，无需点播
        self._paused_seek(self._play_start + int(self.slider.value()))

    def _on_duration(self, duration_ms: int) -> None:
        self._duration_ms = max(0, int(duration_ms))

    def _on_media_status(self, status) -> None:
        if self._player is None:
            return
        if status == QMediaPlayer.EndOfMedia:
            if self._pulse_active:
                self._end_pulse()  # 刷新点播撞到片尾：直接停，不循环
                return
            # 区间尾超出真实片长时（切点前后模式），靠这里兜底循环/停止
            if self.loopBox.isChecked():
                self._player.setPosition(self._play_start)
                self._player.play()
            else:
                self._player.pause()
        elif status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            if self._pending_seek is not None:
                pos = self._pending_seek
                self._pending_seek = None
                self._paused_seek(pos)

    def _on_error(self, _error, error_string: str = "") -> None:
        self._set_status(
            "内嵌播放失败（该格式可能不受系统解码器支持）：" + (error_string or "未知错误") +
            "请改用「外部播放器试看」。")

    # —— 高度管理（SettingCardGroup 按控件当前高度排布，必须像 InfoCard 一样主动定高，
    #    否则 QVideoWidget 在这种布局里会被压成一条缝） ——
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_height()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit_height()

    def _label_text_height(self, label, width: int) -> int:
        metrics = label.fontMetrics()
        text = label.text()
        if not text:
            return metrics.lineSpacing()
        rect = metrics.boundingRect(0, 0, max(60, width), 0,
                                    int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop), text)
        return max(metrics.lineSpacing(), rect.height())

    def _set_status(self, text: str) -> None:
        self.statusLabel.setText(text or "")
        self._fit_height()

    def _fit_height(self) -> None:
        layout = self.layout()
        if layout is None:
            return
        left, top, _right, bottom = layout.getContentsMargins()
        width = max(self.width() - left - _right, 200)
        hint_h = self._label_text_height(self.hintLabel, width)
        status_h = self._label_text_height(self.statusLabel, width)
        title_h = self.titleLabel.sizeHint().height()
        video_h = VIDEO_HEIGHT if MULTIMEDIA_AVAILABLE else 120
        controls_h = max(self._controlsRow.sizeHint().height(), 32)
        actions_h = max(self._actionsRow.sizeHint().height(), 32)
        ops_h = max(self._opsRow.sizeHint().height(), 32)
        self.hintLabel.setFixedHeight(hint_h)
        self.statusLabel.setFixedHeight(status_h)
        needed = (top + bottom + layout.spacing() * 6 + title_h + hint_h
                  + video_h + controls_h + actions_h + ops_h + status_h)
        if self.height() != needed:
            self.setFixedHeight(needed)
            if self.isVisible():
                self._sync_group_height()

    def _sync_group_height(self) -> None:
        from qfluentwidgets import SettingCardGroup  # 延迟导入，避免循环依赖

        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, SettingCardGroup):
                try:
                    parent.adjustSize()
                except Exception:
                    pass
                return
            parent = parent.parentWidget()

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (self.playBtn, self.slider, self.loopBox, self.muteBox,
                       self.aroundBtn, self.wholeBtn):
            widget.setEnabled(enabled and MULTIMEDIA_AVAILABLE)
        # 上一段/下一段/外部试看/本段操作不依赖 QtMultimedia，载入了段就能用
        # （上下段的边界、操作键的预览/忙碌门控由 split_page 在载入后细调）
        for widget in (self.prevBtn, self.nextBtn, self.externalBtn,
                       self.deleteBtn, self.mergeBtn):
            widget.setEnabled(enabled)

    # ------------------------------------------------------------------ 外部试看
    def _extract_external(self) -> None:
        if self._row < 0 or not self._video_path:
            return
        if self._extract_thread is not None and self._extract_thread.isRunning():
            return
        from . import core
        from .config import CFG, qconfig
        ffmpeg = core.resolve_ffmpeg(qconfig.get(CFG.ffmpegPath) or "")
        if not ffmpeg:
            self._set_status("未找到 ffmpeg，无法截取试看片段（可到「设置」页安装依赖）。")
            self._window.notify("缺少 ffmpeg", "请先到「设置」页安装依赖。", "error")
            return
        duration = max(0.1, self._seg_end - self._seg_start)
        self._extract_thread = ExtractThread(self._video_path, self._seg_start, duration,
                                             ffmpeg, self)
        self._extract_thread.done.connect(self._on_extract_done)
        self._extract_thread.failed.connect(self._on_extract_failed)
        self.externalBtn.setEnabled(False)
        self._set_status(f"正在截取第 {self._row + 1} 段（流复制模式，很快）…")
        self._extract_thread.start()

    def _on_extract_done(self, path: str) -> None:
        from . import core
        self._extract_thread = None
        self.externalBtn.setEnabled(True)
        self._set_status(f"试看片段已生成：{os.path.basename(path)}，正在用默认播放器打开…")
        core.open_path_in_explorer(path)

    def _on_extract_failed(self, message: str) -> None:
        self._extract_thread = None
        self.externalBtn.setEnabled(True)
        self._set_status(f"截取失败：{message}")
        self._window.notify("截取失败", message, "error")

    def _stop_extract_thread(self) -> None:
        """退出前等截取线程收尾（aboutToQuit 调用），避免线程未停进程先走。"""
        thread = self._extract_thread
        if thread is not None:
            thread.wait(10000)
