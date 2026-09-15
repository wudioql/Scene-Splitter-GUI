# -*- coding: utf-8 -*-
"""重新生成 docs/ 下 6 张 README 截图（离屏渲染，1280x900）。

用法：python tools/make_screenshots.py
"""
from __future__ import annotations

import os
import shutil
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QPoint, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402
from qfluentwidgets import InfoBar  # noqa: E402

SRC = "/tmp/test_input.mp4"
OUT = "/tmp/shot_out"
DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")

app = QApplication(sys.argv)

from scene_splitter_gui.config import CFG, init_config, qconfig  # noqa: E402
from scene_splitter_gui.main_window import MainWindow  # noqa: E402

init_config()
qconfig.set(CFG.autoOpenOutput, False)
qconfig.set(CFG.autoSwitchResult, False)
qconfig.set(CFG.rememberParams, False)

window = MainWindow()
window.resize(1280, 900)
window.show()


def wait(ms: int = 400) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def run_until_done(timeout_ms: int = 180_000) -> None:
    timer = QTimer()
    timer.timeout.connect(lambda: (window.worker is None) and app.quit())
    timer.start(120)
    QTimer.singleShot(timeout_ms, app.quit)
    app.exec()
    wait(300)


def wait_thumbs(timeout_ms: int = 10_000) -> None:
    """等缩略图后台抓完（选中行已缓存且无在途线程/排队），截图才稳定。"""
    rows = page._selected_rows()
    want = rows[0] if rows else -1
    waited = 0
    while waited < timeout_ms:
        t = page._grab_thread
        idle = t is None or not t.isRunning()
        if want >= 0 and want in page._frames_cache \
                and page._grab_pending is None and idle:
            break
        wait(100)
        waited += 100
    wait(150)
    app.processEvents()


def clear_bars() -> None:
    """关掉残留的右上提示条，避免新旧叠在一起。"""
    for bar in window.findChildren(InfoBar):
        try:
            bar.close()
            bar.deleteLater()
        except Exception:
            pass
    wait(250)
    app.processEvents()


def scroll_to(page, widget=None, frac: float = 0.0, offset: int = 150, settle: int = 600) -> None:
    scroll = page.findChild(QScrollArea)
    bar = scroll.verticalScrollBar()
    if widget is not None:
        y = widget.mapTo(page.view, QPoint(0, 0)).y()
        bar.setValue(max(0, y - offset))
    else:
        bar.setValue(int(bar.maximum() * frac))
    wait(settle)
    app.processEvents()


def shoot(name: str) -> None:
    wait(250)
    app.processEvents()
    path = os.path.join(DOCS, name)
    window.grab().save(path)
    print("saved", name, flush=True)


def fresh_state(template="$VIDEO_NAME_$SCENE_NUMBER", pad=3) -> None:
    page.reset_params()
    wait(200)
    page.set_video_path(SRC)
    wait(500)
    page.outputCard.setText(OUT)
    page.templateCard.setText(template)
    page.padCard.setValue(pad)
    page.overwriteCard.setChecked(True)
    # 经典 5 段效果：阈值 12（默认值 27 在这条测试视频上只检出 2 段）
    page._param_cards["content"]["threshold"].setValue(12.0)
    wait(200)


page = window.split_page
shutil.rmtree(OUT, ignore_errors=True)
fresh_state()

# 1) 视频分割页：顶部（输入 + 场景检测头含重置按钮 + 检测算法）
window.switchTo(page)
scroll_to(page, frac=0.0)
shoot("截图-视频分割页.png")

# 2) 默认预览 → 预览与分段：表格 + 缩略图 + 试看播放器（无提示条，干净）
clear_bars()
page._on_preview_clicked()
run_until_done()
assert page.previewTable.rowCount() == 5, page.previewTable.rowCount()
wait_thumbs()
clear_bars()
scroll_to(page, page.previewTable, offset=260)
shoot("截图-预览与分段.png")

# 3) 加限制再预览（触发智能择优）→ 预览结果：保留新鲜提示条 + 规划摘要 + 表格
page.minSecCard.setValue(1.0)
page.maxSecCard.setValue(2.0)
wait(300)
clear_bars()
page._on_preview_clicked()
run_until_done()
assert getattr(page._preview_result, "smart_used", False), "智能择优应参与"
page._show_segment_frames(force=True)  # 强制刷新走后台线程
wait_thumbs()
scroll_to(page, page.previewCard, offset=200, settle=350)
shoot("截图-预览结果.png")

# 4) 恢复默认 + 经典命名 → 预览 → 分割 → 结果页（保留“任务完成”提示条）
fresh_state(template="$VIDEO_NAME_第$SCENE_NUMBER段", pad=2)
window.switchTo(page)
clear_bars()
page._on_preview_clicked()
run_until_done()
assert page.previewTable.rowCount() == 5
clear_bars()
page._on_split_clicked()
run_until_done()
window.switchTo(window.result_page)
wait(200)
scroll_to(window.result_page, frac=0.0, settle=350)
shoot("截图-结果页.png")

# 5) 设置页（无提示条；记住参数恢复为开，展示常规状态）
clear_bars()
window.settings_page.rememberCard.setChecked(True)
window.switchTo(window.settings_page)
wait(400)
scroll_to(window.settings_page, frac=0.0)
shoot("截图-设置页.png")

# 6) 深色主题：分段设置（含智能开关）+ 输出设置
from qfluentwidgets import Theme, setTheme  # noqa: E402

clear_bars()
setTheme(Theme.DARK)
window.switchTo(page)
wait(500)
scroll_to(page, page.smartCard, offset=300)
shoot("截图-深色主题.png")
setTheme(Theme.LIGHT)

print("done ✅")
