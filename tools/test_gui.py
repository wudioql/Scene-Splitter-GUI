# -*- coding: utf-8 -*-
"""GUI 端到端自测（离屏运行）：覆盖单文件、批量、取消、参数持久化。"""

from __future__ import annotations

import os
import shutil
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

SRC = "/tmp/test_input.mp4"
FAILED: list[str] = []


def check(condition: bool, label: str) -> None:
    print(("  [通过] " if condition else "  [失败] ") + label)
    if not condition:
        FAILED.append(label)


def wait(ms: int = 400) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def run_until_done(window, timeout_ms: int = 180_000) -> None:
    timer = QTimer()
    timer.timeout.connect(lambda: (window.worker is None) and app.quit())
    timer.start(120)
    QTimer.singleShot(timeout_ms, app.quit)
    app.exec()
    wait(300)


app = QApplication(sys.argv)

from scene_splitter_gui import core  # noqa: E402
from scene_splitter_gui.config import CFG, init_config, qconfig  # noqa: E402
from scene_splitter_gui.main_window import MainWindow  # noqa: E402

init_config()
qconfig.set(CFG.autoOpenOutput, False)
qconfig.set(CFG.autoSwitchResult, False)
qconfig.set(CFG.rememberParams, True)

window = MainWindow()
window.resize(1240, 880)
window.show()
wait(400)

print("1) 启动与环境")
check(core.SCENEDETECT_AVAILABLE, "PySceneDetect 可用")
check(core.resolve_ffmpeg() is not None, "ffmpeg 可用")
check(window.split_page.infoCard.label.text() != "", "信息卡已初始化")

print("2) 分段限制：最少/最多帧数")
# 先清掉上次运行残留的参数记忆，保证测试从默认值开始
page0 = window.split_page
page0.reset_params()
wait(200)
out_dir = "/tmp/gui_e2e_single"
shutil.rmtree(out_dir, ignore_errors=True)
page = window.split_page
page.set_video_path(SRC)
wait(500)
page.outputCard.setText(out_dir)
page.overwriteCard.setChecked(True)
page._on_preview_clicked()
run_until_done(window)
check(page._preview_result is not None and page._preview_result.scene_count > 0, "预览（仅检测）得到分段结果")
if page.video_info:
    fps = page.video_info.fps

# 用 core 直接验证帧数限制的边界行为
class Silent(core.Reporter):
    def log(self, message, level="info"):
        pass


base = dict(video_path=SRC, output_dir=out_dir, detector="content",
            detector_params={"threshold": 12.0, "min_scene_len": 0},
            do_split=False, save_csv=False, overwrite=True)
tight = core.run_task(core.TaskParams(**base, min_scene_frames=60, max_scene_frames=60), Silent())
counts = sorted(s.frame_count for s in tight.scenes)
# 最少=最多时属于冲突配置：按设计优先保证最短/最少，允许个别片段略超上限
check(all(c >= 55 for c in counts) and all(c <= 110 for c in counts),
      f"最少=最多帧数(60) 时优先保证下限：{counts}")
check(sum(counts) == tight.info.frame_count, "冲突配置下仍然不丢帧")

wide = core.run_task(core.TaskParams(**base, min_scene_frames=40, max_scene_frames=200), Silent())
check(all(40 <= s.frame_count <= 200 for s in wide.scenes),
      f"帧数落在 40~200 之间：{[s.frame_count for s in wide.scenes]}")
check(sum(s.frame_count for s in wide.scenes) == wide.info.frame_count, "切片总帧数等于视频总帧数（不丢帧）")

sec_limits = core.run_task(core.TaskParams(**base, min_scene_sec=1.0, max_scene_sec=2.0), Silent())
check(all(0.9 <= s.duration <= 2.1 for s in sec_limits.scenes),
      f"秒级限制生效：{[round(s.duration,2) for s in sec_limits.scenes]}")

print("3) 预览 → 确认 → 分割 工作流")
out_dir2 = "/tmp/gui_e2e_split"
shutil.rmtree(out_dir2, ignore_errors=True)
page.outputCard.setText(out_dir2)
page.minSecCard.setValue(1.0)
page.maxSecCard.setValue(3.0)
page.templateCard.setText("$VIDEO_NAME_$SCENE_NUMBER")
page.padCard.setValue(2)
page.csvCard.setChecked(True)
page.htmlCard.setChecked(True)
page.overwriteCard.setChecked(True)
wait(300)
check(not page.splitBtn.isEnabled(), "未预览时「开始分割」不可点")

page._on_preview_clicked()
run_until_done(window)
preview = page._preview_result
check(preview is not None and preview.scene_count > 1, f"预览得到 {preview.scene_count if preview else 0} 段")
check(page.previewTable.rowCount() == preview.scene_count, "预览表格行数与段数一致")
check(page.splitBtn.isEnabled(), "预览后「开始分割」可点")
check(not os.path.isdir(out_dir2) or not any(f.endswith(".mp4") for f in os.listdir(out_dir2)),
      "预览阶段没有输出任何视频文件")
for _ in range(100):  # 缩略图后台抓取：等出现（超时约 10 秒）
    if page.thumbLayout.count() - 1 >= 1:
        break
    wait(100)
check(page.thumbLayout.count() - 1 >= 1, f"选中片段后显示画面预览（{page.thumbLayout.count()-1} 张）")
check(hasattr(page, "segPlayer"), "分段试看播放器已创建")
check(page.segPlayer.segment_index() == 0,
      f"播放器已载入第 1 段（当前 {page.segPlayer.segment_index()}）")
page.previewTable.selectRow(1)
wait(200)
check(page.segPlayer.segment_index() == 1, "切换选中段后播放器跟着切换")
page.previewTable.selectRow(0)
wait(200)

print("   3.0 上一段/下一段按钮")
check(not page.segPlayer.prevBtn.isEnabled(), "第 1 段时「上一段」禁用")
check(page.segPlayer.nextBtn.isEnabled(), "第 1 段时「下一段」可用")
page.segPlayer.nextBtn.click()
wait(200)
check(page.segPlayer.segment_index() == 1, "点「下一段」切到第 2 段")
check(page.previewTable.currentRow() == 1, "表格选中行跟着联动")
check(page.segPlayer.prevBtn.isEnabled(), "第 2 段时「上一段」可用")
last = preview.scene_count - 1
page.previewTable.selectRow(last)
wait(200)
check(not page.segPlayer.nextBtn.isEnabled(), "最后一段时「下一段」禁用")
page.segPlayer.nextBtn.click()
wait(200)
check(page.segPlayer.segment_index() == last, "尾段点「下一段」不越界")
page.segPlayer.prevBtn.click()
wait(200)
check(page.segPlayer.segment_index() == last - 1, "点「上一段」回到前一段")
page.previewTable.selectRow(0)
wait(200)
check(getattr(page._preview_result, "smart_used", False), "智能择优已参与分段规划")

print("   3.1 改动分段参数 -> 预览应标记为过期")
page.maxSecCard.setValue(2.0)
wait(800)
check(getattr(page, "_preview_stale", False), "参数改动后标记为过期")
check(not page.splitBtn.isEnabled(), "过期预览时「开始分割」被禁用")
page._on_split_clicked()
wait(300)
check(window.worker is None, "过期预览不会被强制执行")

print("   3.2 重新预览 + 手动增删段")
page._on_preview_clicked()
run_until_done(window)
count_before = page._preview_result.scene_count
page.previewTable.selectRow(2)
page._delete_selected_segment()
wait(300)
check(page._preview_result.scene_count == count_before - 1, "删除选中段生效")
check(page.splitBtn.isEnabled(), "手动调整后仍可分割")
first_end = page._preview_result.scenes[0].end_frame
second_len = page._preview_result.scenes[1].frame_count
page.previewTable.selectRow(1)
page._merge_selected_segment()
wait(300)
check(page._preview_result.scenes[0].end_frame == first_end + second_len, "与上一段合并生效")
check(any("空档" in w for w in core.summarize(page._preview_result.scenes)["warnings"]),
      "手动删段后的空档有提示")

expected = [(s.start_frame, s.end_frame) for s in page._preview_result.scenes]
page._on_split_clicked()
run_until_done(window)
res = window.result_page.current
check(len(res.outputs) == len(expected), f"输出数量与预览一致（{len(res.outputs)}）")
check([(s.start_frame, s.end_frame) for s in res.scenes] == expected, "输出帧区间与预览完全一致")
check(os.path.isfile(res.csv_path) and os.path.isfile(res.html_path), "CSV 与 HTML 报告已生成")
check(window.result_page.table.rowCount() == res.scene_count, "结果表格行数正确")
check(page.previewTable.updatesEnabled() and window.result_page.table.updatesEnabled(), "表格填充后 updates 守卫已恢复")
check("全部完成" in window.result_page.logEdit.toPlainText(), "日志记录了完成信息")
check(window.result_page.logEdit.maximumBlockCount() == 2000, "日志框有 2000 行上限（防无界增长）")
for path in res.outputs:
    check(os.path.getsize(path) > 1000, f"输出文件非空：{os.path.basename(path)}")

print("   3.3 仅检测并导出报告（不切视频）")
report_dir = "/tmp/gui_e2e_report"
shutil.rmtree(report_dir, ignore_errors=True)
page.outputCard.setText(report_dir)
page.reportBtn.click()
run_until_done(window)
report = window.result_page.current
check(report is not None and report.scene_count > 0 and not report.outputs,
      "报告模式：有分段结果但没有输出视频")
check(report is not None and os.path.isfile(report.csv_path),
      f"报告模式已导出 CSV：{report.csv_path if report else None}")
check(report is not None and os.path.isfile(report.html_path),
      f"报告模式已导出 HTML：{report.html_path if report else None}")
check(report.scene_count == page._preview_result.scene_count or report.scene_count > 0,
      "报告模式可正常得到分段列表")
page.outputCard.setText(out_dir2)

print("4) 批量处理")
out_dir3 = "/tmp/gui_e2e_batch"
shutil.rmtree(out_dir3, ignore_errors=True)
shutil.copy(SRC, "/tmp/e2e_a.mp4")
shutil.copy(SRC, "/tmp/e2e_b.mp4")
batch = window.batch_page
batch.clear()
batch.add_paths(["/tmp/e2e_a.mp4", "/tmp/e2e_b.mp4"])
batch.outputCard.setText(out_dir3)
batch.perFileDirCard.setChecked(True)
batch._start()
run_until_done(window)
check(window.result_page.table.rowCount() == sum(r.scene_count for r in window.result_page.results),
      "批量结果已合并展示")
check(window.result_page.table.columnCount() == 8, "多视频表格包含「来源视频」列")
statuses = [batch.table.item(r, 3).text() for r in range(2)]
expected = [f"完成 {r.scene_count} 段" for r in window.result_page.results]
check(statuses == expected, f"批量状态列：{statuses}（期望 {expected}）")
check(os.path.isdir(os.path.join(out_dir3, "e2e_a")), "按视频单独建了子文件夹")

print("5) 取消任务")
batch.clear()
batch.add_paths([SRC])
batch.outputCard.setText("/tmp/gui_e2e_cancel")
batch._start()
wait(120)
window.cancel_jobs()
run_until_done(window)
check("取消" in window.split_page.statusLabel.text() or "取消" in batch.statusLabel.text(),
      "停止按钮生效")

print("6) 参数持久化")
page.padCard.setValue(4)
page.templateCard.setText("片段_$SCENE_NUMBER_$DATE")
page.minSecCard.setValue(2.5)
page.codecCard.setCurrentIndex(0)
state = page.export_state()
from scene_splitter_gui.config import store_params  # noqa: E402
store_params(state)
window2 = MainWindow()
wait(300)
p2 = window2.split_page
check(p2.padCard.value() == 4, f"序号位数已恢复：{p2.padCard.value()}")
check(p2.templateCard.text() == "片段_$SCENE_NUMBER_$DATE", f"模板已恢复：{p2.templateCard.text()}")
check(abs(p2.minSecCard.value() - 2.5) < 1e-6, f"最短时长已恢复：{p2.minSecCard.value()}")
check(p2.codecCard.currentIndex() == 0, "编码方式已恢复")
from pathlib import Path as _Path  # noqa: E402
import scene_splitter_gui as _pkg  # noqa: E402
from scene_splitter_gui import config as _cfgmod  # noqa: E402
_proj_root = _Path(str(_pkg.__file__)).resolve().parent.parent
check(_cfgmod.CONFIG_FILE == _proj_root / ".scene_splitter_gui" / "config.json",
      "配置在项目目录下（便携）")
_cfgmod.save_config()
check(_cfgmod.CONFIG_FILE.exists(), "配置文件已落盘到项目目录")
_mig = _Path("/tmp/cfg_mig_test")
shutil.rmtree(_mig, ignore_errors=True)
(_mig / "old").mkdir(parents=True)
(_mig / "new").mkdir(parents=True)
(_mig / "old" / "config.json").write_text('{"k": 1}', encoding="utf-8")
check(_cfgmod._migrate_legacy_config(_mig / "old" / "config.json",
                                     _mig / "new" / "config.json"), "旧配置迁移成功")
check((_mig / "new" / "config.json").read_text(encoding="utf-8") == '{"k": 1}', "迁移内容一致")
check(not (_mig / "old" / "config.json").exists(), "迁移后旧文件已清理")
(_mig / "old" / "config.json").write_text('{"k": 9}', encoding="utf-8")
check(not _cfgmod._migrate_legacy_config(_mig / "old" / "config.json",
                                         _mig / "new" / "config.json"), "目标已存在时不覆盖")
shutil.rmtree(_mig, ignore_errors=True)

print("7) 参数校验")
bad = core.TaskParams(video_path=SRC, output_dir="/tmp/x", min_scene_sec=5.0, max_scene_sec=1.0)
check(bool(core.validate_params(bad)), "最长 < 最短时能报错")
bad2 = core.TaskParams(video_path="", output_dir="/tmp/x")
check(bool(core.validate_params(bad2)), "未选视频时能报错")

print("8) 深色主题渲染")
from qfluentwidgets import Theme, setTheme  # noqa: E402
setTheme(Theme.DARK)
wait(400)
window.switchTo(window.result_page)
wait(400)
window.grab().save("/tmp/shot_dark.png")
check(os.path.exists("/tmp/shot_dark.png"), "深色主题下可正常渲染")
setTheme(Theme.LIGHT)

print("9) 播放器本段操作 + 快捷键")
window.switchTo(page)  # 第8节切走了页面，快捷键测试先切回来（焦点才有效）
wait(300)
page.minSecCard.setValue(1.0)
page.maxSecCard.setValue(3.0)
wait(300)
page._on_preview_clicked()
run_until_done(window)
base_n = page._preview_result.scene_count
check(base_n >= 5, f"重预览得到 {base_n} 段（需 ≥5 段才够测四次操作）")
page.previewTable.selectRow(1)
wait(200)
check(page.segPlayer.deleteBtn.isEnabled() and page.segPlayer.mergeBtn.isEnabled(),
      "有选中段时播放器操作键可用")
page.segPlayer.mergeBtn.click()
wait(300)
check(page._preview_result.scene_count == base_n - 1, "播放器「与上一段合并」生效")
check(page._preview_manual, "播放器操作后标记为手动调整")
page.previewTable.selectRow(0)
wait(200)
page.segPlayer.deleteBtn.click()
wait(300)
check(page._preview_result.scene_count == base_n - 2, "播放器「删除本段」生效")
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtCore import Qt as _Qt  # noqa: E402
page.previewTable.setFocus()
page.previewTable.selectRow(1)
wait(200)
QTest.keyClick(page.previewTable, _Qt.Key_M)
wait(300)
check(page._preview_result.scene_count == base_n - 3, "快捷键 M 合并生效")
page.previewTable.selectRow(0)
wait(200)
QTest.keyClick(page.previewTable, _Qt.Key_Delete)
wait(300)
check(page._preview_result.scene_count == base_n - 4, "快捷键 Delete 删除生效")
# 输入框内按键必须放行：文字被改了，但分段数不动
guard_n = page._preview_result.scene_count
edit = page.templateCard.edit
edit.setFocus()
wait(100)
t0 = edit.text()
edit.setCursorPosition(0)
QTest.keyClick(edit, _Qt.Key_Delete)
wait(200)
check(page._preview_result.scene_count == guard_n, "输入框内按 Delete 不触发删除")
check(edit.text() != t0, "输入框内 Delete 正常删除字符（按键未被吃掉）")
QTest.keyClick(edit, _Qt.Key_M)
wait(200)
check(page._preview_result.scene_count == guard_n, "输入框内按 M 不触发合并")

print("10) 删/合后自动选中相邻段并载入播放器")
page.minSecCard.setValue(1.0)
page.maxSecCard.setValue(3.0)
wait(300)
page._on_preview_clicked()
run_until_done(window)
n0 = page._preview_result.scene_count
check(n0 >= 5, f"重预览得到 {n0} 段")
page.previewTable.selectRow(2)
wait(200)
page.deleteSegBtn.click()
wait(300)
check(page._preview_result.scene_count == n0 - 1, "删除后段数 -1")
check(page._selected_rows() == [2], f"删除后选中滑入段（当前 {page._selected_rows()}）")
check(page.segPlayer.segment_index() == 2, "播放器跟随载入滑入段")
check(page.segPlayer.deleteBtn.isEnabled() and page.segPlayer.nextBtn.isEnabled()
      and page.segPlayer.mergeBtn.isEnabled(), "删段后播放器操作键可用")
# 注意：删段后留下了空档，合并必须选空档之外的相邻两段
page.previewTable.selectRow(4)
wait(200)
page.mergeSegBtn.click()
wait(300)
check(page._preview_result.scene_count == n0 - 2, "合并后段数 -1")
check(page._selected_rows() == [3], f"合并后选中合体段（当前 {page._selected_rows()}）")
check(page.segPlayer.segment_index() == 3, "播放器跟随载入合体段")
last = page._preview_result.scene_count - 1
page.previewTable.selectRow(last)
wait(200)
page.deleteSegBtn.click()
wait(300)
check(page._selected_rows() == [last - 1], f"删尾段后选中新尾段（当前 {page._selected_rows()}）")
check(page.segPlayer.segment_index() == last - 1, "播放器跟随新尾段")

print("11) 画面收起 + 循环静音记忆 + 首帧定位")
window.switchTo(page)
window.show()  # run_until_done 的 app.quit() 会藏起主窗口，先恢复（真实使用中窗口一直可见）
wait(300)
check(page.thumbToggle.height() >= page.thumbToggle.sizeHint().height() - 2,
      "收起按钮高度正常（不被压扁）")
window3 = MainWindow()
window3.resize(1240, 880)
window3.show()
wait(500)
p3 = window3.split_page
check(p3.thumbToggle.height() >= p3.thumbToggle.sizeHint().height() - 2,
      f"新窗口收起按钮高度正常（{p3.thumbToggle.height()} vs hint {p3.thumbToggle.sizeHint().height()}）")
window3.hide()
h_expanded = page.thumbHolder.height()
check(page.thumbRow.isVisible(), "默认展开画面预览")
page.thumbToggle.click()
wait(200)
check(not page.thumbRow.isVisible(), "收起后图片行隐藏")
check(page.thumbHolder.height() < h_expanded,
      f"收起后高度变小（{h_expanded}->{page.thumbHolder.height()}）")
check(page.thumbToggle.text() == "展开画面", "按钮文字切换为展开画面")
check(bool(qconfig.get(CFG.collapseThumbs)), "收起状态记入配置")
page.thumbToggle.click()
wait(300)
check(page.thumbRow.isVisible(), "再点后展开画面")
check(not bool(qconfig.get(CFG.collapseThumbs)), "展开状态记入配置")
from scene_splitter_gui.segment_player import (MULTIMEDIA_AVAILABLE as _MM,  # noqa: E402
                                               QMediaPlayer as _QMP, SegmentPlayer)
page.segPlayer.loopBox.setChecked(False)
page.segPlayer.muteBox.setChecked(True)
wait(200)
check(not bool(qconfig.get(CFG.loopPreview)) and bool(qconfig.get(CFG.mutePreview)),
      "循环/静音记入配置")
if _MM and page.segPlayer._audio is not None:
    check(page.segPlayer._audio.isMuted(), "静音勾选即时生效")
    check(page.segPlayer._audio.volume() == 0.0, "静音把音量拉到0（绕开后端setMuted无效）")
_probe = SegmentPlayer(window, page.view)
check(not _probe.loopBox.isChecked() and _probe.muteBox.isChecked(), "新建播放器读到记住的值")
_probe.deleteLater()
page.segPlayer.loopBox.setChecked(True)
page.segPlayer.muteBox.setChecked(False)
wait(200)
if _MM and page.segPlayer._audio is not None:
    check(page.segPlayer._audio.volume() == 0.0, "暂停态音量保持0（播时才给音量）")
    page.segPlayer.playBtn.click()
    wait(400)
    check(abs(page.segPlayer._audio.volume() - 0.8) < 1e-6, "播放时音量给到0.8")
    page.segPlayer.playBtn.click()
    wait(300)
page.segPlayer.load_segment(SRC, 0, 2.5025, 5.0)
check(page.segPlayer._play_start == 2503, f"2.5025s→2503ms（当前 {page.segPlayer._play_start}）")
page.segPlayer.load_segment(SRC, 0, 3.0, 6.0)
check(page.segPlayer._play_start == 3000, "整数秒不受影响")
wait(800)
check(not page.segPlayer._pulse_active, "刷新点播已收敛")
if _MM and page.segPlayer._player is not None:
    check(page.segPlayer._player.playbackState() != _QMP.PlayingState, "点播结束后回到暂停")
    check(page.segPlayer._audio.isMuted() == page.segPlayer.muteBox.isChecked(), "静音设置已还原")

print("12) 进度条点按跳转 + 点播界面静默")
from PySide6.QtCore import QPoint as _QPoint  # noqa: E402
window.switchTo(page)
window.show()
wait(300)
seg = page.segPlayer
check(seg._row == 0 and seg._play_start == 3000, "沿用第11节载入的段（3000~6000ms）")
check(seg.slider.value() == 0, "点播收敛后进度条停在段首（界面静默）")
pos0 = seg._player.position() if (_MM and seg._player is not None) else -1
seg._on_position(3000 + 1500)  # 走产品回填路径（内已 blockSignals）：不应寻址
wait(300)
if _MM and seg._player is not None:
    check(seg._player.position() == pos0, "程序回填进度条不触发寻址")
    check(seg.slider.value() == 1500, "回填后进度条显示正确")
seg.slider.blockSignals(True)
seg.slider.setValue(0)
seg.slider.blockSignals(False)
wait(200)
w = seg.slider.width()
QTest.mouseClick(seg.slider, _Qt.LeftButton, _Qt.NoModifier,
                 _QPoint(int(w * 0.75), seg.slider.height() // 2))
wait(900)
v = seg.slider.value()
check(1800 <= v <= 2700, f"点按3/4处跳到对应位置（slider={v}，页步进只会到300）")
if _MM and seg._player is not None:
    check(abs(seg._player.position() - (3000 + v)) <= 1000, "播放点跟随点按位置")
    check(seg._player.playbackState() != _QMP.PlayingState, "点播结束后回到暂停")
    seg.playBtn.click()
    wait(200)
    pos_before = seg._player.position()
    QTest.mouseClick(seg.slider, _Qt.LeftButton, _Qt.NoModifier,
                     _QPoint(int(w * 0.15), seg.slider.height() // 2))
    wait(500)
    pos_after = seg._player.position()
    check(pos_after < pos_before - 500, f"在播点按往回跳（{pos_before}->{pos_after}）")
    check(seg.slider.value() < 1500, "在播点按后进度条不跳回原处")
    seg.playBtn.click()
    wait(300)

print("13) 首次建配置 + 点播音量恢复")
window.switchTo(page)
window.show()
wait(300)
seg.load_segment(SRC, 0, 1.0, 4.0)
wait(900)
if _MM and seg._player is not None:
    check(seg._audio.volume() == 0.0, "点播结束后音量保持0（暂停态无声）")
_cfg_bak = _cfgmod.CONFIG_FILE.read_bytes()
_cfgmod.CONFIG_FILE.unlink()
try:
    _cfgmod.init_config()
    check(_cfgmod.CONFIG_FILE.exists(), "首次使用自动在项目里创建配置")
finally:
    _cfgmod.CONFIG_FILE.write_bytes(_cfg_bak)

print("\n===== 结果 =====")
print("全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项 ❌：{FAILED}")

for _ in range(100):  # 退出前等后台抓帧收尾
    _t = page._grab_thread
    if _t is None or not _t.isRunning():
        break
    wait(100)
sys.exit(1 if FAILED else 0)
