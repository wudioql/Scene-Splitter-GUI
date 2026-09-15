# -*- coding: utf-8 -*-
"""「批量处理」页面：一次处理多个视频或整个文件夹。"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    LargeTitleLabel,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SettingCardGroup,
    StrongBodyLabel,
    TableWidget,
)

from .. import core
from ..widgets import make_scroll_page, InfoCard, LineEditCard, SwitchCard

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".webm", ".ts", ".m4v",
              ".mpg", ".mpeg", ".rmvb", ".3gp", ".ogv", ".asf"}


class BatchPage(QWidget):
    """批量任务页面。参数沿用「视频分割」页的设置。"""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("batchPage")
        self.files: list[str] = []
        self._busy = False
        self._build_ui()

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        scroll, self.view = make_scroll_page(self)
        self.vBox = QVBoxLayout(self.view)
        self.vBox.setContentsMargins(30, 20, 30, 36)
        self.vBox.setSpacing(14)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.vBox.addWidget(LargeTitleLabel("批量处理", self.view))
        self.vBox.addWidget(CaptionLabel(
            "一次性处理多个视频或整个文件夹。检测与分段参数使用「视频分割」页中的设置，"
            "每个视频会独立检测与输出。", self.view))
        self.vBox.addSpacing(4)

        group = SettingCardGroup("待处理文件", self.view)
        tip = InfoCard("把视频文件或文件夹拖到窗口里也可以快速添加。"
                       "选择文件夹时会按「包含子文件夹」设置递归查找视频文件。", self.view)
        self.recursiveCard = SwitchCard(FluentIcon.FOLDER, "包含子文件夹",
                                        "添加文件夹时递归查找其中的视频文件", True)
        self.outputCard = LineEditCard(
            FluentIcon.FOLDER, "输出目录",
            "留空则每个视频输出到它自己所在的目录；勾选下方选项可为每个视频创建子文件夹",
            browse="dir", width=400)
        self.perFileDirCard = SwitchCard(FluentIcon.FOLDER_ADD, "每个视频单独建子文件夹",
                                         "例如 输出目录/视频A/视频A_001.mp4", False)
        for card in (tip, self.recursiveCard, self.outputCard, self.perFileDirCard):
            group.addSettingCard(card)
        self.vBox.addWidget(group)

        list_group = SettingCardGroup("文件列表", self.view)
        buttons = QWidget(self.view)
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.addFilesBtn = PushButton(FluentIcon.ADD, "添加文件…")
        self.addFolderBtn = PushButton(FluentIcon.FOLDER_ADD, "添加文件夹…")
        self.removeBtn = PushButton(FluentIcon.REMOVE, "移除选中")
        self.clearBtn = PushButton(FluentIcon.DELETE, "清空列表")
        for button in (self.addFilesBtn, self.addFolderBtn, self.removeBtn, self.clearBtn):
            row.addWidget(button)
        row.addStretch(1)
        self.countLabel = CaptionLabel("共 0 个文件", self.view)
        row.addWidget(self.countLabel)
        list_group.addSettingCard(buttons)

        self.table = TableWidget(self.view)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["序号", "文件", "时长", "状态", "输出目录"])
        self.table.verticalHeader().hide()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        self.table.setMinimumHeight(300)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 380)
        self.table.setColumnWidth(2, 110)
        self.table.setColumnWidth(3, 140)
        list_group.addSettingCard(self.table)
        self.vBox.addWidget(list_group)

        self.statusLabel = StrongBodyLabel("准备就绪", self.view)
        self.phaseLabel = CaptionLabel("", self.view)
        self.progressBar = ProgressBar(self.view)
        self.startBtn = PrimaryPushButton(FluentIcon.PLAY, "开始批量处理", self.view)
        self.stopBtn = PushButton(FluentIcon.CLOSE, "停止", self.view)
        self.stopBtn.setEnabled(False)
        self.startBtn.setFixedHeight(36)
        self.startBtn.setMinimumWidth(170)
        self.stopBtn.setFixedHeight(36)
        self.stopBtn.setMinimumWidth(110)
        run_row = QHBoxLayout()
        run_row.setSpacing(10)
        run_row.addWidget(self.startBtn)
        run_row.addWidget(self.stopBtn)
        run_row.addStretch(1)
        self.vBox.addWidget(self.statusLabel)
        self.vBox.addWidget(self.progressBar)
        self.vBox.addLayout(run_row)
        self.vBox.addWidget(self.phaseLabel)
        self.vBox.addStretch(1)

        self.addFilesBtn.clicked.connect(self._add_files)
        self.addFolderBtn.clicked.connect(self._add_folder)
        self.removeBtn.clicked.connect(self._remove_selected)
        self.clearBtn.clicked.connect(self.clear)
        self.startBtn.clicked.connect(self._start)
        self.stopBtn.clicked.connect(self.window.cancel_jobs)

    # ------------------------------------------------------------------ 文件管理
    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件",
            self.window.last_dir("lastInputDir"),
            "视频文件 (*.mp4 *.mkv *.mov *.avi *.flv *.wmv *.webm *.ts *.m4v *.mpg *.mpeg *.rmvb);;"
            "所有文件 (*.*)")
        if files:
            self.add_paths(files)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹",
                                                  self.window.last_dir("lastInputDir"))
        if folder:
            self.add_paths([folder])

    def add_paths(self, paths) -> None:
        found: list[str] = []
        for path in paths:
            path = str(path)
            if os.path.isdir(path):
                if self.recursiveCard.isChecked():
                    for root, _dirs, names in os.walk(path):
                        for name in names:
                            if Path(name).suffix.lower() in VIDEO_EXTS:
                                found.append(os.path.join(root, name))
                else:
                    for name in sorted(os.listdir(path)):
                        full = os.path.join(path, name)
                        if os.path.isfile(full) and Path(name).suffix.lower() in VIDEO_EXTS:
                            found.append(full)
            elif os.path.isfile(path):
                found.append(path)
        added = 0
        known = set(self.files)  # 去重用集合：加几千个文件也不再 O(n²)
        for path in found:
            if path not in known:
                known.add(path)
                self.files.append(path)
                added += 1
        self._refresh_table()
        if added:
            self.window.notify("已添加文件", f"新增 {added} 个视频，共 {len(self.files)} 个。")

    def clear(self) -> None:
        self.files.clear()
        self._refresh_table()

    def _remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.files):
                self.files.pop(row)
        self._refresh_table()

    def _refresh_table(self) -> None:
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self.files))
        for row, path in enumerate(self.files):
            self.table.setItem(row, 0, self._item(str(row + 1)))
            self.table.setItem(row, 1, self._item(os.path.basename(path), tooltip=path))
            self.table.setItem(row, 2, self._item("-"))
            self.table.setItem(row, 3, self._item("待处理"))
            self.table.setItem(row, 4, self._item(self._out_dir_for(path)))
        self.table.setUpdatesEnabled(True)
        self.countLabel.setText(f"共 {len(self.files)} 个文件")

    @staticmethod
    def _item(text: str, tooltip: str = ""):
        from PySide6.QtWidgets import QTableWidgetItem
        item = QTableWidgetItem(text)
        if tooltip:
            item.setToolTip(tooltip)
        return item

    def _out_dir_for(self, path: str) -> str:
        base = self.outputCard.text().strip()
        if not base:
            base = os.path.dirname(os.path.abspath(path))
        elif self.perFileDirCard.isChecked():
            base = os.path.join(base, Path(path).stem)
        return base

    def set_status(self, text: str, percent: int | None = None) -> None:
        self.statusLabel.setText(text)
        if percent is not None:
            self.progressBar.setValue(int(percent))

    def set_phase(self, text: str) -> None:
        self.phaseLabel.setText(text)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.startBtn.setEnabled(not busy)
        self.stopBtn.setEnabled(busy)
        for button in (self.addFilesBtn, self.addFolderBtn, self.removeBtn, self.clearBtn):
            button.setEnabled(not busy)

    def mark_row(self, index: int, text: str) -> None:
        if 0 <= index < self.table.rowCount():
            self.table.setItem(index, 3, self._item(text))

    # ------------------------------------------------------------------ 启动
    def _start(self) -> None:
        if self._busy:
            return
        if not self.files:
            self.window.notify("没有待处理文件", "请先添加视频文件或文件夹。", "warn")
            return
        base_params = self.window.split_page.collect_params(do_split=True)
        jobs = []
        for index, path in enumerate(self.files):
            params = core.TaskParams(**{**base_params.__dict__})
            params.video_path = path
            params.output_dir = self._out_dir_for(path)
            params.open_output_dir = False
            jobs.append(params)
        errors = core.validate_params(jobs[0]) if jobs else []
        if errors:
            self.window.notify("参数有误", errors[0], "error")
            return
        self.window.start_jobs(jobs, source_page=self)
