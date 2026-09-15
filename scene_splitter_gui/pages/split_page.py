# -*- coding: utf-8 -*-
"""「视频分割」主页面：选择视频、设置检测与分段参数、启动任务。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    LargeTitleLabel,
    MessageBox,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SettingCardGroup,
    StrongBodyLabel,
    TableWidget,
)

from .. import core
from ..segment_player import SegmentPlayer
from ..widgets import (
    make_scroll_page,
    ChoiceCard,
    DoubleSpinCard,
    InfoCard,
    LineEditCard,
    SpinCard,
    SwitchCard,
)

VIDEO_FILTER = ("视频文件 (*.mp4 *.mkv *.mov *.avi *.flv *.wmv *.webm *.ts *.m4v *.mpg *.mpeg "
                "*.rmvb *.3gp *.ogv *.asf);;所有文件 (*.*)")

PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
CONTAINER_ITEMS = ["MP4 (.mp4)", "MKV (.mkv)（可保留多音轨/字幕）", "MOV (.mov)"]
CONTAINER_KEYS = ["mp4", "mkv", "mov"]
AUDIO_ITEMS = ["复制原音轨（推荐）", "转为 AAC 音频", "去除音轨"]
AUDIO_KEYS = ["copy", "aac", "none"]
CODEC_ITEMS = ["不重编码（极快，按关键帧切分）", "重编码 H.264（精确到帧，较慢）"]
DETECTOR_ORDER = ["content", "adaptive", "threshold", "hash", "histogram"]


class _FramesThread(QThread):
    """后台抓取某段首/中/尾三帧（cv2 开文件+seek 较重，不能占 GUI 线程）。

    页面同时只跑一个抓帧线程：忙时用户又切段，只记住最新的行号，等线程
    结束再起（中间跳过的行直接丢弃，避免排队越追越长）。
    """

    done = Signal(int, int, object)  # row, 预览代际, 帧列表（BGR numpy 数组）

    def __init__(self, video_path: str, row: int, gen: int,
                 start_frame: int, end_frame: int, parent=None):
        super().__init__(parent)
        self._video_path = video_path
        self._row = row
        self._gen = gen
        self._start_frame = start_frame
        self._end_frame = end_frame

    def run(self) -> None:  # noqa: D401
        try:
            frames = core.grab_frames(self._video_path, self._start_frame,
                                      self._end_frame, 3)
        except Exception:  # noqa: BLE001 - 后台线程绝不能抛到 Qt 里
            frames = []
        self.done.emit(self._row, self._gen, frames or [])


class SplitPage(QWidget):
    """主工作页面。"""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("splitPage")
        self.video_path = ""
        self.video_info: core.VideoInfo | None = None
        self._param_cards: dict[str, dict] = {}
        self._param_groups: dict[str, QWidget] = {}
        self._busy = False
        self._state_tip = None
        # 预览（分段规划）相关状态
        self._preview_result: core.TaskResult | None = None   # 最近一次预览结果
        self._preview_signature = ""                          # 生成预览时的参数指纹
        self._preview_manual = False                          # 预览后是否被手动增删过
        self._preview_ok = False
        self._frames_cache: dict[int, list] = {}
        self._preview_gen = 0        # 预览代际：新预览/手动改段/清空时 +1，
                                     # 旧代际线程的结果直接丢弃（行号已错位不能再用）
        self._grab_thread: QThread | None = None  # 抓帧线程（同时最多一个）
        self._grab_pending: int | None = None     # 线程忙时用户又切段：只记住最新行
        _app = QApplication.instance()
        if _app is not None:
            # 退出前等抓帧线程收尾，避免线程未停进程先走导致崩溃
            _app.aboutToQuit.connect(self._stop_grab_thread)
        self._build_ui()
        self._restore_params()
        self._connect_signals()
        self._on_detector_changed(self.detectorCard.currentIndex())

    # ------------------------------------------------------------------ 界面搭建
    def _build_ui(self) -> None:
        scroll, self.view = make_scroll_page(self)
        self.vBox = QVBoxLayout(self.view)
        self.vBox.setContentsMargins(30, 20, 30, 36)
        self.vBox.setSpacing(14)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        title = LargeTitleLabel("视频分割", self.view)
        subtitle = CaptionLabel("用 PySceneDetect 检测镜头切换，再用 ffmpeg 按场景切成独立片段", self.view)
        self.vBox.addWidget(title)
        self.vBox.addWidget(subtitle)
        self.vBox.addSpacing(4)

        self._build_input_group()
        self._build_detector_group()
        self._build_range_group()
        self._build_limits_group()
        self._build_output_group()
        self._build_report_group()
        self._build_preview_group()
        self._build_run_group()
        self.vBox.addStretch(1)

    def _install_shortcuts(self) -> None:
        """常用快捷键。"""
        from PySide6.QtGui import QShortcut as _Shortcut

        pairs = (
            ("Ctrl+O", self._choose_video, "打开视频"),
            ("Ctrl+Return", self._on_preview_clicked, "预览分段"),
            ("Ctrl+Shift+Return", self._on_split_clicked, "按预览开始分割"),
        )
        self._shortcuts = []
        for keys, slot, _desc in pairs:
            shortcut = _Shortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)
        # Delete / M 走应用级 eventFilter：只在焦点落在本页非输入框内时生效，
        # 输入框里的正常输入必须放行（QShortcut 会吃掉按键，做不到这一点）
        _app = QApplication.instance()
        if _app is not None:
            _app.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        """Delete 删除选中段 / M 与上一段合并（焦点在输入框内时放行）。"""
        try:
            if event.type() == QEvent.KeyPress and event.modifiers() == Qt.NoModifier:
                key = event.key()
                if key in (Qt.Key_Delete, Qt.Key_M) and self._seg_ops_allowed():
                    # 用事件接收者（watched）而不用 focusWidget：不依赖窗口激活状态
                    # （QTest 直投事件时 watched 就是目标控件，离屏测试同样可判）
                    recv = watched if isinstance(watched, QWidget) \
                        else QApplication.focusWidget()
                    if (recv is not None and (recv is self or self.isAncestorOf(recv))
                            and not self._focus_in_editable(recv)):
                        if key == Qt.Key_Delete:
                            self._delete_selected_segment()
                        else:
                            self._merge_selected_segment()
                        return True
        except Exception:
            pass
        return super().eventFilter(watched, event)

    @staticmethod
    def _focus_in_editable(focus) -> bool:
        """焦点是否在可输入控件内（含 SpinBox 等复合控件的内部输入框）。"""
        widget = focus
        while widget is not None:
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
                return True
            widget = widget.parentWidget()
        return False

    def _choose_video(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件",
            os.path.dirname(self.video_path) if self.video_path else str(Path.home()),
            VIDEO_FILTER)
        if path:
            self.set_video_path(path)

    # —— 输入 ——
    def _build_input_group(self) -> None:
        group = SettingCardGroup("输入视频", self.view)
        self.videoCard = LineEditCard(
            FluentIcon.VIDEO, "视频文件",
            "支持 mp4 / mkv / mov / avi / flv / webm 等常见格式，可把文件直接拖到窗口里",
            browse="file", file_filter=VIDEO_FILTER, width=420)
        self.videoCard.addExtraWidget(self._button("打开所在文件夹", self._open_input_dir))
        group.addSettingCard(self.videoCard)

        self.infoCard = InfoCard("尚未选择视频文件。", self.view)
        group.addSettingCard(self.infoCard)
        self.vBox.addWidget(group)

    def _button(self, text: str, slot, tooltip: str = "") -> PushButton:
        button = PushButton(text)
        button.clicked.connect(slot)
        if tooltip:
            button.setToolTip(tooltip)
        return button

    # —— 检测器 ——
    def _build_detector_group(self) -> None:
        group = SettingCardGroup("场景检测", self.view)
        # 右上角快捷入口：恢复默认参数（放最显眼的检测区，避免藏在页面底部被忽略）
        header = QWidget(self.view)
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        self.resetParamsBtn = PushButton(FluentIcon.ERASE_TOOL, "恢复默认参数", self.view)
        self.resetParamsBtn.setToolTip("把本页的检测 / 分段 / 输出参数恢复为默认值"
                                       "（视频文件与输出目录不受影响）")
        self.resetParamsBtn.setFixedHeight(32)
        header_row.addStretch(1)
        header_row.addWidget(self.resetParamsBtn)
        group.addSettingCard(header)
        self.detectorCard = ChoiceCard(
            FluentIcon.SEARCH, "检测算法",
            "不同算法适合不同素材，一般内容类视频用「内容检测」即可",
            items=[core.DETECTOR_LABELS[k] for k in DETECTOR_ORDER], index=0, width=380)
        group.addSettingCard(self.detectorCard)
        self.vBox.addWidget(group)

        # 每种检测器一套参数卡片，切换时显示对应的那一套
        for key in DETECTOR_ORDER:
            specs = core.DETECTOR_PARAMS[key]
            param_group = SettingCardGroup(f"{core.DETECTOR_LABELS[key].split('（')[0]} 参数", self.view)
            self._param_cards[key] = {}
            for spec in specs:
                card = self._make_param_card(spec)
                self._param_cards[key][spec.key] = card
                param_group.addSettingCard(card)
            self._param_groups[key] = param_group
            self.vBox.addWidget(param_group)
            param_group.setVisible(key == DETECTOR_ORDER[0])
        self._sync_param_card_state()

    def _make_param_card(self, spec: core.ParamSpec) -> QWidget:
        title = spec.label
        content = spec.tip
        if spec.kind == "float":
            return DoubleSpinCard(FluentIcon.UNIT, title, content, float(spec.default),
                                  spec.minimum, spec.maximum, spec.step, spec.decimals)
        if spec.kind == "int":
            return SpinCard(FluentIcon.UNIT, title, content, int(spec.default),
                            int(spec.minimum), int(spec.maximum), int(spec.step))
        if spec.kind == "bool":
            return SwitchCard(FluentIcon.ACCEPT, title, content, bool(spec.default))
        if spec.kind == "choice":
            labels = [label for _value, label in spec.choices]
            index = next((i for i, (value, _l) in enumerate(spec.choices)
                          if value == spec.default), 0)
            return ChoiceCard(FluentIcon.LABEL, title, content, labels, index, width=200)
        raise ValueError(f"未知参数类型：{spec.kind}")

    def _sync_param_card_state(self) -> None:
        """kernel_size 为 0 时禁用边缘权重（避免用户误以为无效）。"""
        for key in ("content", "adaptive"):
            cards = self._param_cards.get(key, {})
            kernel = cards.get("kernel_size")
            edges = cards.get("weight_edges")
            if kernel is not None and edges is not None:
                def update(_v=None, k=kernel, e=edges):
                    e.setEnabled(k.value() > 0)
                kernel.valueChanged.connect(update)
                update()

    # —— 处理范围 ——
    def _build_range_group(self) -> None:
        group = SettingCardGroup("处理范围（可选）", self.view)
        self.startCard = DoubleSpinCard(
            FluentIcon.HISTORY, "起始时间", "从视频的第几秒开始处理，0 表示从头开始",
            0.0, 0.0, 100000.0, 1.0, 2, suffix="秒")
        self.endCard = DoubleSpinCard(
            FluentIcon.STOP_WATCH, "结束时间", "处理到第几秒为止，0 表示一直处理到视频结尾",
            0.0, 0.0, 100000.0, 1.0, 2, suffix="秒")
        self.downscaleCard = SpinCard(
            FluentIcon.ZOOM, "分析下采样倍率", "0 = 自动。数值越大检测越快、精度越低，通常 2~4 即可",
            0, 0, 16, 1)
        self.skipCard = SpinCard(
            FluentIcon.SKIP_FORWARD, "跳帧间隔", "0 = 逐帧分析。数值越大越快，但可能漏掉切换点",
            0, 0, 60, 1)
        self.cropCard = LineEditCard(
            FluentIcon.CLIPPING_TOOL, "裁剪区域", "仅分析画面的一部分（可屏蔽台标/字幕），格式 x0,y0,x1,y1",
            placeholder="例如 0,0,1919,1079（留空表示整幅画面）", width=300)
        for card in (self.startCard, self.endCard, self.downscaleCard, self.skipCard, self.cropCard):
            group.addSettingCard(card)
        self.vBox.addWidget(group)

    # —— 分段限制 ——
    def _build_limits_group(self) -> None:
        group = SettingCardGroup("分段长度限制", self.view)
        tip = InfoCard(
            "这里的两组限制会同时生效：先按「秒」换算成帧，再与「帧数」取更严格的一方。"
            "0 表示不限制。片段过短会自动与相邻片段合并，过长会自动再平均切开，"
            "切片首尾相接不会丢帧。", self.view)
        self.minSecCard = DoubleSpinCard(
            FluentIcon.MOVE, "最短时长", "0 = 不限制。短于该时长的片段会被合并到相邻片段",
            0.0, 0.0, 100000.0, 0.5, 2, suffix="秒")
        self.maxSecCard = DoubleSpinCard(
            FluentIcon.MOVE, "最长时长", "0 = 不限制。长于该时长的片段会被平均切成多段",
            0.0, 0.0, 100000.0, 0.5, 2, suffix="秒")
        self.minFrameCard = SpinCard(
            FluentIcon.MOVE, "最少帧数", "0 = 不限制。与「最短时长」取更严格的一方",
            0, 0, 10000000, 1, suffix="帧")
        self.maxFrameCard = SpinCard(
            FluentIcon.MOVE, "最多帧数", "0 = 不限制。与「最长时长」取更严格的一方",
            0, 0, 10000000, 1, suffix="帧")
        self.preferCard = DoubleSpinCard(
            FluentIcon.SPEED_MEDIUM, "优先切分时长",
            "0 = 平均切分。超过最长限制时，优先按该时长均匀切开（例如 60 秒）",
            0.0, 0.0, 100000.0, 1.0, 2, suffix="秒")
        self.smartCard = SwitchCard(
            FluentIcon.ACCEPT, "智能择优切分",
            "超过最长限制必须切开时，优先切在画面变化最大的位置；"
            "合并过短片段时，优先保留变化明显的切点。关闭则按固定间隔切分",
            True)
        group.addSettingCard(self.smartCard)
        for card in (self.minSecCard, self.maxSecCard, self.minFrameCard,
                     self.maxFrameCard, self.preferCard):
            group.addSettingCard(card)
        group.addSettingCard(tip)
        self.vBox.addWidget(group)

    # —— 输出 ——
    def _build_output_group(self) -> None:
        group = SettingCardGroup("输出设置", self.view)
        self.outputCard = LineEditCard(
            FluentIcon.FOLDER, "输出目录", "切片文件的保存位置，留空则保存到源视频所在目录",
            browse="dir", width=400)
        self.outputCard.addExtraWidget(self._button("使用源视频目录", self._use_source_dir))
        group.addSettingCard(self.outputCard)

        self.templateCard = LineEditCard(
            FluentIcon.SAVE_AS, "文件名模板",
            "可用变量：" + "、".join(core.TEMPLATE_VARS), text=core.TaskParams.template, width=400)
        group.addSettingCard(self.templateCard)

        self.previewLabel = CaptionLabel("", self.view)
        self.previewHolder = QWidget(self.view)
        preview_layout = QVBoxLayout(self.previewHolder)
        preview_layout.setContentsMargins(66, 0, 0, 8)
        preview_layout.addWidget(self.previewLabel)
        group.addSettingCard(self.previewHolder)

        self.numberCard = SpinCard(FluentIcon.TAG, "起始序号", "第一段的编号，例如 1 或 101",
                                   1, 0, 1000000, 1)
        self.padCard = SpinCard(FluentIcon.TAG, "序号位数", "不足位数时左侧补 0，例如 3 → 001",
                                3, 0, 10, 1)
        self.containerCard = ChoiceCard(FluentIcon.ZIP_FOLDER, "封装格式", "",
                                        CONTAINER_ITEMS, 0, width=280)
        self.codecCard = ChoiceCard(FluentIcon.MOVIE, "编码方式",
                                    "不重编码最快，但切片起点会对齐到最近的关键帧；"
                                    "重编码可精确到帧，适合后续精细剪辑",
                                    CODEC_ITEMS, 1, width=300)
        self.crfCard = SpinCard(FluentIcon.UNIT, "画质 CRF", "数值越小画质越好、文件越大，常用 18~28",
                                22, 0, 51, 1)
        self.presetCard = ChoiceCard(FluentIcon.SPEED_HIGH, "编码速度预设",
                                     "越靠后压缩率越高、速度越慢", PRESETS, 2, width=200)
        self.audioCard = ChoiceCard(FluentIcon.MUSIC, "音频处理", "不重编码模式下选「复制原音轨」最稳妥；拼回原片需视频重编码＋去音轨/转 AAC",
                                    AUDIO_ITEMS, 0, width=240)
        self.extraCard = LineEditCard(
            FluentIcon.DEVELOPER_TOOLS, "附加 ffmpeg 参数",
            "高级用法：会原样追加到 ffmpeg 命令中，例如 -vf scale=1280:-2", width=320)
        self.overwriteCard = SwitchCard(FluentIcon.SAVE, "覆盖同名文件",
                                        "关闭时同名文件会被跳过并记录在日志里", False)
        for card in (self.numberCard, self.padCard, self.containerCard, self.codecCard,
                     self.crfCard, self.presetCard, self.audioCard, self.extraCard,
                     self.overwriteCard):
            group.addSettingCard(card)
        self.vBox.addWidget(group)

    # —— 报告 ——
    def _build_report_group(self) -> None:
        group = SettingCardGroup("报告与预览", self.view)
        self.csvCard = SwitchCard(FluentIcon.DOCUMENT, "导出 CSV 场景列表",
                                  "包含每段的起止时间、时长、帧数与输出文件名", True)
        self.htmlCard = SwitchCard(FluentIcon.GLOBE, "导出 HTML 报告",
                                   "带缩略图的网页报告，方便快速浏览", False)
        self.imageCard = SwitchCard(FluentIcon.PHOTO, "保存缩略图",
                                    "为每段视频抽取若干张画面存到「缩略图」文件夹", False)
        self.imageCountCard = SpinCard(FluentIcon.PHOTO, "每段缩略图数量", "", 3, 1, 20, 1)
        for card in (self.csvCard, self.htmlCard, self.imageCard, self.imageCountCard):
            group.addSettingCard(card)
        self.vBox.addWidget(group)


    # —— 分段预览 ——
    def _build_preview_group(self) -> None:
        group = SettingCardGroup("分段预览（建议先预览，确认后再分割）", self.view)

        self.previewCard = InfoCard(
            "还没有预览结果。点下方「① 预览分段」，程序会先只做检测与分段规划，"
            "列出最终会切成哪些段——确认满意后再点「② 开始分割」，保存下来的就与预览完全一致；"
            "不满意可以直接改参数重新预览。", self.view)
        group.addSettingCard(self.previewCard)

        buttons = QWidget(self.view)
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.previewBtn = PrimaryPushButton(FluentIcon.SEARCH, "① 预览分段")
        self.splitBtn = PrimaryPushButton(FluentIcon.CUT, "② 开始分割")
        self.deleteSegBtn = PushButton(FluentIcon.DELETE, "删除选中段")
        self.mergeSegBtn = PushButton(FluentIcon.MOVE, "与上一段合并")
        self.deleteSegBtn.setToolTip("删除表格中选中的段（快捷键 Delete）")
        self.mergeSegBtn.setToolTip("选中段与上一段合并（快捷键 M；第 1 段不可合并）")
        self.thumbBtn = PushButton(FluentIcon.PHOTO, "刷新画面预览")
        for button in (self.previewBtn, self.splitBtn):
            button.setFixedHeight(36)
            button.setMinimumWidth(150)
        for button in (self.deleteSegBtn, self.mergeSegBtn, self.thumbBtn):
            button.setFixedHeight(36)
        row.addWidget(self.previewBtn)
        row.addWidget(self.splitBtn)
        row.addWidget(self.deleteSegBtn)
        row.addWidget(self.mergeSegBtn)
        row.addWidget(self.thumbBtn)
        row.addStretch(1)
        group.addSettingCard(buttons)

        self.previewTable = TableWidget(self.view)
        self.previewTable.setColumnCount(5)
        self.previewTable.setHorizontalHeaderLabels(
            ["#", "起始时间", "结束时间", "时长(秒)", "输出文件名"])
        self.previewTable.verticalHeader().hide()
        self.previewTable.setBorderVisible(True)
        self.previewTable.setBorderRadius(8)
        self.previewTable.setMinimumHeight(260)
        self.previewTable.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((50, 135, 135, 90)):
            self.previewTable.setColumnWidth(column, width)
        group.addSettingCard(self.previewTable)

        thumb_holder = QWidget(self.view)
        self.thumbHolder = thumb_holder
        thumb_layout = QVBoxLayout(thumb_holder)
        thumb_layout.setContentsMargins(0, 6, 0, 4)
        thumb_layout.setSpacing(6)
        self.thumbRow = QWidget(thumb_holder)
        self.thumbLayout = QHBoxLayout(self.thumbRow)
        self.thumbLayout.setContentsMargins(0, 0, 0, 0)
        self.thumbLayout.setSpacing(6)
        self.thumbLayout.addStretch(1)
        try:
            from ..config import CFG, qconfig
            _collapsed = bool(qconfig.get(CFG.collapseThumbs))
        except Exception:
            _collapsed = False
        self._thumbs_collapsed = _collapsed
        self._thumbTipRow = QWidget(thumb_holder)
        _tip_layout = QHBoxLayout(self._thumbTipRow)
        _tip_layout.setContentsMargins(0, 0, 0, 0)
        _tip_layout.setSpacing(8)
        self.thumbTip = CaptionLabel(
            "选中表格中的某一段，这里会显示该段的首/中/尾画面，用于确认切分位置是否合适。",
            self._thumbTipRow)
        _tip_layout.addWidget(self.thumbTip, 1)
        self.thumbToggle = PushButton("展开画面" if _collapsed else "收起画面",
                                      self._thumbTipRow)
        self.thumbToggle.setToolTip("收起/展开画面预览（收起后表格和播放器离得更近）")
        self.thumbToggle.clicked.connect(self._toggle_thumbs)
        _tip_layout.addWidget(self.thumbToggle)
        self.thumbRow.setVisible(not _collapsed)
        thumb_layout.addWidget(self.thumbRow)
        thumb_layout.addWidget(self._thumbTipRow)
        group.addSettingCard(thumb_holder)

        self.segPlayer = SegmentPlayer(self.window, self.view)
        self.segPlayer.set_step_callback(self._step_preview_row)
        self.segPlayer.set_ops_callback(self._player_seg_op)
        group.addSettingCard(self.segPlayer)
        self.vBox.addWidget(group)

        self.previewBtn.clicked.connect(self._on_preview_clicked)
        self.splitBtn.clicked.connect(self._on_split_clicked)
        self.deleteSegBtn.clicked.connect(self._delete_selected_segment)
        self.mergeSegBtn.clicked.connect(self._merge_selected_segment)
        self.thumbBtn.clicked.connect(lambda: self._show_segment_frames(force=True))
        self.previewTable.itemSelectionChanged.connect(lambda: self._show_segment_frames())
        self.previewTable.itemSelectionChanged.connect(self._load_player_segment)
        self._set_preview_buttons(False)
        # 构建时尺寸未稳定（样式/字体 polish 在首次显示时才落地），显示后再定一次高度，
        # 否则缩略图提示行的按钮会被压扁（字糊掉，点一次才好）
        QTimer.singleShot(0, self._show_segment_frames)

    def _set_preview_buttons(self, has_preview: bool) -> None:
        self.splitBtn.setEnabled(has_preview and not self._busy)
        self.thumbBtn.setEnabled(has_preview and not self._busy)
        self._sync_seg_op_buttons()

    def _seg_ops_allowed(self) -> bool:
        """删除/合并操作是否可用：有预览、非忙碌、预览不过期（手动结果视为当前）。"""
        if self._preview_result is None or self._busy:
            return False
        if self._preview_manual:
            return True
        try:
            return self.params_signature() == self._preview_signature
        except Exception:
            return False

    def _sync_seg_op_buttons(self) -> None:
        """同步四处删除/合并键（表格上方两个 + 播放器里两个）的可用状态。"""
        allowed = self._seg_ops_allowed()
        self.deleteSegBtn.setEnabled(allowed)
        self.mergeSegBtn.setEnabled(allowed)
        player = getattr(self, "segPlayer", None)
        if player is not None:
            loaded = allowed and player.segment_index() >= 0
            player.deleteBtn.setEnabled(loaded)
            player.mergeBtn.setEnabled(loaded)

    def _player_seg_op(self, action: str) -> None:
        """播放器「删除本段/与上一段合并」：复用表格选中行的逻辑（两者已联动）。"""
        if action == "delete":
            self._delete_selected_segment()
        elif action == "merge":
            self._merge_selected_segment()

    # —— 参数指纹：用于判断预览是否已过期 ——
    SEGMENT_KEYS = (
        "detector", "detector_params", "start_time", "end_time", "downscale", "frame_skip",
        "crop", "min_scene_sec", "max_scene_sec", "min_scene_frames", "max_scene_frames",
        "prefer_scene_sec", "smart_cut",
    )

    def params_signature(self) -> str:
        """只取会影响分段结果的参数生成指纹（编码/命名等输出参数不影响）。"""
        params = self.collect_params(do_split=True)
        data = {key: getattr(params, key, None) for key in self.SEGMENT_KEYS}
        return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)

    def _check_preview_freshness(self) -> None:
        """参数被改动后提示重新预览（切割选项不影响分段，不参与比较）。"""
        if self._preview_result is None:
            return
        if self._preview_manual:
            return
        stale = self.params_signature() != self._preview_signature
        if stale != getattr(self, "_preview_stale", False):
            self._preview_stale = stale
            self._refresh_preview_text()
        self.splitBtn.setEnabled(not stale and not self._busy)
        self._sync_seg_op_buttons()  # 过期禁用、改回参数后自动恢复（含播放器里的两个）

    def _refresh_preview_text(self) -> None:
        if self._preview_result is None:
            return
        result = self._preview_result
        stats = core.summarize(result.scenes, None if self._preview_manual else
                               self.collect_params(do_split=True),
                               result.info.fps if result.info else 0.0)
        lines = [
            f"分段规划：共 {stats['count']} 段，总时长 {core.format_duration(stats['total'])}；"
            f"每段 {stats['min']:.2f} ~ {stats['max']:.2f} 秒"
            f"（平均 {stats['avg']:.2f} 秒，{stats['min_frames']} ~ {stats['max_frames']} 帧）",
        ]
        if result.smart_used:
            lines.append("已启用智能择优切分：切点优先落在画面变化最大的位置。")
        if self._preview_manual:
            lines.append("已手动调整过分段（删除/合并），下面的结果会原样输出。")
        elif getattr(self, "_preview_stale", False):
            lines.append("⚠ 参数已修改，与当前预览不一致，请重新点「① 预览分段」。")
        else:
            lines.append("✓ 预览与当前参数一致，可点「② 开始分割」。")
        for warning in stats["warnings"]:
            lines.append("提示：" + warning)
        self.previewCard.setText("\n".join(lines))

    # —— 表格填充 ——
    def _fill_preview_table(self) -> None:
        result = self._preview_result
        if result is None:
            self.previewTable.setRowCount(0)
            return
        scenes = result.scenes
        params = self.collect_params(do_split=True)
        self.previewTable.setUpdatesEnabled(False)
        self.previewTable.setRowCount(len(scenes))
        for row, scene in enumerate(scenes):
            number = params.start_number + row
            try:
                # 预览时用 .临时 后缀，避免与真实输出文件混淆
                name = core.render_filename(params.template, params.video_path, scene,
                                            number, params.pad) + f".{params.container}"
            except Exception:
                name = "-"
            values = [str(scene.index), core.format_timecode(scene.start_sec),
                      core.format_timecode(scene.end_sec), f"{scene.duration:.3f}", name]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 4:
                    item.setToolTip(name)
                self.previewTable.setItem(row, column, item)
        self.previewTable.setUpdatesEnabled(True)

    # —— 触发预览 ——
    def _on_preview_clicked(self) -> None:
        if self._busy:
            self.window.notify("任务正在进行", "请先等待当前任务结束或点击「停止」。", "warn")
            return
        params = self.collect_params(do_split=True)
        errors = core.validate_params(params)
        if errors:
            self.window.notify("参数有误", errors[0], "error")
            return
        self._frames_cache.clear()
        self._preview_gen += 1
        self.window.start_jobs([params], source_page=self, mode="plan")

    # —— 按预览结果分割 ——
    def _on_split_clicked(self) -> None:
        if self._preview_result is None:
            self.window.notify("请先预览", "请先点「① 预览分段」确认分段结果。", "warn")
            return
        if getattr(self, "_preview_stale", False) and not self._preview_manual:
            self.window.notify("参数已修改", "参数改动后需要重新预览，再执行分割。", "warn")
            return
        params = self.collect_params(do_split=True)
        errors = core.validate_params(params)
        if errors:
            self.window.notify("参数有误", errors[0], "error")
            return
        result = self._preview_result
        result.scenes = self._renumber()          # 保证序号连续
        self.window.start_jobs([params], source_page=self, mode="split", plans=[result])

    def _renumber(self) -> list:
        result = self._preview_result
        for index, scene in enumerate(result.scenes, start=1):
            scene.index = index
        return result.scenes

    # —— 手动调整分段 ——
    def _selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.previewTable.selectedIndexes()})

    def _delete_selected_segment(self) -> None:
        result = self._preview_result
        if result is None:
            return
        rows = self._selected_rows()
        if not rows:
            self.window.notify("未选择片段", "请先在预览表格里选中要删除的段。", "warn")
            return
        if len(rows) >= len(result.scenes):
            self.window.notify("不能全部删除", "至少要保留一段视频。", "warn")
            return
        for row in reversed(rows):
            result.scenes.pop(row)
        self._mark_manual(select_row=min(rows[0], len(result.scenes) - 1))

    def _merge_selected_segment(self) -> None:
        result = self._preview_result
        if result is None:
            return
        rows = self._selected_rows()
        if not rows or rows[0] == 0:
            self.window.notify("无法合并", "请选择第 2 段及之后的片段（会与上一段合并）。", "warn")
            return
        row = rows[0]
        previous, current = result.scenes[row - 1], result.scenes[row]
        if previous.end_frame != current.start_frame:
            self.window.notify("无法合并", "两段之间不相邻，不能合并。", "warn")
            return
        previous.end_frame = current.end_frame
        result.scenes.pop(row)
        self._mark_manual(select_row=rows[0] - 1)

    def _mark_manual(self, select_row: int | None = None) -> None:
        self._preview_manual = True
        self._preview_stale = False
        # 行号已错位，旧的画面缓存必须丢弃，否则会显示错段的图
        self._frames_cache.clear()
        self._preview_gen += 1  # 在途线程的结果随之作废
        if getattr(self, "segPlayer", None) is not None:
            self.segPlayer.clear()
        self._renumber()
        self._fill_preview_table()
        if select_row is not None and self.previewTable.rowCount() > 0:
            # 删/合后行号错位：选中合体段（合并）或滑入段（删除）并载入播放器，
            # 否则表格停在错段上、播放器空转，所有本段操作全灰
            row = min(max(select_row, 0), self.previewTable.rowCount() - 1)
            prev = self._selected_rows()
            self.previewTable.selectRow(row)
            if self._selected_rows() == prev:
                # 行号没变时 selectRow 不触发信号，手动补载缩略图与播放器
                self._show_segment_frames()
                self._load_player_segment()
        self._refresh_preview_text()
        self._set_preview_buttons(True)
        self.window.notify("已更新预览", f"当前共 {len(self._preview_result.scenes)} 段，"
                                        "将按此结果输出。")

    # —— 片段画面预览 ——
    def _fit_thumb_height(self, image_height: int = 0) -> None:
        """缩略图区域按内容定高（ExpandLayout 依据当前高度排布，必须主动调整）。"""
        bottom_h = max(self.thumbTip.sizeHint().height() + 4,
                       self.thumbToggle.sizeHint().height())
        shown = 0 if self._thumbs_collapsed else max(0, image_height)
        gap = 0 if self._thumbs_collapsed else self.thumbHolder.layout().spacing()
        self.thumbHolder.setFixedHeight(shown + bottom_h + 10 + gap)

    def _toggle_thumbs(self) -> None:
        """收起/展开画面预览（状态记入配置，下次启动保持）。"""
        self._thumbs_collapsed = not self._thumbs_collapsed
        self.thumbRow.setVisible(not self._thumbs_collapsed)
        self.thumbToggle.setText("展开画面" if self._thumbs_collapsed else "收起画面")
        try:
            from ..config import CFG, qconfig, save_config
            qconfig.set(CFG.collapseThumbs, self._thumbs_collapsed)
            save_config()
        except Exception:
            pass
        if self._thumbs_collapsed:
            self._fit_thumb_height(0)
        else:
            self._show_segment_frames()  # 展开时按当前选中补载（命中即绘）

    def _clear_frames(self) -> None:
        while self.thumbLayout.count() > 1:
            item = self.thumbLayout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _show_segment_frames(self, force: bool = False) -> None:
        """选中段的首/中/尾画面：命中缓存立刻显示，未命中后台抓取（不卡界面）。

        未命中时旧图保留、只改提示文字，避免「先清空再卡住」的闪烁感。
        """
        result = self._preview_result
        if result is None:
            self._grab_pending = None
            self._clear_frames()
            self._fit_thumb_height(0)
            self.thumbTip.setText("选中表格中的某一段，这里会显示该段的首/中/尾画面。")
            return
        rows = self._selected_rows()
        if not rows:
            self._grab_pending = None
            self._clear_frames()
            self._fit_thumb_height(0)
            self.thumbTip.setText("选中表格中的某一段，这里会显示该段的首/中/尾画面。")
            return
        row = rows[0]
        if row >= len(result.scenes):
            return
        scene = result.scenes[row]
        if self._thumbs_collapsed and not force:
            # 收起状态：只更新提示行，不抓图不绘制（省 CPU；展开时再补载）
            self.thumbTip.setText(f"第 {scene.index} 段画面预览：{scene.start_tc} → {scene.end_tc}"
                                  f"（{scene.frame_count} 帧）")
            return
        frames = self._frames_cache.get(row)
        if frames is not None and not force:
            self._render_frames(scene, frames)
            self._maybe_preload(row)
            return
        # 未命中：提示「正在读取」，后台线程抓完再显示（旧图先留着）
        self.thumbTip.setText(f"第 {scene.index} 段画面预览：{scene.start_tc} → {scene.end_tc}"
                              f"（{scene.frame_count} 帧）　正在读取…")
        self._request_frames(row)

    def _render_frames(self, scene, frames) -> None:
        """把三帧画到缩略图区（调用方保证 scene/frames 对应当前选中行）。"""
        self._clear_frames()
        self._fit_thumb_height(0)
        self.thumbTip.setText(f"第 {scene.index} 段画面预览：{scene.start_tc} → {scene.end_tc}"
                              f"（{scene.frame_count} 帧）")
        if not frames:
            self.thumbTip.setText(self.thumbTip.text() + "　（无法读取画面，可能文件已被移动）")
            return
        image_height = 0
        for frame in frames:
            label = QLabel(self.thumbRow)
            label.setPixmap(self._bgr_to_pixmap(frame))
            label.setFixedSize(label.pixmap().size())
            label.setStyleSheet("border-radius:4px;")
            self.thumbLayout.insertWidget(self.thumbLayout.count() - 1, label)
            image_height = max(image_height, label.pixmap().height())
        self._fit_thumb_height(image_height)

    # —— 后台抓帧：单线程 + 只追最新 + 空闲预取邻段 ——
    def _request_frames(self, row: int) -> None:
        if self._grab_thread is not None and self._grab_thread.isRunning():
            self._grab_pending = row  # 忙：只记住最新的，中间跳过的直接丢弃
            return
        self._start_grab(row)

    def _start_grab(self, row: int) -> None:
        result = self._preview_result
        if result is None or row < 0 or row >= len(result.scenes):
            return
        scene = result.scenes[row]
        thread = _FramesThread(self.video_path, row, self._preview_gen,
                               scene.start_frame, scene.end_frame, self)
        thread.done.connect(self._on_frames_done)
        thread.finished.connect(thread.deleteLater)
        self._grab_thread = thread
        thread.start()

    def _on_frames_done(self, row: int, gen: int, frames) -> None:
        if self._grab_thread is None or self.sender() is self._grab_thread:
            self._grab_thread = None  # 极少见：槽排队期间已起了新线程，此时不动跟踪位
        if gen == self._preview_gen and self._preview_result is not None:
            self._cache_put(row, frames)
            rows = self._selected_rows()
            if rows and rows[0] == row and row < len(self._preview_result.scenes):
                self._render_frames(self._preview_result.scenes[row], frames)
        pending = self._grab_pending
        self._grab_pending = None
        if pending is not None:
            self._show_segment_frames()  # 线程忙时用户又切了段：按当前选中重走一遍
            return
        rows = self._selected_rows()
        current = rows[0] if rows else -1
        if current != row and (current < 0 or current not in self._frames_cache):
            self._show_segment_frames()  # 选中已不在本行且未命中：起线程（无选中则清空）
            return
        self._maybe_preload(current if current >= 0 else row)

    def _maybe_preload(self, row: int) -> None:
        """彻底空闲时预取相邻段（各一），让「上一段/下一段」点下去就是缓存命中。"""
        if self._grab_thread is not None and self._grab_thread.isRunning():
            return
        if self._grab_pending is not None:
            return
        result = self._preview_result
        if result is None:
            return
        for neighbor in (row + 1, row - 1):
            if 0 <= neighbor < len(result.scenes) and neighbor not in self._frames_cache:
                self._start_grab(neighbor)
                return

    def _cache_put(self, row: int, frames) -> None:
        """有界缓存：最多留 60 段（约 30MB），旧的先淘汰，避免长预览吃内存。"""
        self._frames_cache.pop(row, None)
        self._frames_cache[row] = frames or []
        while len(self._frames_cache) > 60:
            self._frames_cache.pop(next(iter(self._frames_cache)))

    def _stop_grab_thread(self) -> None:
        """退出前等抓帧线程收尾（aboutToQuit 调用），避免线程未停进程先走。"""
        thread = self._grab_thread
        if thread is not None:
            thread.wait(5000)

    # —— 片段视频试看 ——
    def _load_player_segment(self) -> None:
        player = getattr(self, "segPlayer", None)
        if player is None:
            return
        result = self._preview_result
        if result is None:
            player.clear()
            return
        rows = self._selected_rows()
        if not rows or rows[0] >= len(result.scenes):
            return  # 仅取消选中时不打断正在看的片段
        scene = result.scenes[rows[0]]
        player.load_segment(self.video_path, rows[0], scene.start_sec, scene.end_sec)
        # 上下段按钮按边界细调（首段禁上、尾段禁下）
        player.prevBtn.setEnabled(rows[0] > 0)
        player.nextBtn.setEnabled(rows[0] < len(result.scenes) - 1)
        self._sync_seg_op_buttons()  # load_segment 会全开控制键，这里按预览/忙碌重 gate

    def _step_preview_row(self, delta: int) -> None:
        """播放器「上一段/下一段」：表格选中行 ±1，缩略图与播放器随选择信号自动跟随；
        切之前在播的话新段从头接着播，否则保持暂停（与点选表格一致）。"""
        result = self._preview_result
        player = getattr(self, "segPlayer", None)
        if result is None or player is None or not result.scenes:
            return
        rows = self._selected_rows()
        base = rows[0] if rows else player.segment_index()
        new_row = min(max(base + delta, 0), len(result.scenes) - 1)
        if rows and new_row == rows[0]:
            return  # 已在边界（按钮本应禁用，这里再兜一层）
        was_playing = player.is_playing()
        self.previewTable.selectRow(new_row)  # 触发选择信号 → 载入新段并暂停在段首
        if was_playing:
            player.play_whole_segment()

    @staticmethod
    def _bgr_to_pixmap(frame):
        height, width, channels = frame.shape
        image = QImage(frame.data, width, height, channels * width, QImage.Format_BGR888)
        return QPixmap.fromImage(image.copy())

    # —— 由主窗口回调 ——
    def on_plan_ready(self, result: core.TaskResult) -> None:
        """预览（仅检测）完成。"""
        self._preview_result = result
        self._preview_manual = False
        self._preview_stale = False
        self._preview_signature = self.params_signature()
        self._frames_cache.clear()
        self._preview_gen += 1
        self._fill_preview_table()
        self._refresh_preview_text()
        self._set_preview_buttons(True)
        if self.previewTable.rowCount():
            self.previewTable.selectRow(0)
        # selectRow 在已选中第 0 行时不会触发选择信号，这里显式载入播放器
        self._load_player_segment()
        self.window.notify("预览完成",
                           f"规划出 {result.scene_count} 段，确认后可点「② 开始分割」。", "ok")

    def on_split_finished(self) -> None:
        """分割完成：预览仍然保留，方便继续调整或换参数再试。"""
        self._refresh_preview_text()

    def invalidate_preview(self) -> None:
        self._preview_result = None
        self._preview_signature = ""
        self._preview_manual = False
        self._preview_stale = False
        self._frames_cache.clear()
        self._preview_gen += 1
        self.previewTable.setRowCount(0)
        self._set_preview_buttons(False)
        if getattr(self, "segPlayer", None) is not None:
            self.segPlayer.clear()
        self._show_segment_frames()

    # —— 运行区 ——
    def _build_run_group(self) -> None:
        card = QWidget(self.view)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        self.statusLabel = StrongBodyLabel("准备就绪", self.view)
        self.hintLabel = CaptionLabel("", self.view)
        self.progressBar = ProgressBar(self.view)
        self.progressBar.setValue(0)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.stopBtn = PushButton(FluentIcon.CLOSE, "停止", self.view)
        self.reportBtn = PushButton(FluentIcon.DOCUMENT, "仅检测并导出报告", self.view)
        self.openOutBtn = PushButton(FluentIcon.FOLDER, "打开输出目录", self.view)
        for button in (self.stopBtn, self.reportBtn, self.openOutBtn):
            button.setFixedHeight(36)
            button.setMinimumWidth(120)
        self.stopBtn.setEnabled(False)
        row.addWidget(self.stopBtn)
        row.addStretch(1)
        row.addWidget(self.reportBtn)
        row.addWidget(self.openOutBtn)

        layout.addWidget(self.statusLabel)
        layout.addWidget(self.progressBar)
        layout.addLayout(row)
        layout.addWidget(self.hintLabel)
        self.vBox.addWidget(card)
        self._update_template_preview()

    # ------------------------------------------------------------------ 信号
    def _connect_signals(self) -> None:
        self.videoCard.textChanged.connect(self._on_video_path_changed)
        self.detectorCard.currentIndexChanged.connect(self._on_detector_changed)
        self.templateCard.textChanged.connect(self._update_template_preview)
        self.padCard.valueChanged.connect(self._update_template_preview)
        self.codecCard.currentIndexChanged.connect(self._sync_codec_state)
        self.imageCard.checkedChanged.connect(
            lambda checked: self.imageCountCard.setEnabled(checked))
        self.imageCountCard.setEnabled(self.imageCard.isChecked())
        self.reportBtn.clicked.connect(lambda: self._start(do_split=False))
        self.stopBtn.clicked.connect(self._on_stop)
        self.openOutBtn.clicked.connect(self._open_output_dir)
        self.resetParamsBtn.clicked.connect(self._on_reset_params)
        self._sync_codec_state()
        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.setInterval(350)
        self._probe_timer.timeout.connect(self._probe_video)
        # 定时比对参数指纹：任何影响分段的参数被改动后，自动提示需要重新预览
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(500)
        self._watch_timer.timeout.connect(self._check_preview_freshness)
        self._watch_timer.start()
        self._install_shortcuts()

    # ------------------------------------------------------------------ 逻辑
    def _on_detector_changed(self, index: int) -> None:
        key = DETECTOR_ORDER[max(0, min(index, len(DETECTOR_ORDER) - 1))]
        for name, group in self._param_groups.items():
            group.setVisible(name == key)

    def _sync_codec_state(self) -> None:
        reencode = self.codecCard.currentIndex() == 1
        self.crfCard.setEnabled(reencode)
        self.presetCard.setEnabled(reencode)
        self.audioCard.setEnabled(True)
        if reencode and self.audioCard.currentIndex() == 0:
            self.hintLabel.setText("提示：重编码模式下「复制原音轨」速度最快；"
                                   "若音轨格式不被目标容器支持，请改为「转为 AAC 音频」。")
        else:
            self.hintLabel.setText("")

    def _update_template_preview(self) -> None:
        template = self.templateCard.text() or "$VIDEO_NAME_$SCENE_NUMBER"
        try:
            name = core.render_filename(template, "我的视频.mp4",
                                        core.SceneSpan(1, 125, 375, 25.0),
                                        1, self.padCard.value())
        except Exception as exc:  # noqa: BLE001
            name = f"模板错误：{exc}"
        self.previewLabel.setText(f"命名预览：{name}.mp4　（起始 00:00:05.000 / 结束 00:00:15.000）")

    def _on_video_path_changed(self, _text: str) -> None:
        self._probe_timer.start()
        if self._preview_result is not None:
            self.invalidate_preview()

    def set_video_path(self, path: str) -> None:
        self.videoCard.setText(os.path.abspath(path))
        self._probe_video()

    def _probe_video(self) -> None:
        path = self.videoCard.text().strip().strip('"')
        self.video_path = path
        if not path or not os.path.isfile(path):
            self.video_info = None
            self.infoCard.setText("尚未选择视频文件。" if not path else
                                  f"文件不存在：{path}")
            return
        if not core.SCENEDETECT_AVAILABLE:
            self.infoCard.setText("未检测到 PySceneDetect，请先在「设置」页安装依赖。")
            return
        try:
            info = core.probe_video(path)
            self.video_info = info
            size = os.path.getsize(path)
            self.infoCard.setText(
                f"文件：{Path(path).name}　大小 {core.format_bytes(size)}\n"
                f"分辨率：{info.size_text}　帧率：{info.fps:.3f} fps　"
                f"时长：{core.format_duration(info.duration)}　总帧数：{info.frame_count}")
            if self.outputCard.text().strip() == "":
                pass
        except Exception as exc:  # noqa: BLE001
            self.video_info = None
            self.infoCard.setText(f"读取视频信息失败：{exc}")

    def _use_source_dir(self) -> None:
        path = self.videoCard.text().strip()
        if path:
            self.outputCard.setText(os.path.dirname(os.path.abspath(path)))

    def _open_input_dir(self) -> None:
        path = self.videoCard.text().strip()
        if path and os.path.exists(path):
            core.open_path_in_explorer(path if os.path.isdir(path)
                                       else os.path.dirname(os.path.abspath(path)))

    def out_dir(self) -> str:
        text = self.outputCard.text().strip() or ""
        if not text and self.video_path:
            text = os.path.dirname(os.path.abspath(self.video_path))
        return text

    def _open_output_dir(self) -> None:
        directory = self.out_dir()
        if directory and os.path.isdir(directory):
            core.open_path_in_explorer(directory)
        else:
            self.window.notify("输出目录不存在", "请先设置有效的输出目录。", "warn")

    def _start(self, do_split: bool) -> None:
        """仅检测（并导出报告），不切割视频。"""
        if self._busy:
            self.window.notify("任务正在进行", "请先等待当前任务结束或点击「停止」。", "warn")
            return
        params = self.collect_params(do_split=do_split)
        errors = core.validate_params(params)
        if errors:
            self.window.notify("参数有误", errors[0], "error")
            return
        self._frames_cache.clear()
        self._preview_gen += 1
        # do_split=False 时必须走 full 模式：plan_task 检测 + execute_plan 写报告（不切视频）。
        # plan 模式只做内存规划，不会产生任何报告文件，所以不能用它实现「导出报告」。
        self.window.start_jobs([params], source_page=self, mode="full")

    # 由主窗口调用：任务开始/结束
    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.stopBtn.setEnabled(busy)
        for button in (getattr(self, "previewBtn", None), getattr(self, "reportBtn", None),
                       getattr(self, "openOutBtn", None), getattr(self, "resetParamsBtn", None)):
            if button is not None:
                button.setEnabled(not busy)
        for card in (self.videoCard, self.detectorCard, self.outputCard, self.templateCard):
            card.setEnabled(not busy)
        if busy:
            self._set_preview_buttons(False)
        else:
            has_preview = self._preview_result is not None
            self._set_preview_buttons(has_preview)
            self._check_preview_freshness()

    def set_status(self, text: str, percent: int | None = None) -> None:
        self.statusLabel.setText(text)
        if percent is not None:
            self.progressBar.setValue(int(percent))

    def _on_stop(self) -> None:
        self.window.cancel_jobs()

    def _on_reset_params(self) -> None:
        """恢复默认参数（带二次确认）。"""
        if self._busy:
            self.window.notify("任务正在进行", "请先等待当前任务结束或点击「停止」。", "warn")
            return
        box = MessageBox("恢复默认参数",
                         "将「视频分割」页的检测 / 分段 / 输出参数恢复为默认值。\n"
                         "视频文件与输出目录不受影响。是否继续？", self.window)
        box.yesButton.setText("恢复默认")
        box.cancelButton.setText("取消")
        if box.exec():
            self.reset_params()
            self._check_preview_freshness()  # 立即刷新预览过期状态，不等定时器
            self.window.notify("已重置", "参数已恢复默认值。")

    # ------------------------------------------------------------------ 参数收集
    def collect_params(self, do_split: bool = True) -> core.TaskParams:
        key = DETECTOR_ORDER[self.detectorCard.currentIndex()]
        detector_params = {}
        for name, card in self._param_cards[key].items():
            if isinstance(card, SwitchCard):
                detector_params[name] = bool(card.isChecked())
            elif isinstance(card, ChoiceCard):
                spec = next(s for s in core.DETECTOR_PARAMS[key] if s.key == name)
                detector_params[name] = spec.choices[card.currentIndex()][0]
            else:
                detector_params[name] = card.value()
        video_path = (self.video_path or self.videoCard.text().strip().strip('"')).strip()
        out_dir = self.out_dir()
        # 统一转成绝对路径：避免用户手输相对路径时，后续操作因工作目录变化而出错
        if video_path:
            video_path = os.path.abspath(video_path)
        if out_dir:
            out_dir = os.path.abspath(out_dir)
        return core.TaskParams(
            video_path=video_path,
            output_dir=out_dir,
            detector=key,
            detector_params=detector_params,
            start_time=self.startCard.value(),
            end_time=self.endCard.value(),
            downscale=self.downscaleCard.value(),
            frame_skip=self.skipCard.value(),
            crop=self.cropCard.text().strip(),
            min_scene_sec=self.minSecCard.value(),
            max_scene_sec=self.maxSecCard.value(),
            min_scene_frames=self.minFrameCard.value(),
            max_scene_frames=self.maxFrameCard.value(),
            prefer_scene_sec=self.preferCard.value(),
            smart_cut=self.smartCard.isChecked(),
            template=self.templateCard.text().strip() or "$VIDEO_NAME_$SCENE_NUMBER",
            start_number=self.numberCard.value(),
            pad=self.padCard.value(),
            container=CONTAINER_KEYS[self.containerCard.currentIndex()],
            reencode=self.codecCard.currentIndex() == 1,
            crf=self.crfCard.value(),
            preset=PRESETS[self.presetCard.currentIndex()],
            audio=AUDIO_KEYS[self.audioCard.currentIndex()],
            extra_args=self.extraCard.text().strip(),
            overwrite=self.overwriteCard.isChecked(),
            save_csv=self.csvCard.isChecked(),
            save_html=self.htmlCard.isChecked(),
            save_images=self.imageCard.isChecked(),
            image_count=self.imageCountCard.value(),
            do_split=do_split,
        )

    # ------------------------------------------------------------------ 参数记忆
    def _restore_params(self) -> None:
        from ..config import load_last_params
        saved = load_last_params()
        if not saved:
            return

        def num(card, key, default):
            try:
                if isinstance(card, LineEditCard):
                    card.setText(str(saved.get(key, default)))
                else:
                    card.setValue(saved.get(key, default))
            except Exception:
                pass

        detector = saved.get("detector", "content")
        if detector in DETECTOR_ORDER:
            self.detectorCard.setCurrentIndex(DETECTOR_ORDER.index(detector))
        dp = saved.get("detector_params", {})
        if isinstance(dp, dict):
            for name, value in dp.items():
                card = self._param_cards.get(detector, {}).get(name)
                if card is None:
                    continue
                try:
                    if isinstance(card, SwitchCard):
                        card.setChecked(bool(value))
                    elif isinstance(card, ChoiceCard):
                        spec = next(s for s in core.DETECTOR_PARAMS[detector] if s.key == name)
                        values = [v for v, _ in spec.choices]
                        if value in values:
                            card.setCurrentIndex(values.index(value))
                    else:
                        card.setValue(value)
                except Exception:
                    pass

        num(self.startCard, "start_time", 0.0)
        num(self.endCard, "end_time", 0.0)
        num(self.downscaleCard, "downscale", 0)
        num(self.skipCard, "frame_skip", 0)
        num(self.cropCard, "crop", "")
        num(self.minSecCard, "min_scene_sec", 0.0)
        num(self.maxSecCard, "max_scene_sec", 0.0)
        num(self.minFrameCard, "min_scene_frames", 0)
        num(self.maxFrameCard, "max_scene_frames", 0)
        num(self.preferCard, "prefer_scene_sec", 0.0)
        num(self.templateCard, "template", "$VIDEO_NAME_$SCENE_NUMBER")
        num(self.numberCard, "start_number", 1)
        num(self.padCard, "pad", 3)
        num(self.crfCard, "crf", 22)
        num(self.extraCard, "extra_args", "")
        if saved.get("container") in CONTAINER_KEYS:
            self.containerCard.setCurrentIndex(CONTAINER_KEYS.index(saved["container"]))
        if saved.get("preset") in PRESETS:
            self.presetCard.setCurrentIndex(PRESETS.index(saved["preset"]))
        if saved.get("audio") in AUDIO_KEYS:
            self.audioCard.setCurrentIndex(AUDIO_KEYS.index(saved["audio"]))
        if isinstance(saved.get("reencode"), bool):
            self.codecCard.setCurrentIndex(1 if saved["reencode"] else 0)
        for card, key, default in ((self.csvCard, "save_csv", True),
                                   (self.htmlCard, "save_html", False),
                                   (self.imageCard, "save_images", False),
                                   (self.smartCard, "smart_cut", True),
                                   (self.overwriteCard, "overwrite", False)):
            if key in saved:
                try:
                    card.setChecked(bool(saved[key]))
                except Exception:
                    pass
        num(self.imageCountCard, "image_count", 3)
        self.outputCard.setText(saved.get("output_dir", "") or "")

    def reset_params(self) -> None:
        """把所有参数恢复为默认值。"""
        defaults = core.TaskParams()
        for index, key in enumerate(DETECTOR_ORDER):
            for spec in core.DETECTOR_PARAMS[key]:
                card = self._param_cards[key].get(spec.key)
                if card is None:
                    continue
                try:
                    if isinstance(card, SwitchCard):
                        card.setChecked(bool(spec.default))
                    elif isinstance(card, ChoiceCard):
                        values = [v for v, _ in spec.choices]
                        card.setCurrentIndex(values.index(spec.default))
                    else:
                        card.setValue(spec.default)
                except Exception:
                    pass
            if key == defaults.detector:
                self.detectorCard.setCurrentIndex(index)
        for card, value in ((self.startCard, 0.0), (self.endCard, 0.0),
                            (self.downscaleCard, 0), (self.skipCard, 0),
                            (self.minSecCard, 0.0), (self.maxSecCard, 0.0),
                            (self.minFrameCard, 0), (self.maxFrameCard, 0),
                            (self.preferCard, 0.0), (self.numberCard, 1),
                            (self.padCard, 3), (self.crfCard, 22), (self.imageCountCard, 3)):
            card.setValue(value)
        self.cropCard.setText("")
        self.templateCard.setText(defaults.template)
        self.extraCard.setText("")
        # 视频文件与输出目录不受影响：重置的是调参结果，不是用户的工作现场
        self.containerCard.setCurrentIndex(0)
        self.codecCard.setCurrentIndex(1)
        self.presetCard.setCurrentIndex(2)
        self.audioCard.setCurrentIndex(0)
        self.overwriteCard.setChecked(False)
        self.csvCard.setChecked(True)
        self.htmlCard.setChecked(False)
        self.imageCard.setChecked(False)
        self.smartCard.setChecked(True)

    def export_state(self) -> dict:
        params = self.collect_params(do_split=True)
        data = dict(params.__dict__)
        # 不保存具体视频路径，避免下次启动时指向已删除的文件
        data.pop("video_path", None)
        return data

    def on_job_finished(self) -> None:
        self.set_busy(False)
        self.set_status("已完成。", 100)
