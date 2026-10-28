# -*- coding: utf-8 -*-

"""提供设置页使用的紧凑型开关控件。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPaintEvent, QPainter, QPalette, QPen
from PySide6.QtWidgets import QCheckBox, QWidget


class SwitchControl(QCheckBox):
    """以滑块外观显示布尔值，并用整数信号兼容现有设置回调。

    Attributes:
        value_changed: 状态变化信号，关闭发送0，开启发送1。
    """

    value_changed = Signal(int)

    def __init__(self, accessible_name: str, parent: QWidget | None = None) -> None:
        """创建无文字的开关控件。

        Args:
            accessible_name: 屏幕阅读器使用的开关名称。
            parent: 可选的Qt父控件。

        Raises:
            TypeError: 名称不是字符串，或父控件不是QWidget。
            ValueError: 名称为空白字符串。
        """
        if not isinstance(accessible_name, str):
            raise TypeError("accessible_name必须是字符串")
        if not accessible_name.strip():
            raise ValueError("accessible_name不能为空")
        if parent is not None and not isinstance(parent, QWidget):
            raise TypeError("parent必须是QWidget或None")
        super().__init__(parent)
        self._accessible_name = accessible_name.strip()
        self.setText("")
        self.setAccessibleName(self._accessible_name)
        self.setAccessibleDescription("关闭")
        self.setToolTip(f"切换{self._accessible_name}")
        self.setFixedSize(48, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(self._publish_value)

    def sizeHint(self) -> QSize:
        """返回适合紧凑设置行的固定开关尺寸。

        Returns:
            QSize: 48×28逻辑像素。
        """
        return QSize(48, 28)

    def hitButton(self, position: QPoint) -> bool:
        """让整个开关矩形都能响应点击。

        Args:
            position: 鼠标在控件本地坐标中的位置。

        Returns:
            bool: 点击点位于控件内时为True。
        """
        return self.rect().contains(position)

    def paintEvent(self, event: QPaintEvent) -> None:
        """绘制圆角轨道、滑块和键盘焦点提示。

        Args:
            event: Qt绘制事件；控件按当前开关状态完整重绘。
        """
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        track = QRectF(1.0, 3.0, 46.0, 22.0)
        palette = self.palette()
        track_color = QColor("#1687E8" if self.isChecked() else "#62666B")
        if not self.isEnabled():
            track_color.setAlpha(125)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, 11.0, 11.0)

        knob_size = 16.0
        knob_x = track.right() - knob_size - 3.0 if self.isChecked() else track.left() + 3.0
        knob = QRectF(knob_x, track.top() + 3.0, knob_size, knob_size)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(knob)

        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(palette.color(QPalette.ColorRole.Highlight), 1.0))
            painter.drawRoundedRect(track.adjusted(-1.0, -1.0, 1.0, 1.0), 12.0, 12.0)
        painter.end()

    def set_checked_silently(self, enabled: bool) -> None:
        """刷新已保存值但不触发设置写回。

        Args:
            enabled: True表示开启，False表示关闭。

        Raises:
            TypeError: enabled不是布尔值。
        """
        if not isinstance(enabled, bool):
            raise TypeError("enabled必须是布尔值")
        previous_state = self.blockSignals(True)
        try:
            self.setChecked(enabled)
        finally:
            self.blockSignals(previous_state)
        self.setAccessibleDescription("开启" if enabled else "关闭")

    def _publish_value(self, enabled: bool) -> None:
        """把用户点击转换为兼容旧设置回调的0/1值。

        Args:
            enabled: 控件切换后的布尔状态。
        """
        self.setAccessibleDescription("开启" if enabled else "关闭")
        self.value_changed.emit(int(enabled))
