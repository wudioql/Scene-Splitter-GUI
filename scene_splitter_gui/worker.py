# -*- coding: utf-8 -*-
"""后台工作线程：把 core 的同步流程包装成 Qt 信号，避免界面卡死。

支持三种运行模式：
  · full  —— 检测 + 分割一次完成（批量处理使用）
  · plan  —— 只做检测与分段规划（「预览分段」使用，不切割视频）
  · split —— 按已经预览确认好的分段列表执行切割（「开始分割」使用）
"""

from __future__ import annotations

import threading
import traceback
from typing import Optional, Sequence

from PySide6.QtCore import QThread, Signal

from . import core


class QtReporter(core.Reporter):
    """把 core 的进度/日志回调转换成 Qt 信号。"""

    def __init__(self, worker: "Worker"):
        self.worker = worker

    def log(self, message: str, level: str = "info") -> None:
        self.worker.sigLog.emit(str(message), level)

    def stage(self, text: str) -> None:
        self.worker._begin_stage(text)

    def progress(self, current: int, total: int) -> None:
        self.worker._report_progress(current, total)

    def cancelled(self) -> bool:
        return self.worker.is_cancelled()


class Worker(QThread):
    """执行一个或多个任务（批量处理时传多个 TaskParams）。"""

    sigLog = Signal(str, str)          # 消息, 级别
    sigStage = Signal(str)             # 当前阶段文本
    sigProgress = Signal(int)          # 总体进度 0-100
    sigFinished = Signal(object)       # list[TaskResult]
    sigCancelled = Signal()
    sigFailed = Signal(str, str)       # 摘要, 详细信息

    # 不同模式下各阶段占总进度的区间：(关键字, 起始%, 区间%)
    STAGES = {
        "full": (
            ("打开视频", 0, 2),
            ("正在检测场景", 2, 58),
            ("整理片段", 60, 4),
            ("正在导出缩略图", 62, 6),
            ("正在切割视频", 68, 32),
        ),
        "plan": (
            ("打开视频", 0, 4),
            ("正在检测场景", 4, 88),
            ("整理片段", 92, 8),
        ),
        "split": (
            ("正在导出缩略图", 0, 10),
            ("正在切割视频", 10, 90),
        ),
    }

    def __init__(self, jobs: Sequence[core.TaskParams], ffmpeg_path: str = "",
                 mode: str = "full", plans: Optional[Sequence[core.TaskResult]] = None,
                 parent=None):
        super().__init__(parent)
        self.jobs = list(jobs)
        self.ffmpeg_path = ffmpeg_path
        self.mode = mode if mode in self.STAGES else "full"
        self.plans = list(plans) if plans else []
        self._cancel_event = threading.Event()
        self._reporter = QtReporter(self)
        self._stage_base = 0
        self._stage_span = 2
        self._job_index = 0

    # —— 取消 ——
    def cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # —— 供 Reporter 调用 ——
    def _begin_stage(self, text: str) -> None:
        for key, base, span in self.STAGES[self.mode]:
            if key in text:
                self._stage_base, self._stage_span = base, span
                break
        prefix = ""
        if len(self.jobs) > 1:
            prefix = f"[{self._job_index + 1}/{len(self.jobs)}] "
        self.sigStage.emit(prefix + text)

    def _report_progress(self, current: int, total: int) -> None:
        ratio = max(0.0, min(1.0, current / total)) if total > 0 else 0.0
        within = self._stage_base + self._stage_span * ratio
        if len(self.jobs) > 1:
            overall = (self._job_index * 100 + within) / len(self.jobs)
        else:
            overall = within
        self.sigProgress.emit(int(max(0, min(100, overall))))

    # —— 单个任务的执行 ——
    def _run_job(self, index: int, job: core.TaskParams) -> core.TaskResult:
        if self.mode == "plan":
            return core.plan_task(job, self._reporter)
        if self.mode == "split":
            if index >= len(self.plans):
                raise ValueError("缺少预览结果，无法执行分割。")
            return core.execute_plan(job, self.plans[index], self._reporter, self.ffmpeg_path)
        return core.run_task(job, self._reporter, self.ffmpeg_path)

    # —— 线程主循环 ——
    def run(self) -> None:  # noqa: D401
        results = []
        try:
            for index, job in enumerate(self.jobs):
                if self.is_cancelled():
                    raise core.Cancelled()
                self._job_index = index
                if len(self.jobs) > 1:
                    self.sigStage.emit(
                        f"[{index + 1}/{len(self.jobs)}] 开始处理："
                        f"{job.video_path.replace(chr(92), '/').rsplit('/', 1)[-1]}")
                results.append(self._run_job(index, job))
            if self.is_cancelled():
                raise core.Cancelled()
        except core.Cancelled:
            self.sigLog.emit("任务已被用户取消。", "warn")
            self.sigCancelled.emit()
            return
        except Exception as exc:  # noqa: BLE001
            detail = traceback.format_exc()
            self.sigLog.emit(f"出错：{exc}", "error")
            self.sigFailed.emit(str(exc), detail)
            return
        self.sigFinished.emit(results)
