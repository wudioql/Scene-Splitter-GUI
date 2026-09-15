# -*- coding: utf-8 -*-
"""通用 Fluent 风格控件：给设置卡片加上数值输入框、下拉框、路径选择等。"""

from __future__ import annotations

import os
from typing import Iterable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    DoubleSpinBox,
    FluentIcon,
    LineEdit,
    SettingCard,
    SimpleCardWidget,
    SmoothScrollArea,
    SpinBox,
    StrongBodyLabel,
    SwitchButton,
    ToolButton,
)


class SpinCard(SettingCard):
    """带整数输入框的设置卡片。"""

    valueChanged = Signal(int)

    def __init__(self, icon, title: str, content: str = "", value: int = 0,
                 minimum: int = 0, maximum: int = 100000, step: int = 1,
                 suffix: str = "", width: int = 150, parent=None):
        super().__init__(icon, title, content, parent)
        self.spin = SpinBox(self)
        self.spin.setRange(minimum, maximum)
        self.spin.setSingleStep(step)
        self.spin.setValue(value)
        self.spin.setFixedWidth(width)
        if suffix:
            self.spin.setSuffix(f" {suffix}")
        self.spin.valueChanged.connect(self.valueChanged)
        self.hBoxLayout.addWidget(self.spin, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def value(self) -> int:
        return self.spin.value()

    def setValue(self, value: int) -> None:
        self.spin.setValue(int(value))


class DoubleSpinCard(SettingCard):
    """带浮点数输入框的设置卡片。"""

    valueChanged = Signal(float)

    def __init__(self, icon, title: str, content: str = "", value: float = 0.0,
                 minimum: float = 0.0, maximum: float = 100.0, step: float = 1.0,
                 decimals: int = 2, suffix: str = "", width: int = 150, parent=None):
        super().__init__(icon, title, content, parent)
        self.spin = DoubleSpinBox(self)
        self.spin.setRange(minimum, maximum)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(decimals)
        self.spin.setValue(value)
        self.spin.setFixedWidth(width)
        if suffix:
            self.spin.setSuffix(f" {suffix}")
        self.spin.valueChanged.connect(self.valueChanged)
        self.hBoxLayout.addWidget(self.spin, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def value(self) -> float:
        return self.spin.value()

    def setValue(self, value: float) -> None:
        self.spin.setValue(float(value))


class ChoiceCard(SettingCard):
    """带下拉框的设置卡片。"""

    currentIndexChanged = Signal(int)

    def __init__(self, icon, title: str, content: str = "",
                 items: Iterable[str] = (), index: int = 0, width: int = 220, parent=None):
        super().__init__(icon, title, content, parent)
        self.combo = ComboBox(self)
        self.combo.addItems(list(items))
        self.combo.setCurrentIndex(index)
        self.combo.setFixedWidth(width)
        self.combo.currentIndexChanged.connect(self.currentIndexChanged)
        self.hBoxLayout.addWidget(self.combo, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def currentIndex(self) -> int:
        return self.combo.currentIndex()

    def setCurrentIndex(self, index: int) -> None:
        self.combo.setCurrentIndex(int(index))


class LineEditCard(SettingCard):
    """带单行文本框的设置卡片（可选浏览按钮）。"""

    textChanged = Signal(str)

    def __init__(self, icon, title: str, content: str = "", text: str = "",
                 placeholder: str = "", browse: Optional[str] = None,
                 file_filter: str = "", width: int = 320, parent=None):
        super().__init__(icon, title, content, parent)
        self._browse = browse
        self._filter = file_filter
        self.edit = LineEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setText(text)
        self.edit.setClearButtonEnabled(True)
        self.edit.setFixedWidth(width)
        self.edit.textChanged.connect(self.textChanged)
        self.hBoxLayout.addWidget(self.edit, 0, Qt.AlignRight)
        if browse:
            self.button = ToolButton(FluentIcon.FOLDER if browse == "dir" else FluentIcon.DOCUMENT, self)
            self.button.setToolTip("浏览…")
            self.button.clicked.connect(self._on_browse)
            self.hBoxLayout.addWidget(self.button, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def _on_browse(self) -> None:
        start = self.edit.text().strip() or str(os.path.expanduser("~"))
        if self._browse == "dir":
            path = QFileDialog.getExistingDirectory(self, "选择文件夹", start)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", os.path.dirname(start) or start,
                                                  self._filter or "所有文件 (*.*)")
        if path:
            self.edit.setText(path)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:
        self.edit.setText(text or "")

    def addExtraWidget(self, widget: QWidget) -> None:
        """在文本框右侧追加一个控件（例如「使用源目录」按钮）。"""
        index = max(0, self.hBoxLayout.count() - 1)
        self.hBoxLayout.insertWidget(index, widget, 0, Qt.AlignRight)
        self.hBoxLayout.insertSpacing(index, 8)


class SwitchCard(SettingCard):
    """带开关按钮的设置卡片。"""

    checkedChanged = Signal(bool)

    def __init__(self, icon, title: str, content: str = "", checked: bool = False,
                 on_text: str = "开", off_text: str = "关", parent=None):
        super().__init__(icon, title, content, parent)
        self.switch = SwitchButton(self)
        self.switch.setOnText(on_text)
        self.switch.setOffText(off_text)
        self.switch.setChecked(checked)
        self.switch.checkedChanged.connect(self.checkedChanged)
        self.hBoxLayout.addWidget(self.switch, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def isChecked(self) -> bool:
        return self.switch.isChecked()

    def setChecked(self, value: bool) -> None:
        self.switch.setChecked(bool(value))


class InfoCard(SimpleCardWidget):
    """信息条卡片：用于展示视频信息 / 提示文本。

    注意：SettingCardGroup 使用 ExpandLayout 排列卡片，只会按照子控件的“当前高度”
    摆放，所以文本变更后必须主动调整自身高度，分组才会跟着长高（否则文字被裁切）。
    """

    MARGIN = (16, 12, 16, 12)

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.label = BodyLabel(text, self)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        layout = QVBoxLayout(self)
        left, top, right, bottom = self.MARGIN
        layout.setContentsMargins(left, top, right, bottom)
        layout.addWidget(self.label)

    # —— 自适应高度（QLabel.heightForWidth 在部分平台上不可靠，这里用字体度量直接算） ——
    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def _text_height(self, width: int) -> int:
        text = self.label.text()
        if not text:
            return max(16, self.label.fontMetrics().height())
        metrics = self.label.fontMetrics()
        rect = metrics.boundingRect(0, 0, max(60, width), 0,
                                    int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop), text)
        return max(16, rect.height())

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        _l, top, _r, bottom = self.MARGIN
        return self._text_height(width - self.MARGIN[0] - self.MARGIN[2]) + top + bottom

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._fit_height()

    def _fit_height(self) -> None:
        width = max(self.width(), 1)
        inner = max(60, width - self.MARGIN[0] - self.MARGIN[2])
        text_height = self._text_height(inner)
        if self.label.height() != text_height:
            self.label.setFixedHeight(text_height)
        _l, top, _r, bottom = self.MARGIN
        needed = text_height + top + bottom
        if self.height() != needed:
            # ExpandLayout 是按子控件当前 height() 排布的，所以这里把高度定死
            self.setFixedHeight(needed)
        if needed != getattr(self, "_last_needed", None):
            self._last_needed = needed
            self.updateGeometry()
            layout = self.layout()
            if layout is not None:
                layout.activate()
            self._sync_group_height()   # 文本变短时卡片需要回收多余高度

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

    def setText(self, text: str) -> None:
        self.label.setText(text or "")
        self.updateGeometry()
        self._fit_height()


class SectionCard(CardWidget):
    """带标题的自定义卡片容器（可随意放置控件）。"""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.titleLabel = StrongBodyLabel(title, self)
        self.subLabel = CaptionLabel(subtitle, self) if subtitle else None
        self.bodyLayout = QVBoxLayout()
        self.bodyLayout.setContentsMargins(0, 0, 0, 0)
        self.bodyLayout.setSpacing(8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        layout.addWidget(self.titleLabel)
        if self.subLabel:
            self.subLabel.setTextColor("#606060", "#a0a0a0")
            layout.addWidget(self.subLabel)
        layout.addLayout(self.bodyLayout)

    def addWidget(self, widget: QWidget) -> None:
        self.bodyLayout.addWidget(widget)

    def addLayout(self, layout) -> None:
        self.bodyLayout.addLayout(layout)


def make_row(*widgets: QWidget, spacing: int = 8) -> QWidget:
    """把若干控件横向排成一行。"""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    return container


def make_scroll_page(parent: QWidget | None = None):
    """创建「可滚动页面」容器，返回 (滚动区, 内容容器)。

    注意两点（否则深色主题下页面会变成系统浅色底）：
      1. `enableTransparentBackground()` 必须在 `setWidget()` 之后调用，
         它内部会给内容控件设置透明样式；
      2. QScrollArea 的 viewport 默认会填充系统调色板底色，需要关闭 autoFillBackground。
    """
    scroll = SmoothScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    view = QWidget()
    view.setObjectName("scrollView")
    scroll.setWidget(view)
    if hasattr(scroll, "enableTransparentBackground"):
        scroll.enableTransparentBackground()
    else:  # 兼容旧版本
        scroll.setStyleSheet("QScrollArea{border: none; background: transparent}")
        view.setStyleSheet("QWidget{background: transparent}")
    scroll.viewport().setAutoFillBackground(False)
    return scroll, view
