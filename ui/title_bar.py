# -*- coding: utf-8 -*-

"""主窗口标题栏控件，提供应用操作图标和运行状态标题。"""

from __future__ import annotations

from collections.abc import Callable
import math
import sys

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QMouseEvent, QPainter, QPalette, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QWidget
from ui.icons import line_icon


def native_caption_button_width(window: QWidget) -> int:
    """读取Windows原生窗口按钮总宽度，转换为Qt逻辑像素。

    Args:
        window: 已创建窗口句柄的主窗口。
    Returns:
        最小化、最大化和关闭按钮的总宽度；非Windows平台或查询失败时返回0。
    """
    from PySide6.QtGui import QGuiApplication

    if sys.platform != 'win32' or QGuiApplication.platformName() != 'windows' or window.windowHandle() is None:
        return 0
    import ctypes
    from ctypes import wintypes

    try:
        query = ctypes.WinDLL('dwmapi').DwmGetWindowAttribute
        query.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        query.restype = ctypes.c_long
        rectangle = wintypes.RECT()
        result = query(int(window.winId()), 5, ctypes.byref(rectangle), ctypes.sizeof(rectangle))
        if result < 0:
            return 0
        return max(0, math.ceil((rectangle.right-rectangle.left)/window.devicePixelRatioF()))
    except (OSError, AttributeError):
        return 0


def clip_native_title_overlay(window: QWidget) -> bool:
    """让Qt的Windows标题叠加层只显示右侧窗口控制按钮。

    Args:
        window: 已创建句柄的主窗口，仅处理它自己的_q_titlebar子窗口。
    Returns:
        成功设置显示区域时为True，非Windows或尚无叠加层时为False。
    Raises:
        TypeError: window不是QWidget。
    """
    from PySide6.QtGui import QGuiApplication

    if not isinstance(window, QWidget):
        raise TypeError('window必须为QWidget')
    if sys.platform != 'win32' or QGuiApplication.platformName() != 'windows' or window.windowHandle() is None:
        return False
    import ctypes
    from ctypes import wintypes

    try:
        user = ctypes.WinDLL('user32', use_last_error=True)
        gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        user.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
        user.FindWindowExW.restype = wintypes.HWND
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.GetWindowRect.restype = wintypes.BOOL
        user.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        user.SetWindowRgn.restype = ctypes.c_int
        gdi.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        gdi.CreateRectRgn.restype = wintypes.HRGN
        gdi.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi.DeleteObject.restype = wintypes.BOOL
        overlay = user.FindWindowExW(int(window.winId()), None, '_q_titlebar', None)
        if not overlay:
            return False
        rectangle = wintypes.RECT()
        if not user.GetWindowRect(overlay, ctypes.byref(rectangle)):
            return False
        width, height = rectangle.right-rectangle.left, rectangle.bottom-rectangle.top
        if width <= 0 or height <= 0:
            return False
        buttons = math.ceil((max(native_caption_button_width(window), 144)+8)*window.devicePixelRatioF())
        region = gdi.CreateRectRgn(max(0, width-buttons), 0, width, height)
        if not region:
            return False
        # 成功后区域归Windows所有；只有失败时才由应用释放GDI对象。
        if not user.SetWindowRgn(overlay, region, True):
            gdi.DeleteObject(region)
            return False
        return True
    except (OSError, AttributeError):
        return False


class ElidingCaption(QLabel):
    """空间不足时省略标题末尾，为短状态和系统按钮保留位置。"""

    def minimumSizeHint(self) -> QSize:
        """允许标题横向缩短，保持文字行高。

        Returns:
            宽度可缩小到0的最小尺寸。
        """
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event: QPaintEvent) -> None:
        """按实际可用宽度绘制省略后的标题。

        Args:
            event: Qt绘制事件。
        """
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width())
        painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignVCenter, text)
        painter.end()


class MainTitleBar(QWidget):
    """显示设置、暂停/继续、重置、赞赏图标和窗口标题。

    Attributes:
        settings_button: 打开设置对话框的无文字图标按钮。
        pause_button: 切换暂停和继续的图标按钮。
        reset_button: 重置记牌器的无文字图标按钮。
        donation_button: 直达既有赞赏页的无文字图标按钮。
        caption_label: 显示程序状态与检测耗时的标题。
        status_label: 耗时后的布局、故障或诊断短状态，悬停显示详情。
        retry_button: 识别故障时显示的顶部重启入口。
    """

    def __init__(
        self,
        parent: QWidget,
        on_settings_clicked: Callable[[], None],
        on_pause_clicked: Callable[[], None],
        on_reset_clicked: Callable[[], None],
        on_restart_clicked: Callable[[], None] | None = None,
        on_donation_clicked: Callable[[], None] | None = None,
    ) -> None:
        """创建标题栏控件并连接应用操作。

        Args:
            parent: 主窗口中央部件。
            on_settings_clicked: 打开设置页的回调。
            on_pause_clicked: 暂停或恢复检测的回调。
            on_reset_clicked: 重置记牌器的回调。
            on_restart_clicked: 可选的识别服务重启回调，省略时不启用入口。
            on_donation_clicked: 可选的赞赏页回调，省略时禁用按钮。
        """
        super().__init__(parent)
        self.setObjectName("MainTitleBar")
        self.setFixedHeight(32)
        # 这个控件刻意铺在原生标题栏安全区内，不能再被Qt自动向下缩进。
        self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        self.setAttribute(Qt.WidgetAttribute.WA_LayoutOnEntireRect, True)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 2, 144, 2)
        self._layout.setSpacing(2)

        self._paused = False
        self.settings_button = self._create_button("打开设置", on_settings_clicked)
        self.settings_button.setAccessibleName("设置")
        self._layout.addWidget(self.settings_button)

        self.pause_button = self._create_button("暂停检测", on_pause_clicked)
        self._layout.addWidget(self.pause_button)

        self.reset_button = self._create_button("重置记牌器", on_reset_clicked)
        self.reset_button.setAccessibleName("重置记牌器")
        self._layout.addWidget(self.reset_button)

        self.donation_button = self._create_button('赞赏', on_donation_clicked)
        self._layout.addWidget(self.donation_button)

        self.caption_label = ElidingCaption(self)
        self.caption_label.setObjectName("MainTitleBarCaption")
        self.caption_label.setMinimumWidth(0)
        self.caption_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._layout.addWidget(self.caption_label)
        self.status_label = QLabel(self)
        self.status_label.setObjectName('MainTitleBarStatus')
        self.status_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.status_label.hide()
        self._layout.addWidget(self.status_label)
        self.retry_button = QPushButton('重启', self)
        self.retry_button.setObjectName('MainTitleBarRetry')
        self.retry_button.setFixedHeight(24)
        self.retry_button.setToolTip('重新加载推理服务，并从头初始化记牌状态')
        self.retry_button.setAccessibleName('重启识别')
        self.retry_button.setEnabled(on_restart_clicked is not None)
        if on_restart_clicked is not None:
            self.retry_button.clicked.connect(on_restart_clicked)
        self.retry_button.hide()
        self._layout.addWidget(self.retry_button)
        self._layout.addStretch(1)

        self.set_paused(False)

    def _create_button(self, tooltip: str, callback: Callable[[], None] | None) -> QToolButton:
        """创建尺寸统一且仅显示图标的标题栏操作按钮。

        Args:
            tooltip: 鼠标悬停时显示的操作说明。
            callback: 按钮触发的操作；None时禁用入口。

        Returns:
            QToolButton: 按回调是否存在启用的图标按钮。
        """
        button = QToolButton(self)
        button.setObjectName("MainTitleBarAction")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIconSize(QSize(20, 20))
        button.setFixedSize(30, 28)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setEnabled(callback is not None)
        if callback is not None:
            button.clicked.connect(callback)
        return button

    def set_caption(self, caption: str) -> None:
        """更新标题栏中的窗口状态和耗时文本。

        Args:
            caption: 要显示的完整标题。
        """
        self.caption_label.setText(caption)
        self.caption_label.setToolTip(caption)

    def set_paused(self, paused: bool) -> None:
        """根据检测状态切换暂停与继续图标。

        Args:
            paused: True表示检测已暂停，应显示继续图标；False表示检测运行中。
        """
        self._paused = paused
        self._refresh_icons()
        tooltip = '继续检测' if paused else '暂停检测'
        self.pause_button.setToolTip(tooltip)
        self.pause_button.setAccessibleName(tooltip)

    def _refresh_icons(self) -> None:
        """使用同一前景色和细线样式绘制操作图标。"""
        color = self.palette().color(QPalette.ColorRole.WindowText)
        color.setAlphaF(.85)
        self.settings_button.setIcon(line_icon('settings', color))
        self.pause_button.setIcon(line_icon('play' if self._paused else 'pause', color))
        self.reset_button.setIcon(line_icon('reset', color))
        self.donation_button.setIcon(line_icon('donate', color))

    def changeEvent(self, event: QEvent) -> None:
        """明暗主题或样式变化后同步图标颜色。

        Args:
            event: Qt主题变化事件。
        """
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange, QEvent.Type.StyleChange):
            if hasattr(self, 'donation_button'):
                self._refresh_icons()

    def set_layout_status(self, text: str, detail: str = '') -> None:
        """在耗时后显示短状态，悬停时可读完整说明。

        Args:
            text: 简短布局状态；空字符串隐藏。
            detail: 完整的后台状态信息。
        """
        self.status_label.setText('· '+text if text else '')
        self.status_label.setToolTip(detail)
        self.status_label.setAccessibleDescription(detail)
        self.status_label.setVisible(bool(text))

    def set_right_safe_area_inset(self, width: int) -> None:
        """为 Windows 原生标题栏控制按钮预留右侧空间。

        Args:
            width: 右侧保留宽度，单位为 Qt 逻辑像素。

        Raises:
            TypeError: width不是整数。
            ValueError: width小于0。
        """
        if not isinstance(width, int):
            raise TypeError("width必须是整数")
        if width < 0:
            raise ValueError("width不能小于0")
        margins = self._layout.contentsMargins()
        self._layout.setContentsMargins(margins.left(), margins.top(), width, margins.bottom())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """在标题栏空白区域启动系统窗口拖动。

        Args:
            event: 标题栏鼠标按下事件。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击标题栏空白区域切换最大化状态。

        Args:
            event: 标题栏鼠标双击事件。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if window.isMaximized():
                window.showNormal()
            else:
                window.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
