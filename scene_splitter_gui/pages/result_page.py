# -*- coding: utf-8 -*-
"""「结果与日志」页面：查看每段切片、导出报告、查看运行日志。"""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    LargeTitleLabel,
    PlainTextEdit,
    PushButton,
    SettingCardGroup,
    TableWidget,
)

from .. import core
from ..widgets import make_scroll_page, InfoCard

LOG_COLORS = {"info": "", "warn": "[警告] ", "error": "[错误] ", "ok": "[完成] "}

HEADERS_SINGLE = ["#", "起始时间", "结束时间", "时长(秒)", "帧数", "起始帧", "输出文件"]
HEADERS_MULTI = ["#", "起始时间", "结束时间", "时长(秒)", "帧数", "起始帧", "输出文件", "来源视频"]
COLUMN_WIDTHS = [50, 135, 135, 90, 70, 70]
# 表格里用紧凑时码，避免被截断
OUTPUT_COLUMN = 6


class ResultPage(QWidget):
    """展示检测/分割结果与运行日志。"""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("resultPage")
        self.results: list[core.TaskResult] = []
        self.current: core.TaskResult | None = None
        self.out_dir = ""
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

        self.vBox.addWidget(LargeTitleLabel("结果与日志", self.view))
        self.vBox.addWidget(CaptionLabel("查看每段的起止时间与输出文件，双击可在默认播放器中打开。", self.view))
        self.vBox.addSpacing(4)

        group = SettingCardGroup("本次任务", self.view)
        self.summaryCard = InfoCard("尚无结果，先到「视频分割」页开始一次任务吧。", self.view)
        group.addSettingCard(self.summaryCard)

        buttons = QWidget(self.view)
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.openDirBtn = PushButton(FluentIcon.FOLDER, "打开输出目录")
        self.openFileBtn = PushButton(FluentIcon.VIDEO, "播放选中片段")
        self.exportCsvBtn = PushButton(FluentIcon.DOCUMENT, "导出 CSV")
        self.exportHtmlBtn = PushButton(FluentIcon.GLOBE, "导出 HTML 报告")
        self.copyBtn = PushButton(FluentIcon.COPY, "复制列表")
        self.clearBtn = PushButton(FluentIcon.DELETE, "清空结果")
        for button in (self.openDirBtn, self.openFileBtn, self.exportCsvBtn,
                       self.exportHtmlBtn, self.copyBtn, self.clearBtn):
            row.addWidget(button)
        row.addStretch(1)
        group.addSettingCard(buttons)

        self.table = TableWidget(self.view)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(HEADERS_SINGLE)
        self.table.verticalHeader().hide()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setMinimumHeight(320)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate(COLUMN_WIDTHS):
            self.table.setColumnWidth(column, width)
        group.addSettingCard(self.table)
        self.vBox.addWidget(group)

        log_group = SettingCardGroup("运行日志", self.view)
        log_buttons = QWidget(self.view)
        log_row = QHBoxLayout(log_buttons)
        log_row.setContentsMargins(0, 0, 0, 0)
        log_row.setSpacing(8)
        self.saveLogBtn = PushButton(FluentIcon.SAVE, "保存日志")
        self.clearLogBtn = PushButton(FluentIcon.BROOM, "清空日志")
        self.verboseBtn = PushButton(FluentIcon.VIEW, "显示全部日志")
        log_row.addWidget(self.saveLogBtn)
        log_row.addWidget(self.clearLogBtn)
        log_row.addWidget(self.verboseBtn)
        log_row.addStretch(1)
        log_group.addSettingCard(log_buttons)

        self.logEdit = PlainTextEdit(self.view)
        self.logEdit.setMaximumBlockCount(2000)  # 日志上限：旧行自动丢弃，不再无界增长
        self.logEdit.setReadOnly(True)
        self.logEdit.setMinimumHeight(220)
        self.logEdit.setPlaceholderText("运行过程中的信息会显示在这里……")
        log_group.addSettingCard(self.logEdit)
        self.vBox.addWidget(log_group)
        self.vBox.addStretch(1)

        self.openDirBtn.clicked.connect(self._open_dir)
        self.openFileBtn.clicked.connect(self._play_selected)
        self.exportCsvBtn.clicked.connect(self._export_csv)
        self.exportHtmlBtn.clicked.connect(self._export_html)
        self.copyBtn.clicked.connect(self._copy_list)
        self.clearBtn.clicked.connect(self.clear)
        self.table.doubleClicked.connect(lambda _i: self._play_selected())
        self.saveLogBtn.clicked.connect(self._save_log)
        self.clearLogBtn.clicked.connect(self.logEdit.clear)
        self.verboseBtn.clicked.connect(self._toggle_verbose)

    # ------------------------------------------------------------------ 日志
    def append_log(self, message: str, level: str = "info") -> None:
        prefix = LOG_COLORS.get(level, "")
        self.logEdit.appendPlainText(prefix + str(message))
        if level == "error":
            bar = self.logEdit.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _toggle_verbose(self) -> None:
        from ..config import CFG, qconfig, save_config
        value = not qconfig.get(CFG.verboseLog)
        qconfig.set(CFG.verboseLog, value)
        save_config()
        self.verboseBtn.setText("显示全部日志" if value else "仅显示关键日志")
        self.window.notify("日志设置", "已切换为" + ("显示全部日志" if value else "仅显示关键日志"))

    # ------------------------------------------------------------------ 结果
    def set_results(self, results: list[core.TaskResult], out_dir: str = "") -> None:
        self.results = list(results)
        if results:
            self.current = results[-1]
            self.out_dir = out_dir or self.current.html_path or self.current.csv_path or ""
            if not self.out_dir and self.current.outputs:
                self.out_dir = os.path.dirname(self.current.outputs[0])
        self._refresh()

    def _refresh(self) -> None:
        if self.current is None:
            return
        multi = len(self.results) > 1
        lines = []
        for index, item in enumerate(self.results):
            if multi:
                lines.append(f"[{index + 1}] {os.path.basename(item.video_path)}："
                             f"{item.scene_count} 段，输出 {len(item.outputs)} 个文件，"
                             f"耗时 {item.elapsed:.1f} 秒")
            else:
                info = item.info
                if info:
                    lines.append(f"视频：{os.path.basename(item.video_path)}　"
                                 f"{info.size_text}　{info.fps:.3f} fps　"
                                 f"{core.format_duration(info.duration)}")
                lines.append(f"切片：{item.scene_count} 段，总时长 "
                             f"{core.format_duration(item.total_seconds)}，"
                             f"输出 {len(item.outputs)} 个文件，"
                             f"耗时 {item.elapsed:.1f} 秒")
        if multi:
            total = sum(r.scene_count for r in self.results)
            lines.append(f"合计：{len(self.results)} 个视频，{total} 段切片")
        target_dir = self._target_dir()
        if target_dir:
            lines.append(f"输出目录：{target_dir}")
        self.summaryCard.setText("\n".join(lines))

        # 汇总行数据：[(所属结果, 该片段序号, 输出文件或 None)]
        self._rows: list[tuple] = []
        for result in self.results:
            output_map = result.scene_outputs or result.outputs
            for row, scene in enumerate(result.scenes):
                output = output_map[row] if row < len(output_map) else None
                self._rows.append((result, scene, output))

        self.table.setColumnCount(8 if multi else 7)
        self.table.setHorizontalHeaderLabels(HEADERS_MULTI if multi else HEADERS_SINGLE)
        for column, width in enumerate(COLUMN_WIDTHS):
            self.table.setColumnWidth(column, width)
        if multi:
            self.table.setColumnWidth(7, 180)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self._rows))
        for row, (result, scene, output) in enumerate(self._rows):
            values = [str(scene.index),
                      core.format_timecode(scene.start_sec),
                      core.format_timecode(scene.end_sec),
                      f"{scene.duration:.3f}", str(scene.frame_count), str(scene.start_frame),
                      os.path.basename(output) if output else "（未输出）"]
            if multi:
                values.append(os.path.basename(result.video_path))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if output and column == OUTPUT_COLUMN:
                    item.setToolTip(output)
                self.table.setItem(row, column, item)
        self.table.setUpdatesEnabled(True)

    def _target_dir(self) -> str:
        for result in reversed(self.results):
            if result.outputs:
                return os.path.dirname(result.outputs[0])
            for path in (result.html_path, result.csv_path, result.image_dir):
                if path:
                    return os.path.dirname(path) if os.path.isfile(path) else path
        return ""

    # ------------------------------------------------------------------ 操作
    def _open_dir(self) -> None:
        directory = self._target_dir()
        if directory and os.path.isdir(directory):
            core.open_path_in_explorer(directory)
        else:
            self.window.notify("无法打开目录", "本次任务没有输出目录。", "warn")

    def _selected_row(self) -> int:
        rows = {index.row() for index in self.table.selectedIndexes()}
        return next(iter(rows)) if rows else -1

    def _play_selected(self) -> None:
        row = self._selected_row()
        rows = getattr(self, "_rows", [])
        if row < 0 or row >= len(rows):
            self.window.notify("未选择片段", "请先在表格中选择一行已输出的片段。", "warn")
            return
        output = rows[row][2]
        if not output or not os.path.exists(output):
            self.window.notify("文件不存在", "该片段没有输出文件（可能处于「仅检测」模式）。", "warn")
            return
        core.open_path_in_explorer(output)

    def _export_csv(self) -> None:
        if self.current is None or not self.current.scenes:
            self.window.notify("没有可导出的数据", "请先执行一次任务。", "warn")
            return
        default = self.current.csv_path or os.path.join(
            self._target_dir() or os.getcwd(), "场景列表.csv")
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", default, "CSV 文件 (*.csv)")
        if not path:
            return
        output_map = self.current.scene_outputs or self.current.outputs
        core.export_csv(path, self.current.scenes, output_map, self.current.video_path)
        self.window.notify("导出成功", f"CSV 已保存：{path}")

    def _export_html(self) -> None:
        if self.current is None or not self.current.scenes:
            self.window.notify("没有可导出的数据", "请先执行一次任务。", "warn")
            return
        default = self.current.html_path or os.path.join(
            self._target_dir() or os.getcwd(), "场景报告.html")
        path, _ = QFileDialog.getSaveFileName(self, "导出 HTML 报告", default, "网页文件 (*.html)")
        if not path:
            return
        output_map = self.current.scene_outputs or self.current.outputs
        core.export_html(path, self.current.scenes, self.current.video_path, output_map)
        self.window.notify("导出成功", f"报告已保存：{path}")

    def _copy_list(self) -> None:
        rows = getattr(self, "_rows", [])
        if not rows:
            self.window.notify("没有可复制的数据", "请先执行一次任务。", "warn")
            return
        multi = len(self.results) > 1
        lines = ["\t".join(HEADERS_MULTI if multi else HEADERS_SINGLE)]
        for result, scene, output in rows:
            values = [str(scene.index), core.format_timecode(scene.start_sec),
                      core.format_timecode(scene.end_sec), f"{scene.duration:.3f}",
                      str(scene.frame_count), str(scene.start_frame), output or ""]
            if multi:
                values.append(result.video_path)
            lines.append("\t".join(values))
        QApplication.clipboard().setText("\n".join(lines))
        self.window.notify("已复制", f"已复制 {len(rows)} 行到剪贴板。")

    def _save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存日志", os.path.join(self._target_dir() or os.getcwd(), "运行日志.txt"),
            "文本文件 (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.logEdit.toPlainText())
        self.window.notify("已保存", f"日志已保存：{path}")

    def clear(self) -> None:
        self.results.clear()
        self.current = None
        self._rows = []
        self.table.setRowCount(0)
        self.summaryCard.setText("尚无结果，先到「视频分割」页开始一次任务吧。")
