# -*- coding: utf-8 -*-
"""主窗口：侧边导航 + 任务调度。"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from qfluentwidgets import (
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    MSFluentWindow,
    NavigationItemPosition,
    qconfig,
)

from . import core
from .config import APP_NAME, APP_VERSION, CFG, save_config, store_params
from .pages.batch_page import VIDEO_EXTS, BatchPage
from .pages.result_page import ResultPage
from .pages.settings_page import SettingsPage
from .pages.split_page import SplitPage
from .worker import Worker


class MainWindow(MSFluentWindow):
    """应用主窗口。"""

    def __init__(self):
        super().__init__()
        self.worker: Worker | None = None
        self.current_source: QWidget | None = None
        self.current_mode = "full"
        self.current_export_reports = False
        self._result_out_dir = ""

        self.split_page = SplitPage(self)
        self.batch_page = BatchPage(self)
        self.result_page = ResultPage(self)
        self.settings_page = SettingsPage(self)

        self.addSubInterface(self.split_page, FluentIcon.MOVIE, "视频分割")
        self.addSubInterface(self.batch_page, FluentIcon.LIBRARY, "批量处理")
        self.addSubInterface(self.result_page, FluentIcon.DOCUMENT, "结果与日志")
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, "设置",
                             position=NavigationItemPosition.BOTTOM)

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1200, 840)
        self.setMinimumSize(980, 680)
        self.setAcceptDrops(True)
        try:
            self.setMicaEffectEnabled(True)
        except Exception:
            pass
        try:
            self.navigationInterface.setExpandWidth(220)
        except Exception:
            pass

        self.append_log(f"{APP_NAME} v{APP_VERSION} 已启动。", "ok")
        self._check_environment()
        self.splash_screen = None

    # ------------------------------------------------------------------ 环境检查
    def _check_environment(self) -> None:
        if core.SCENEDETECT_AVAILABLE and core.resolve_ffmpeg(qconfig.get(CFG.ffmpegPath)):
            self.append_log("依赖检查通过：PySceneDetect 与 ffmpeg 均可用。", "ok")
            return
        missing = []
        if not core.SCENEDETECT_AVAILABLE:
            missing.append("PySceneDetect")
        if not core.resolve_ffmpeg(qconfig.get(CFG.ffmpegPath)):
            missing.append("ffmpeg")
        text = "、".join(missing)
        self.append_log(f"缺少组件：{text}", "error")
        box = MessageBox(
            "缺少运行组件",
            f"未检测到：{text}\n\n"
            "点击「现在安装」会自动执行：\n"
            f"  python -m pip install -U scenedetect imageio-ffmpeg\n\n"
            "也可以稍后到「设置」页手动安装。",
            self)
        box.yesButton.setText("现在安装")
        box.cancelButton.setText("稍后再说")
        if box.exec():
            self.switchTo(self.settings_page)
            self.settings_page._install_dependencies()

    # ------------------------------------------------------------------ 日志与提示
    def append_log(self, message: str, level: str = "info") -> None:
        if level in ("info", "ok") and not qconfig.get(CFG.verboseLog):
            return
        try:
            self.result_page.append_log(message, level)
        except Exception:
            pass

    def verbose_log(self) -> bool:
        try:
            return bool(qconfig.get(CFG.verboseLog))
        except Exception:
            return True

    def notify(self, title: str, content: str = "", level: str = "info", duration: int = 3000) -> None:
        mapping = {
            "info": InfoBar.info,
            "ok": InfoBar.success,
            "warn": InfoBar.warning,
            "error": InfoBar.error,
        }
        function = mapping.get(level, InfoBar.info)
        try:
            function(title=title, content=content, orient=Qt.Horizontal,
                     isClosable=True, position=InfoBarPosition.TOP_RIGHT,
                     duration=duration, parent=self)
        except Exception:
            pass

    def last_dir(self, key: str) -> str:
        try:
            value = qconfig.get(getattr(CFG, key)) or str(Path.home())
        except Exception:
            value = str(Path.home())
        return value if os.path.isdir(value) else str(Path.home())

    def _remember_dir(self, key: str, path: str) -> None:
        try:
            directory = path if os.path.isdir(path) else os.path.dirname(path)
            if directory and os.path.isdir(directory):
                qconfig.set(getattr(CFG, key), directory)
        except Exception:
            pass

    # ------------------------------------------------------------------ 任务调度
    def start_jobs(self, jobs, source_page: QWidget, mode: str = "full",
                   plans=None, export_reports: bool = False) -> None:
        """启动任务。

        mode: full = 检测+分割；plan = 仅预览分段；split = 按预览结果分割
        plans: mode="split" 时传入已确认的 TaskResult（含 scenes）
        export_reports: mode="plan" 时是否顺带导出 CSV/HTML 报告
        """
        if self.worker is not None and self.worker.isRunning():
            self.notify("任务正在进行", "请先等待或停止当前任务。", "warn")
            return
        self.current_source = source_page
        self.current_mode = mode
        self.current_export_reports = export_reports
        self._result_out_dir = jobs[0].output_dir if jobs else ""
        self._remember_dir("lastInputDir", jobs[0].video_path if jobs else "")
        if jobs and jobs[0].output_dir:
            self._remember_dir("lastOutputDir", jobs[0].output_dir)
        # 记住参数
        try:
            store_params(self.split_page.export_state())
        except Exception:
            pass

        labels = {"full": "开始任务", "plan": "开始预览分段", "split": "按预览结果开始分割"}
        start_label = labels.get(mode, "开始任务")
        if mode == "full" and jobs and not getattr(jobs[0], "do_split", True):
            start_label = "开始检测（仅导出报告，不分割视频）"
        self.append_log("=" * 60)
        self.append_log(f"{start_label}：{len(jobs)} 个视频"
                        + (f"，共 {len(plans[0].scenes)} 段（已确认）" if mode == "split" and plans else ""))
        self.worker = Worker(jobs, ffmpeg_path=qconfig.get(CFG.ffmpegPath) or "",
                             mode=mode, plans=plans, parent=self)
        self.worker.sigLog.connect(self.append_log)
        self.worker.sigStage.connect(self._on_stage)
        self.worker.sigProgress.connect(self._on_progress)
        self.worker.sigFinished.connect(self._on_finished)
        self.worker.sigCancelled.connect(self._on_cancelled)
        self.worker.sigFailed.connect(self._on_failed)
        self.split_page.set_busy(True)
        if mode != "plan":
            self.batch_page.set_busy(True)
        self.split_page.set_status("任务已启动…", 0)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def cancel_jobs(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.append_log("正在停止任务…", "warn")
            self.worker.cancel()
            self.split_page.set_status("正在停止…")

    def _on_stage(self, text: str) -> None:
        self.split_page.set_status(text)
        self.batch_page.set_phase(text)

    def _on_progress(self, percent: int) -> None:
        self.split_page.set_status(self.split_page.statusLabel.text(), percent)
        self.batch_page.set_status(self.batch_page.statusLabel.text(), percent)

    def _on_finished(self, results) -> None:
        mode = getattr(self, "current_mode", "full")
        self.split_page.set_busy(False)
        self.batch_page.set_busy(False)
        if mode == "plan":
            # 预览模式：把规划结果交回页面展示，不切视频、不跳转结果页
            self.split_page.set_status(f"预览完成：共 {results[0].scene_count} 段，"
                                       "确认后可点「② 开始分割」。" if results else "预览完成。", 100)
            if results:
                try:
                    self.split_page.on_plan_ready(results[0])
                    if self.current_export_reports:
                        self.result_page.set_results(list(results), "")
                except Exception as exc:  # noqa: BLE001
                    self.append_log(f"预览结果展示失败：{exc}", "error")
            if results and results[0].scene_count == 0:
                self.notify("没有切点", "未检测到场景切换，请调小检测阈值后重新预览。", "warn", 5000)
            self.worker = None
            return

        self.current_mode = "full"
        job = self.worker.jobs[0] if self.worker and self.worker.jobs else None
        report_only = job is not None and not getattr(job, "do_split", True)
        done_text = "检测完成。" if report_only else "已完成。"
        self.split_page.set_status(done_text, 100)
        self.batch_page.set_status(done_text, 100)
        self.split_page.on_split_finished()
        if self.current_source is self.batch_page:
            for row in range(min(self.batch_page.table.rowCount(), len(results))):
                result = results[row]
                self.batch_page.mark_row(row, f"完成 {result.scene_count} 段")
        out_dir = ""
        if results:
            first = results[0]
            if first.outputs:
                out_dir = os.path.dirname(first.outputs[0])
            else:  # 仅检测模式没有视频输出，用报告所在目录兜底
                for _path in (first.html_path, first.csv_path, first.image_dir):
                    if _path:
                        out_dir = _path if os.path.isdir(_path) else os.path.dirname(_path)
                        break
        self.result_page.set_results(list(results), out_dir)
        if report_only and results and self.current_source is self.split_page:
            # 仅检测模式：同步刷新分割页的预览（与旧版 plan 行为保持一致）
            try:
                self.split_page.on_plan_ready(results[0])
            except Exception as exc:  # noqa: BLE001
                self.append_log(f"预览结果展示失败：{exc}", "error")
        total_scenes = sum(r.scene_count for r in results)
        failed = sum(len(r.skipped) for r in results)
        if report_only:
            self.notify("检测完成",
                        f"共 {len(results)} 个视频，{total_scenes} 段（仅报告，未输出视频）",
                        "ok", 4000)
            self.append_log(f"检测完成：{len(results)} 个视频，{total_scenes} 段切片（仅报告）。", "ok")
        else:
            self.notify("任务完成",
                        f"共 {len(results)} 个视频，输出 {total_scenes} 段"
                        + (f"，{failed} 段被跳过/失败" if failed else ""),
                        "ok" if not failed else "warn", 4000)
            self.append_log(f"任务完成：{len(results)} 个视频，{total_scenes} 段切片。", "ok")

        if qconfig.get(CFG.autoSwitchResult):
            self.switchTo(self.result_page)
        if qconfig.get(CFG.autoOpenOutput) and job is not None and job.open_output_dir and out_dir:
            if os.path.isdir(out_dir):
                core.open_path_in_explorer(out_dir)
        self.worker = None

    def _on_cancelled(self) -> None:
        self.split_page.set_busy(False)
        self.batch_page.set_busy(False)
        self.current_mode = "full"
        self.split_page.set_status("已取消。")
        self.batch_page.set_status("已取消。")
        self.notify("任务已取消", "已停止检测/分割，已生成的文件会保留。", "warn")
        self.worker = None

    def _on_failed(self, message: str, detail: str) -> None:
        self.split_page.set_busy(False)
        self.batch_page.set_busy(False)
        self.current_mode = "full"
        self.split_page.set_status("任务失败。")
        self.batch_page.set_status("任务失败。")
        self.append_log(detail, "error")
        box = MessageBox("任务失败", message, self)
        box.cancelButton.hide()
        box.yesButton.setText("知道了")
        box.exec()
        self.worker = None

    # ------------------------------------------------------------------ 拖放
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if not path:
                continue
            if os.path.isdir(path) or Path(path).suffix.lower() in VIDEO_EXTS:
                paths.append(path)
        if not paths:
            return
        if len(paths) == 1 and os.path.isfile(paths[0]) and self.stackedWidget.currentWidget() != self.batch_page:
            self.switchTo(self.split_page)
            self.split_page.set_video_path(paths[0])
            self.notify("已载入视频", os.path.basename(paths[0]))
        else:
            self.switchTo(self.batch_page)
            self.batch_page.add_paths(paths)

    # ------------------------------------------------------------------ 关闭
    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            box = MessageBox("任务进行中", "仍有任务在运行，确定要退出吗？", self)
            box.yesButton.setText("退出")
            box.cancelButton.setText("继续处理")
            if not box.exec():
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(3000)
        try:
            store_params(self.split_page.export_state())
        except Exception:
            pass
        save_config()
        event.accept()
