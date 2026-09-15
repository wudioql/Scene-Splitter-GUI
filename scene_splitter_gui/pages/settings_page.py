# -*- coding: utf-8 -*-
"""「设置」页面：外观、行为、环境依赖、参数重置与关于信息。"""

from __future__ import annotations

import sys

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    LargeTitleLabel,
    MessageBox,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SettingCardGroup,
)

from .. import core
from ..config import (
    APP_NAME,
    APP_SUBTITLE,
    APP_VERSION,
    CFG,
    CONFIG_FILE,
    THEME_COLORS,
    THEME_MODES,
    apply_appearance,
    qconfig,
    reset_config,
    save_config,
)
from ..widgets import make_scroll_page, ChoiceCard, InfoCard, LineEditCard, SwitchCard


class SettingsPage(QWidget):
    """应用设置。"""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("settingsPage")
        self._process: QProcess | None = None
        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        scroll, self.view = make_scroll_page(self)
        self.vBox = QVBoxLayout(self.view)
        self.vBox.setContentsMargins(30, 20, 30, 36)
        self.vBox.setSpacing(14)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.vBox.addWidget(LargeTitleLabel("设置", self.view))
        self.vBox.addWidget(CaptionLabel(APP_SUBTITLE, self.view))
        self.vBox.addSpacing(4)

        # 外观
        look = SettingCardGroup("外观", self.view)
        self.themeCard = ChoiceCard(FluentIcon.BRUSH, "主题模式",
                                    "跟随系统时会随 Windows 的浅色/深色设置自动切换",
                                    list(THEME_MODES.keys()), 0, width=200)
        self.colorCard = ChoiceCard(FluentIcon.PALETTE, "主题色", "界面主色",
                                    list(THEME_COLORS.keys()), 0, width=200)
        look.addSettingCard(self.themeCard)
        look.addSettingCard(self.colorCard)
        self.vBox.addWidget(look)

        # 行为
        behavior = SettingCardGroup("行为", self.view)
        self.autoOpenCard = SwitchCard(FluentIcon.FOLDER, "任务完成后打开输出目录", "", True)
        self.autoSwitchCard = SwitchCard(FluentIcon.VIEW, "任务完成后切换到结果页", "", True)
        self.verboseCard = SwitchCard(FluentIcon.VIEW, "输出详细日志",
                                      "关闭后日志只保留阶段信息与警告/错误", True)
        self.rememberCard = SwitchCard(FluentIcon.SAVE, "记住上次使用的参数",
                                       "下次启动时自动恢复检测、分段与输出设置", True)
        for card in (self.autoOpenCard, self.autoSwitchCard, self.verboseCard, self.rememberCard):
            behavior.addSettingCard(card)
        self.vBox.addWidget(behavior)

        # 环境
        env = SettingCardGroup("环境与依赖", self.view)
        self.depCard = InfoCard("正在检测依赖…", self.view)
        env.addSettingCard(self.depCard)
        self.ffmpegCard = LineEditCard(
            FluentIcon.DEVELOPER_TOOLS, "ffmpeg 路径",
            "留空则自动查找：系统 PATH → imageio-ffmpeg 内置版本。也可手动指定 ffmpeg.exe",
            browse="file", file_filter="ffmpeg (ffmpeg.exe ffmpeg);;所有文件 (*.*)", width=380)
        env.addSettingCard(self.ffmpegCard)
        self.installBtn = PrimaryPushButton(FluentIcon.DOWNLOAD, "一键安装 / 更新依赖")
        self.installBtn.clicked.connect(self._install_dependencies)
        self.refreshBtn = PushButton(FluentIcon.SYNC, "重新检测环境")
        self.refreshBtn.clicked.connect(self.refresh_environment)
        buttons = QWidget(self.view)
        from PySide6.QtWidgets import QHBoxLayout
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.installBtn)
        row.addWidget(self.refreshBtn)
        row.addStretch(1)
        env.addSettingCard(buttons)

        self.pipLog = PlainTextEdit(self.view)
        self.pipLog.setReadOnly(True)
        self.pipLog.setPlaceholderText("安装输出会显示在这里……")
        self.pipLog.setMinimumHeight(140)
        self.pipLog.setVisible(False)
        env.addSettingCard(self.pipLog)
        self.vBox.addWidget(env)

        # 参数
        params = SettingCardGroup("参数与配置", self.view)
        self.resetParamsBtn = PushButton(FluentIcon.ERASE_TOOL, "重置参数为默认值")
        self.resetParamsBtn.clicked.connect(self._reset_params)
        self.openConfigBtn = PushButton(FluentIcon.FOLDER, "打开配置文件所在目录")
        self.openConfigBtn.clicked.connect(lambda: core.open_path_in_explorer(str(CONFIG_FILE.parent)))
        self.resetAllBtn = PushButton(FluentIcon.DELETE, "恢复出厂设置（含外观）")
        self.resetAllBtn.clicked.connect(self._reset_all)
        param_buttons = QWidget(self.view)
        prow = QHBoxLayout(param_buttons)
        prow.setContentsMargins(0, 0, 0, 0)
        prow.setSpacing(8)
        for button in (self.resetParamsBtn, self.openConfigBtn, self.resetAllBtn):
            prow.addWidget(button)
        prow.addStretch(1)
        params.addSettingCard(param_buttons)
        params.addSettingCard(InfoCard(f"配置文件位置：{CONFIG_FILE}", self.view))
        self.vBox.addWidget(params)

        # 关于
        about = SettingCardGroup("关于", self.view)
        about.addSettingCard(InfoCard(
            f"{APP_NAME} v{APP_VERSION}\n"
            "· 场景检测：PySceneDetect（BSD-3-Clause，作者 Brandon Castellano）\n"
            "· 视频切割：ffmpeg（LGPL/GPL）\n"
            "· 界面：PySide6 + PySide6-Fluent-Widgets（MIT）\n"
            "本工具仅做批量切割的前端封装，不含任何视频内容；请遵守当地法律与版权规定。",
            self.view))
        self.vBox.addWidget(about)
        self.vBox.addStretch(1)

        self.themeCard.currentIndexChanged.connect(self._on_theme_changed)
        self.colorCard.currentIndexChanged.connect(self._on_color_changed)
        self.autoOpenCard.checkedChanged.connect(
            lambda v: self._set(CFG.autoOpenOutput, v))
        self.autoSwitchCard.checkedChanged.connect(
            lambda v: self._set(CFG.autoSwitchResult, v))
        self.verboseCard.checkedChanged.connect(lambda v: self._set(CFG.verboseLog, v))
        self.rememberCard.checkedChanged.connect(lambda v: self._set(CFG.rememberParams, v))
        self.ffmpegCard.textChanged.connect(self._on_ffmpeg_changed)

    # ------------------------------------------------------------------ 读写配置
    def _load_values(self) -> None:
        self.themeCard.setCurrentIndex(list(THEME_MODES.keys()).index(
            qconfig.get(CFG.uiThemeMode)) if qconfig.get(CFG.uiThemeMode) in THEME_MODES else 0)
        self.colorCard.setCurrentIndex(list(THEME_COLORS.keys()).index(
            qconfig.get(CFG.uiThemeColor)) if qconfig.get(CFG.uiThemeColor) in THEME_COLORS else 0)
        self.autoOpenCard.setChecked(bool(qconfig.get(CFG.autoOpenOutput)))
        self.autoSwitchCard.setChecked(bool(qconfig.get(CFG.autoSwitchResult)))
        self.verboseCard.setChecked(bool(qconfig.get(CFG.verboseLog)))
        self.rememberCard.setChecked(bool(qconfig.get(CFG.rememberParams)))
        self.ffmpegCard.setText(qconfig.get(CFG.ffmpegPath) or "")
        self.refresh_environment()

    def _set(self, item, value) -> None:
        try:
            qconfig.set(item, value)
            save_config()
        except Exception:
            pass

    def _on_theme_changed(self, index: int) -> None:
        mode = list(THEME_MODES.keys())[index]
        self._set(CFG.uiThemeMode, mode)
        apply_appearance()

    def _on_color_changed(self, index: int) -> None:
        self._set(CFG.uiThemeColor, list(THEME_COLORS.keys())[index])
        apply_appearance()

    def _on_ffmpeg_changed(self, text: str) -> None:
        self._set(CFG.ffmpegPath, text.strip())
        self.refresh_environment()

    # ------------------------------------------------------------------ 环境
    def refresh_environment(self) -> None:
        lines = []
        ok = True
        if core.SCENEDETECT_AVAILABLE:
            lines.append(f"PySceneDetect：已安装（v{core.SCENEDETECT_VERSION}）")
        else:
            ok = False
            lines.append("PySceneDetect：未安装！" + core.SCENEDETECT_IMPORT_ERROR)
        ffmpeg = core.resolve_ffmpeg(self.ffmpegCard.text().strip())
        if ffmpeg:
            lines.append(f"ffmpeg：{ffmpeg}")
        else:
            ok = False
            lines.append("ffmpeg：未找到（分割功能不可用）")
        lines.append(f"Python：{sys.version.split()[0]}（{sys.executable}）")
        lines.append(f"OpenCV：{self._opencv_version()}")
        lines.append("状态：" + ("全部就绪，可以开始处理视频。" if ok else
                                 "缺少组件，请点击下方「一键安装 / 更新依赖」。"))
        self.depCard.setText("\n".join(lines))

    @staticmethod
    def _opencv_version() -> str:
        try:
            import cv2
            return cv2.__version__
        except Exception:
            return "未安装"

    def _install_dependencies(self) -> None:
        self.pipLog.setVisible(True)
        self.pipLog.clear()
        self.pipLog.appendPlainText(
            f"即将执行：{sys.executable} -m pip install -U scenedetect imageio-ffmpeg\n"
            "（会自动下载依赖，请保持网络连接）\n")
        self.installBtn.setEnabled(False)
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read_process_output)
        self._process.finished.connect(self._on_install_finished)
        self._process.errorOccurred.connect(
            lambda err: self.pipLog.appendPlainText(f"\n进程错误：{err}"))
        self._process.start(sys.executable, ["-m", "pip", "install", "-U",
                                             "scenedetect", "imageio-ffmpeg"])

    def _read_process_output(self) -> None:
        if self._process is None:
            return
        data = bytes(self._process.readAllStandardOutput()).decode("utf-8", "ignore")
        if data:
            self.pipLog.appendPlainText(data.rstrip("\n"))

    def _on_install_finished(self, code: int, _status) -> None:
        self.installBtn.setEnabled(True)
        self.pipLog.appendPlainText(f"\n安装结束（返回码 {code}）。"
                                    + ("已安装成功，重启程序后生效。" if code == 0 else "安装失败，请检查网络或手动安装。"))
        self.window.notify("依赖安装", "安装完成，建议重启程序。" if code == 0 else "安装失败，请查看输出信息。",
                           "ok" if code == 0 else "error")

    # ------------------------------------------------------------------ 重置
    def _reset_params(self) -> None:
        box = MessageBox("重置参数", "将「视频分割」页的检测 / 分段 / 输出参数恢复为默认值\n"
                         "（视频文件与输出目录不受影响），是否继续？", self.window)
        box.yesButton.setText("重置")
        box.cancelButton.setText("取消")
        if box.exec():
            self.window.split_page.reset_params()
            self.window.notify("已重置", "参数已恢复默认值。")

    def _reset_all(self) -> None:
        box = MessageBox("恢复出厂设置", "将清除所有设置（外观、参数记忆、ffmpeg 路径），是否继续？",
                         self.window)
        box.yesButton.setText("恢复")
        box.cancelButton.setText("取消")
        if box.exec():
            reset_config()
            self._load_values()
            self.window.split_page.reset_params()
            self.window.notify("已恢复", "已恢复出厂设置。")
