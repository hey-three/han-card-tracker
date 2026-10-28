# -*- coding: utf-8 -*-

"""
设置对话框模块
提供基本设置、高级设置、关于和赞赏页面
"""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QToolTip,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QEvent, QObject, QPoint, QSize, QTimer, Qt, QRect, Signal, QModelIndex, QPersistentModelIndex
from PySide6.QtGui import QColor, QHelpEvent, QKeyEvent, QMouseEvent, QPainter, QPalette, QPen, QPixmap, QResizeEvent, QShowEvent

import config.settings as settings
from ui.switch_control import SwitchControl
from ui.icons import line_icon


class LayoutItemDelegate(QStyledItemDelegate):
    """绘制布局名称及右侧同风格的编辑、删除图标。"""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        """给名称保留省略空间，图标颜色跟随列表选中状态。

        Args:
            painter: Qt绘图器。
            option: 条目样式。
            index: 条目索引。
        """
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        background = QPalette.ColorRole.Highlight if selected else QPalette.ColorRole.Base
        painter.fillRect(option.rect, option.palette.brush(background))
        text_option = QStyleOptionViewItem(option)
        text_option.rect = option.rect.adjusted(0, 0, -76, 0)
        text_option.textElideMode = Qt.TextElideMode.ElideRight
        super().paint(painter, text_option, index)
        if index.data(Qt.ItemDataRole.DisplayRole) not in settings.WINDOW_LAYOUTS:
            return
        color = option.palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        for kind, rect in (('edit', self.get_edit_button_rect(option.rect)),
                           ('delete', self.get_delete_button_rect(option.rect))):
            line_icon(kind, color).paint(painter, rect.adjusted(4, 4, -4, -4))

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        """为两个图标保留足够点击高度。

        Args:
            option: 条目样式。
            index: 条目索引。
        Returns:
            至少34逻辑像素高的条目尺寸。
        """
        size = super().sizeHint(option, index)
        size.setHeight(max(34, size.height()))
        return size

    @staticmethod
    def get_delete_button_rect(item_rect: QRect) -> QRect:
        """计算条目右侧垃圾桶的点击区域。

        Args:
            item_rect: 条目矩形。
        Returns:
            28像素方形区域。
        """
        return QRect(item_rect.right()-36, item_rect.center().y()-14, 28, 28)

    @staticmethod
    def get_edit_button_rect(item_rect: QRect) -> QRect:
        """计算垃圾桶左侧的编辑点击区域。

        Args:
            item_rect: 条目矩形。
        Returns:
            28像素方形区域。
        """
        return LayoutItemDelegate.get_delete_button_rect(item_rect).translated(-30, 0)


class LayoutComboView(QListView):
    """布局下拉列表：图标悬停提示、点击动作与普通选项互不混淆。

    Attributes:
        _pressed_action: 鼠标按下的动作和名称，松开时核对，拖出则取消。
        _pending_action: 收起列表后等待执行的动作。
    """

    def __init__(self, combo: QComboBox, delete_handler: Callable[[str], None], parent: QWidget | None = None,
                 *, edit_handler: Callable[[str], None] | None = None) -> None:
        """绑定动作，使用子定时器避免在弹出列表事件中直接打开模态框。

        Args:
            combo: 所属下拉控件。
            delete_handler: 删除回调。
            parent: 父控件。
            edit_handler: 编辑指定布局的回调。
        """
        super().__init__(parent)
        self._combo = combo
        self._delete_handler = delete_handler
        self._edit_handler = edit_handler
        self._delegate = LayoutItemDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._pressed_action: tuple[str, str] | None = None
        self._pending_action: tuple[str, str] | None = None
        self._action_timer = QTimer(self)
        self._action_timer.setSingleShot(True)
        self._action_timer.timeout.connect(self._run_action)

    def showEvent(self, event: QShowEvent) -> None:
        """在Qt弹出容器安装过滤器之后接管图标点击，防止松开事件被抢先消费。

        Args:
            event: 下拉视图显示事件。
        """
        super().showEvent(event)
        self._pressed_action = None
        self.viewport().installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """优先处理图标按下及松开，普通行点击仍交给Qt选择逻辑。

        Args:
            watched: 事件接收对象，只有视口上的图标点击被接管。
            event: Qt事件；拖离原图标松开仅取消动作，不切换布局。
        Returns:
            已消费图标事件为True，其余保持父类处理结果。
        """
        if watched is self.viewport() and isinstance(event, QMouseEvent):
            if (event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton
                    and self._action_at(event.position().toPoint()) is not None):
                self.mousePressEvent(event)
                return True
            if (event.type() == QEvent.Type.MouseButtonRelease
                    and event.button() == Qt.MouseButton.LeftButton
                    and self._pressed_action is not None):
                self.mouseReleaseEvent(event)
                return True
        return super().eventFilter(watched, event)

    def _action_at(self, point: QPoint) -> tuple[str, str] | None:
        """查找指针命中的动作，不把空列表占位项当作布局。

        Args:
            point: 视口坐标。
        Returns:
            动作和布局名，未命中为None。
        """
        index = self.indexAt(point)
        name = index.data(Qt.ItemDataRole.DisplayRole)
        if not index.isValid() or name not in settings.WINDOW_LAYOUTS:
            return None
        rect = self.visualRect(index)
        if self._delegate.get_edit_button_rect(rect).contains(point) and self._edit_handler is not None:
            return 'edit', str(name)
        if self._delegate.get_delete_button_rect(rect).contains(point):
            return 'delete', str(name)
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """图标按下时暂存动作，避免误触布局切换。

        Args:
            event: 鼠标事件。
        """
        self._pressed_action = self._action_at(event.position().toPoint()) if event.button() == Qt.MouseButton.LeftButton else None
        if self._pressed_action is not None:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """在同一图标松开才执行，支持拖离取消。

        Args:
            event: 鼠标事件。
        """
        action, self._pressed_action = self._pressed_action, None
        if action is not None:
            if action == self._action_at(event.position().toPoint()):
                self._pending_action = action
                self._combo.hidePopup()
                self._action_timer.start(0)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def viewportEvent(self, event: QEvent) -> bool:
        """在图标上分别显示编辑、删除提示。

        Args:
            event: 视口事件。
        Returns:
            是否处理了该事件。
        """
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            action = self._action_at(event.pos())
            if action:
                QToolTip.showText(event.globalPos(), '编辑' if action[0] == 'edit' else '删除', self)
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """提供F2编辑和Delete删除的键盘入口。

        Args:
            event: 键盘事件。
        """
        name = self.currentIndex().data(Qt.ItemDataRole.DisplayRole)
        if name in settings.WINDOW_LAYOUTS and event.key() in (Qt.Key.Key_F2, Qt.Key.Key_Delete):
            self._pending_action = ('edit' if event.key() == Qt.Key.Key_F2 else 'delete', str(name))
            self._combo.hidePopup()
            self._action_timer.start(0)
            event.accept()
            return
        super().keyPressEvent(event)

    def _run_action(self) -> None:
        """收起列表后运行唯一一次动作。"""
        action, self._pending_action = self._pending_action, None
        if action:
            QToolTip.hideText()
            handler = self._edit_handler if action[0] == 'edit' else self._delete_handler
            if handler is not None:
                handler(action[1])


class ResponsiveImageLabel(QLabel):
    """在可用区域内等比例缩放并居中显示图片。

    Attributes:
        _source_pixmap: 保留原始分辨率、供每次尺寸变化时重新缩放的图片。
    """

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        """创建自适应图片标签。

        Args:
            pixmap: 原始图片；允许为空以供调用方显示加载错误。
            parent: 可选的Qt父控件。

        Raises:
            TypeError: pixmap不是QPixmap，或parent不是QWidget或None。
        """
        if not isinstance(pixmap, QPixmap):
            raise TypeError("pixmap必须是QPixmap")
        if parent is not None and not isinstance(parent, QWidget):
            raise TypeError("parent必须是QWidget或None")
        super().__init__(parent)
        self._source_pixmap = QPixmap(pixmap)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._refresh_pixmap()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """控件尺寸变化时等比例刷新图片尺寸。

        Args:
            event: Qt传入的控件尺寸变化事件。
        """
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        """将原始图片按当前标签可用尺寸等比例缩放。"""
        if self._source_pixmap.isNull():
            return
        target_size = self.contentsRect().size()
        if target_size.width() <= 0 or target_size.height() <= 0:
            return
        self.setPixmap(
            self._source_pixmap.scaled(
                target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class SettingsDialog(QDialog):
    """
    设置对话框
    提供基本设置、高级设置、关于和赞赏四个标签页，用于配置和介绍应用程序
    """

    LABEL_MIN_WIDTH = 80
    PAGE_MARGIN = 20
    PAGE_SPACING = 20
    layout_edit_requested = Signal(str)
    auto_layout_requested = Signal()
    layout_status_changed = Signal(int)
    panel_arrangement_changed = Signal(int)

    manual_inference_changed = Signal(int)
    diagnostics_changed = Signal(int)
    diagnostic_disk_changed = Signal(float)

    def __init__(
        self,
        parent: QWidget | None = None,
        on_interval_change_callback: Callable[[int], None] | None = None,
        on_layout_change_callback: Callable[[str], None] | None = None,
        on_device_change_callback: Callable[[int], None] | None = None,
        on_reset_time_change_callback: Callable[[int], None] | None = None,
        on_frame_length_change_callback: Callable[[int], None] | None = None,
        on_always_on_top_change_callback: Callable[[int], None] | None = None,
        on_show_played_cards_change_callback: Callable[[int], None] | None = None,
        on_debug_mode_change_callback: Callable[[int], None] | None = None,
        on_show_current_screenshot_change_callback: Callable[[int], None] | None = None,
        on_show_timing_change_callback: Callable[[int], None] | None = None,
        on_layout_editor_callback: Callable[[SettingsDialog], None] | None = None,
        on_layout_delete_callback: Callable[[SettingsDialog, str], bool] | None = None,
        on_model_change_callback: Callable[[int], None] | None = None,
        on_confidence_change_callback: Callable[[int], None] | None = None,
    ) -> None:
        """创建设置页及可选操作回调。

        Args:
            parent: 父窗口。
            on_interval_change_callback: 对应设置操作的回调；None不连接外部处理。
            on_layout_change_callback: 接收手动布局名称的回调；None不连接外部处理。
            on_device_change_callback: 对应设置操作的回调；None不连接外部处理。
            on_reset_time_change_callback: 对应设置操作的回调；None不连接外部处理。
            on_frame_length_change_callback: 对应设置操作的回调；None不连接外部处理。
            on_always_on_top_change_callback: 对应设置操作的回调；None不连接外部处理。
            on_show_played_cards_change_callback: 对应设置操作的回调；None不连接外部处理。
            on_debug_mode_change_callback: 对应设置操作的回调；None不连接外部处理。
            on_show_current_screenshot_change_callback: 对应设置操作的回调；None不连接外部处理。
            on_show_timing_change_callback: 对应设置操作的回调；None不连接外部处理。
            on_layout_editor_callback: 对应设置操作的回调；None不连接外部处理。
            on_layout_delete_callback: 对应设置操作的回调；None不连接外部处理。
            on_model_change_callback: 对应设置操作的回调；None不连接外部处理。
            on_confidence_change_callback: 对应设置操作的回调；None不连接外部处理。
        """
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumSize(680, 400)

        self.on_interval_change_callback = on_interval_change_callback
        self.on_layout_change_callback = on_layout_change_callback
        self.on_device_change_callback = on_device_change_callback
        self.on_reset_time_change_callback = on_reset_time_change_callback
        self.on_frame_length_change_callback = on_frame_length_change_callback
        self.on_always_on_top_change_callback = on_always_on_top_change_callback
        self.on_show_played_cards_change_callback = on_show_played_cards_change_callback
        self.on_debug_mode_change_callback = on_debug_mode_change_callback
        self.on_show_current_screenshot_change_callback = on_show_current_screenshot_change_callback
        self.on_show_timing_change_callback = on_show_timing_change_callback
        self.on_layout_editor_callback = on_layout_editor_callback
        self.on_layout_delete_callback = on_layout_delete_callback
        self.on_model_change_callback = on_model_change_callback
        self.on_confidence_change_callback = on_confidence_change_callback

        # 创建标签页控件
        self.tab_widget = QTabWidget(self)

        # 创建标签页
        self.tab1 = QWidget()
        self.tab2 = QWidget()
        self.tab3 = QWidget()
        self.tab4 = QWidget()

        # 添加标签页
        self.tab_widget.addTab(self.tab1, "基本设置")
        self.tab_widget.addTab(self.tab2, "高级设置")
        self.tab_widget.addTab(self.tab4, "赞赏")
        self.tab_widget.addTab(self.tab3, "关于")

        # 创建整体布局
        layout = QVBoxLayout(self)
        layout.addWidget(self.tab_widget)

        # 在基本设置标签页中添加控件
        self._setup_basic_settings()

        # 在高级设置标签页中添加控件
        self._setup_advanced_settings()

        # 在关于标签页中添加控件
        self._setup_about_settings()

        # 在赞赏标签页中显示项目二维码
        self._setup_donation_settings()

    @staticmethod
    def _set_combo_current_safely(combo: QComboBox, index: int) -> None:
        if index < 0:
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    @staticmethod
    def _set_combo_items(combo: QComboBox, items, default_index: int = 0) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        if items and 0 <= default_index < len(items):
            combo.setCurrentIndex(default_index)
        combo.blockSignals(False)

    def _new_page_layout(self, page: QWidget) -> QVBoxLayout:
        layout = QVBoxLayout(page)
        layout.setContentsMargins(self.PAGE_MARGIN, self.PAGE_MARGIN, self.PAGE_MARGIN, self.PAGE_MARGIN)
        layout.setSpacing(self.PAGE_SPACING)
        return layout

    def _add_combo_row(self, parent_layout: QVBoxLayout, label_text: str, combo_name: str, items, callback, tooltip: str = ""):
        row = QHBoxLayout()
        label = QLabel(label_text)
        label.setMinimumWidth(self.LABEL_MIN_WIDTH)

        combo = QComboBox()
        combo.setObjectName(combo_name)
        combo.addItems(items)
        combo.currentIndexChanged.connect(callback)

        row.addWidget(label)

        if tooltip:
            help_label = QLabel("?")
            help_label.setFixedSize(16, 16)
            help_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            help_label.setStyleSheet(
                "QLabel { color: #999; font-size: 12px; font-weight: bold; "
                "border: 1px solid #bbb; border-radius: 8px; }"
                "QLabel:hover { color: #333; border-color: #666; }"
            )
            help_label.setToolTip(tooltip)
            row.addWidget(help_label)

        row.addStretch(1)
        row.addWidget(combo, 0, Qt.AlignmentFlag.AlignRight)
        parent_layout.addLayout(row)
        return combo

    def _add_switch_row(
        self,
        parent_layout: QVBoxLayout,
        label_text: str,
        switch_name: str,
        callback: Callable[[int], None],
        tooltip: str = "",
    ) -> SwitchControl:
        """在设置页添加左侧说明、右侧滑动开关。

        Args:
            parent_layout: 接收设置行的垂直布局。
            label_text: 设置名称及显示文字。
            switch_name: 开关的稳定Qt对象名称。
            callback: 状态变化时接收0/1值的回调。
            tooltip: 可选的说明文字。

        Returns:
            SwitchControl: 已接入回调的开关控件。
        """
        row = QHBoxLayout()
        label = QLabel(label_text)
        label.setMinimumWidth(self.LABEL_MIN_WIDTH)
        row.addWidget(label)

        if tooltip:
            help_label = QLabel("?")
            help_label.setFixedSize(16, 16)
            help_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            help_label.setStyleSheet(
                "QLabel { color: #999; font-size: 12px; font-weight: bold; "
                "border: 1px solid #bbb; border-radius: 8px; }"
                "QLabel:hover { color: #333; border-color: #666; }"
            )
            help_label.setToolTip(tooltip)
            row.addWidget(help_label)

        switch = SwitchControl(label_text.rstrip("：:"), self)
        switch.setObjectName(switch_name)
        switch.value_changed.connect(callback)
        row.addStretch(1)
        row.addWidget(switch)
        parent_layout.addLayout(row)
        return switch

    def _build_layout_mode_settings(self, parent_layout: QVBoxLayout) -> None:
        """建立自动/手动模式选择及对应的缩进配置区。

        Args:
            parent_layout: 接收布局模式控件的基本设置布局。
        """
        mode_row = QHBoxLayout()
        mode_label = QLabel("布局方式：")
        mode_label.setMinimumWidth(self.LABEL_MIN_WIDTH)
        mode_row.addWidget(mode_label)

        self.btn_layout_auto = QPushButton("自动布局")
        self.btn_layout_manual = QPushButton("手动框选")
        for button in (self.btn_layout_auto, self.btn_layout_manual):
            button.setObjectName("LayoutModeButton")
            button.setCheckable(True)

        self.layout_mode_group = QButtonGroup(self)
        self.layout_mode_group.setExclusive(True)
        self.layout_mode_group.addButton(self.btn_layout_auto, 0)
        self.layout_mode_group.addButton(self.btn_layout_manual, 1)
        self.layout_mode_group.idClicked.connect(self._on_layout_mode_changed)
        mode_row.addWidget(self.btn_layout_auto)
        mode_row.addWidget(self.btn_layout_manual)
        mode_row.addStretch(1)
        parent_layout.addLayout(mode_row)

        self.layout_mode_details = QFrame()
        self.layout_mode_details.setObjectName("LayoutModeDetails")
        self.layout_mode_details.setFrameShape(QFrame.Shape.NoFrame)
        details_layout = QVBoxLayout(self.layout_mode_details)
        details_layout.setContentsMargins(18, 5, 0, 5)

        self.layout_settings_group = QGroupBox()
        self.layout_settings_group.setObjectName("LayoutSettingsGroup")
        group_layout = QVBoxLayout(self.layout_settings_group)
        group_layout.setContentsMargins(10, 14, 10, 8)

        self.layout_mode_stack = QStackedWidget(self.layout_settings_group)
        self.auto_layout_panel = QWidget(self.layout_mode_stack)
        auto_panel_layout = QHBoxLayout(self.auto_layout_panel)
        auto_panel_layout.setContentsMargins(0, 0, 0, 0)
        auto_panel_layout.addWidget(QLabel("目标游戏窗口："))
        self.label_auto_window = QLabel("尚未选择目标窗口")
        self.label_auto_window.setObjectName("AutoLayoutWindowTitle")
        auto_panel_layout.addWidget(self.label_auto_window, 1)
        self.btn_auto_layout = QPushButton("选择窗口…")
        self.btn_auto_layout.setObjectName("AutoLayoutWindowButton")
        self.btn_auto_layout.setToolTip("选择要自动定位牌区的游戏窗口")
        self.btn_auto_layout.clicked.connect(self.auto_layout_requested.emit)
        auto_panel_layout.addWidget(self.btn_auto_layout)
        self.layout_mode_stack.addWidget(self.auto_layout_panel)

        self.manual_layout_panel = QWidget(self.layout_mode_stack)
        manual_panel_layout = QVBoxLayout(self.manual_layout_panel)
        manual_panel_layout.setContentsMargins(0, 0, 0, 0)
        manual_panel_layout.setSpacing(12)

        self.combo_layout = QComboBox()
        self.combo_layout.setObjectName("LayoutCombo")
        self.combo_layout.setMinimumWidth(300)
        self.combo_layout.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_layout.setMinimumContentsLength(18)
        self.combo_layout.setView(LayoutComboView(self.combo_layout, self._on_layout_delete_requested, self,
                                                   edit_handler=self.layout_edit_requested.emit))
        self.combo_layout.activated.connect(self._on_layout_changed)

        layout_row = QHBoxLayout()
        layout_label = QLabel("选择布局：")
        layout_label.setMinimumWidth(self.LABEL_MIN_WIDTH)
        layout_row.addWidget(layout_label)
        layout_row.addWidget(self.combo_layout, 1)
        self.btn_layout_editor = QPushButton("新建布局")
        self.btn_layout_editor.setObjectName("BtnLayoutEditor")
        self.btn_layout_editor.clicked.connect(self._on_layout_editor_clicked)
        layout_row.addWidget(self.btn_layout_editor)
        manual_panel_layout.addLayout(layout_row)

        self.combo_manual_inference = self._add_combo_row(
            manual_panel_layout,
            "检测方式：",
            "ManualInferenceCombo",
            ["拼图检测", "原图检测"],
            self.manual_inference_changed.emit,
            tooltip="仅对手动框选布局生效；自动布局始终使用整图。",
        )
        self.set_current_manual_inference(settings.PACKED_INFERENCE)
        self.layout_mode_stack.addWidget(self.manual_layout_panel)
        group_layout.addWidget(self.layout_mode_stack)
        details_layout.addWidget(self.layout_settings_group)
        parent_layout.addWidget(self.layout_mode_details)

        self.refresh_layout_list()
        current_layout = settings.WINDOW_LAYOUTS.get(settings.CURRENT_LAYOUT or "", {})
        current_mode = "auto" if current_layout.get("mode") == "auto" else "manual"
        self.set_current_layout_mode(current_mode, str(current_layout.get("window_title", "")))

    def _setup_basic_settings(self) -> None:
        """在基本设置标签页中添加控件。"""
        basic_layout = self._new_page_layout(self.tab1)
        self._build_layout_mode_settings(basic_layout)

        self.combo_panel_arrangement = self._add_combo_row(
            basic_layout, "界面排列：", "PanelArrangementCombo",
            ["上下排列", "左右排列（长条）"], self.panel_arrangement_changed.emit,
            tooltip="仅调整剩余牌数与出牌记录的位置，保留当前牌局；与游戏检测布局独立",
        )

        from config.settings import _scan_model_dirs
        self.combo_model = self._add_combo_row(
            basic_layout,
            "YOLO 模型：",
            "ModelCombo",
            _scan_model_dirs(),
            self._on_model_changed,
            tooltip="选择 YOLO 推理模型，将 yolo/weights/ 下的子文件夹放入 best.pt 即可识别",
        )

        self.combo_device = self._add_combo_row(
            basic_layout,
            "设备选择：",
            "DeviceCombo",
            ["CPU", "GPU"],
            self._on_device_changed,
        )

        self.switch_always_on_top = self._add_switch_row(
            basic_layout,
            "显示在最上层：",
            "AlwaysOnTopSwitch",
            self._on_always_on_top_changed,
        )
        self.switch_show_played_cards = self._add_switch_row(
            basic_layout,
            "显示出牌：",
            "ShowPlayedCardsSwitch",
            self._on_show_played_cards_changed,
        )
        self.switch_show_layout_status = self._add_switch_row(
            basic_layout,
            "显示布局状态提示：",
            "ShowLayoutStatusSwitch",
            self.layout_status_changed.emit,
            tooltip="在标题栏耗时后显示简短布局提示，悬停查看详情；关闭后仍保留故障和恢复入口",
        )
        self.switch_show_timing = self._add_switch_row(
            basic_layout,
            "显示耗时：",
            "ShowTimingSwitch",
            self._on_show_timing_changed,
            tooltip="在标题栏显示 YOLO 推理耗时和整轮流程耗时",
        )

        basic_layout.addStretch()

    def set_current_panel_arrangement(self, arrangement: str) -> None:
        """回显已保存的界面排列，避免刷新触发重复保存。

        Args:
            arrangement: vertical为上下排列，horizontal为左右排列。
        """
        self._set_combo_current_safely(self.combo_panel_arrangement, int(arrangement == 'horizontal'))

    def _setup_advanced_settings(self) -> None:
        """在高级设置标签页中添加控件。"""
        advanced_layout = self._new_page_layout(self.tab2)

        self.combo_confidence = self._add_combo_row(
            advanced_layout,
            "置信度阈值：",
            "ConfidenceCombo",
            ["0.3", "0.4", "0.5", "0.6", "0.7", "0.8"],
            self._on_confidence_changed,
            tooltip="YOLO 检测置信度阈值，越低识别越多但可能误检，越高越准确但可能漏检",
        )

        self.combo_interval = self._add_combo_row(
            advanced_layout,
            "检测间隔：",
            "IntervalCombo",
            ["0.1秒", "0.15秒", "0.2秒", "0.25秒", "0.3秒", "0.35秒", "0.4秒", "0.45秒", "0.5秒"],
            self._on_interval_changed,
            tooltip="每次检测屏幕的时间间隔，越小检测越快，但占用资源越多",
        )
        self._set_combo_current_safely(self.combo_interval, 1)

        self.combo_reset_time = self._add_combo_row(
            advanced_layout,
            "底牌消失结束时长：",
            "ResetTimeCombo",
            ["1.0秒", "1.5秒", "2.0秒", "2.5秒", "3.0秒", "3.5秒", "4.0秒", "4.5秒", "5.0秒"],
            self._on_reset_time_changed,
            tooltip="底牌区持续无牌达到此时长且至少 max(3, 帧长度) 帧才结束；底牌消失期间暂停记录，保留旧显示至下次底牌出现开局。截图失败和暂停不计入。",
        )

        self.combo_frame_length = self._add_combo_row(
            advanced_layout,
            "帧长度：",
            "FrameLengthCombo",
            ["1", "2", "3", "4", "5", "6"],
            self._on_frame_length_changed,
            tooltip="连续多少帧检测相同内容才确认，避免误检",
        )


        self.switch_show_current_screenshot = self._add_switch_row(
            advanced_layout,
            "实时显示 YOLO 输入：",
            "ShowCurrentScreenshotSwitch",
            self._on_show_current_screenshot_changed,
            tooltip="显示本轮实际传给 YOLO 的原图或拼图；补查回退时显示最后使用的原图，不叠加原图牌区框。",
        )

        self.switch_debug_mode = self._add_switch_row(
            advanced_layout,
            "输出调试日志：",
            "DebugModeSwitch",
            self._on_debug_mode_changed,
            tooltip="在终端输出每帧各牌区的识别结果，不负责保存截图",
        )

        self.switch_full_diagnostics = self._add_switch_row(
            advanced_layout, '完整诊断：', 'FullDiagnosticsSwitch', self.diagnostics_changed.emit,
            tooltip='后台保存原图、每次YOLO输入及标注、状态变化。队列/内存拥堵时暂缓采样，回落后自动恢复并记录缺帧；磁盘故障需处理后重开。识别继续。保存期间仍有图片复制等开销。',
        )
        disk_row = QHBoxLayout()
        disk_label = QLabel('诊断磁盘上限：')
        disk_label.setMinimumWidth(self.LABEL_MIN_WIDTH)
        disk_row.addWidget(disk_label)
        disk_row.addStretch(1)
        self.spin_diagnostic_disk = QDoubleSpinBox()
        self.spin_diagnostic_disk.setObjectName('DiagnosticDiskLimit')
        self.spin_diagnostic_disk.setFixedWidth(130)
        self.spin_diagnostic_disk.setRange(0.1, 1024.0)
        self.spin_diagnostic_disk.setDecimals(2)
        self.spin_diagnostic_disk.setSingleStep(0.5)
        self.spin_diagnostic_disk.setSuffix(' GiB')
        self.spin_diagnostic_disk.setKeyboardTracking(False)
        self.spin_diagnostic_disk.setToolTip('诊断文件总额，0.1～1024 GiB，1 GiB约1.07 GB；不会预先占用磁盘。下调时只清已结束旧局，当前局超额会暂停。')
        self.spin_diagnostic_disk.editingFinished.connect(self._on_diagnostic_disk_edited)
        disk_row.addWidget(self.spin_diagnostic_disk, 0, Qt.AlignmentFlag.AlignRight)
        advanced_layout.addLayout(disk_row)
        self.set_current_diagnostic_disk(float(settings.DIAGNOSTIC_DISK_GIB))

        self.label_diagnostics = QLabel('完整诊断未开启')
        self.label_diagnostics.setWordWrap(True)
        advanced_layout.addWidget(self.label_diagnostics)
        self.set_current_full_diagnostics(settings.FULL_DIAGNOSTICS)

        advanced_layout.addStretch()

    def _on_interval_changed(self, index):
        """
        检测间隔改变事件

        参数:
            index: 选择的索引
        """
        if self.on_interval_change_callback:
            self.on_interval_change_callback(index)

    def _on_layout_mode_changed(self, index: int) -> None:
        """仅切换二级配置区，不在选择布局前改变当前运行布局。

        Args:
            index: 0显示自动窗口选择，1显示手动布局设置。
        """
        if index not in (0, 1):
            return
        self._layout_mode = "auto" if index == 0 else "manual"
        self.layout_mode_stack.setCurrentIndex(index)

    def _on_layout_changed(self, index: int) -> None:
        """将手动布局下拉项转换为布局名称后交给主窗口处理。

        Args:
            index: 手动布局下拉列表索引。
        """
        if not self.combo_layout.isEnabled() or not 0 <= index < self.combo_layout.count():
            return
        if self.on_layout_change_callback:
            self.on_layout_change_callback(self.combo_layout.itemText(index))

    def _on_device_changed(self, index):
        """
        设备选择改变事件

        参数:
            index: 选择的索引
        """
        if self.on_device_change_callback:
            self.on_device_change_callback(index)

    def _on_reset_time_changed(self, index):
        """
        重置时间改变事件

        参数:
            index: 选择的索引
        """
        if self.on_reset_time_change_callback:
            self.on_reset_time_change_callback(index)

    def _on_frame_length_changed(self, index):
        """
        帧长度改变事件

        参数:
            index: 选择的索引
        """
        if self.on_frame_length_change_callback:
            self.on_frame_length_change_callback(index)

    def _on_always_on_top_changed(self, index):
        """
        是否显示在最上层改变事件

        参数:
            index: 选择的索引
        """
        if self.on_always_on_top_change_callback:
            self.on_always_on_top_change_callback(index)

    def _on_show_played_cards_changed(self, index):
        """
        是否显示玩家所出的牌改变事件

        参数:
            index: 选择的索引
        """
        if self.on_show_played_cards_change_callback:
            self.on_show_played_cards_change_callback(index)

    def _on_debug_mode_changed(self, index):
        """
        调试模式改变事件

        参数:
            index: 选择的索引
        """
        if self.on_debug_mode_change_callback:
            self.on_debug_mode_change_callback(index)


    def _on_show_timing_changed(self, index):
        """
        显示耗时改变事件
        """
        if self.on_show_timing_change_callback:
            self.on_show_timing_change_callback(index)

    def _on_show_current_screenshot_changed(self, index: int) -> None:
        """转发实时截图预览开关变化。"""
        if self.on_show_current_screenshot_change_callback:
            self.on_show_current_screenshot_change_callback(index)

    def _on_model_changed(self, index):
        """模型选择改变事件。"""
        if self.on_model_change_callback:
            self.on_model_change_callback(index)

    def _on_confidence_changed(self, index):
        """置信度阈值改变事件。"""
        if self.on_confidence_change_callback:
            self.on_confidence_change_callback(index)

    def _on_layout_editor_clicked(self) -> None:
        """打开空白编辑器创建新布局，已有布局通过各自的铅笔图标编辑。"""
        if self.on_layout_editor_callback:
            self.on_layout_editor_callback(self)

    def _on_layout_delete_requested(self, layout_name: str) -> None:
        """点击下拉项删除按钮时，弹确认框并触发布局删除回调。"""
        from config.settings import WINDOW_LAYOUTS

        if len(WINDOW_LAYOUTS) <= 1:
            QMessageBox.warning(self, "无法删除", "至少需要保留一个布局配置。")
            return

        confirm = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除布局“{layout_name}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if self.on_layout_delete_callback:
            deleted = self.on_layout_delete_callback(self, layout_name)
            if deleted is False:
                QMessageBox.warning(self, "删除失败", "布局删除失败，请检查配置文件后重试。")

    def refresh_layout_list(self, selected_name: str | None = None) -> None:
        """刷新手动布局列表，并保留可用的当前项。

        Args:
            selected_name: 优先显示的手动布局名称；自动布局名称会被忽略。
        """
        names = [
            name for name, config in settings.WINDOW_LAYOUTS.items()
            if config.get("mode", "manual") == "manual"
        ]
        display_names = names or ["暂无手动布局"]
        self._set_combo_items(self.combo_layout, display_names, default_index=0)
        self.combo_layout.setEnabled(bool(names))
        self.combo_layout.setToolTip(
            "选择要使用的手动布局。" if names else "暂无手动布局；可通过“可视化编辑”创建。"
        )
        if names:
            target_name = selected_name if selected_name in names else settings.CURRENT_LAYOUT
            target_name = target_name if target_name in names else names[0]
            self._set_combo_current_safely(self.combo_layout, self.combo_layout.findText(str(target_name)))
        self.combo_layout.update()
        self.combo_layout.repaint()

    def set_current_interval(self, interval_text):
        """
        设置当前检测间隔

        参数:
            interval_text: 检测间隔文本（如 "0.2秒"）
        """
        index = self.combo_interval.findText(interval_text)
        self._set_combo_current_safely(self.combo_interval, index)

    def set_current_layout(self, layout_name: str) -> None:
        """恢复当前手动布局选择，不触发切换回调。

        Args:
            layout_name: 已保存的手动布局名称。
        """
        index = self.combo_layout.findText(layout_name)
        self._set_combo_current_safely(self.combo_layout, index)

    def set_current_layout_mode(self, mode: str, window_title: str = "") -> None:
        """按当前活动布局恢复顶层模式和自动窗口标题。

        Args:
            mode: 当前活动布局模式，只允许auto或manual。
            window_title: 自动模式当前关联的游戏窗口标题。

        Raises:
            ValueError: mode不是auto或manual。
        """
        if mode not in ("auto", "manual"):
            raise ValueError("布局模式必须是auto或manual")
        index = 0 if mode == "auto" else 1
        button = self.layout_mode_group.button(index)
        if button is not None:
            button.setChecked(True)
        self._on_layout_mode_changed(index)
        self.label_auto_window.setText(window_title or "尚未选择目标窗口")

    def set_current_device(self, device_choice):
        """
        设置当前设备选择

        参数:
            device_choice: 设备选择（"cpu" 或 "cuda"）
        """
        device_map = {"cpu": "CPU", "cuda": "GPU"}
        device_text = device_map.get(device_choice, "CPU")
        index = self.combo_device.findText(device_text)
        self._set_combo_current_safely(self.combo_device, index)

    def set_current_reset_time(self, reset_time):
        """
        设置当前重置时间

        参数:
            reset_time: 重置时间（秒）
        """
        reset_time_text = f"{reset_time}秒"
        index = self.combo_reset_time.findText(reset_time_text)
        self._set_combo_current_safely(self.combo_reset_time, index)

    def set_current_frame_length(self, frame_length):
        """
        设置当前帧长度

        参数:
            frame_length: 帧长度
        """
        frame_length_text = str(frame_length)
        index = self.combo_frame_length.findText(frame_length_text)
        self._set_combo_current_safely(self.combo_frame_length, index)

    def set_current_always_on_top(self, always_on_top: bool) -> None:
        """恢复已保存的置顶状态，不触发设置回调。

        Args:
            always_on_top: 是否显示在最上层。
        """
        self.switch_always_on_top.set_checked_silently(always_on_top)

    def set_current_show_played_cards(self, show_played_cards: bool) -> None:
        """恢复已保存的出牌历史显示状态。

        Args:
            show_played_cards: 是否显示出牌历史。
        """
        self.switch_show_played_cards.set_checked_silently(show_played_cards)

    def set_current_debug_mode(self, debug_mode: bool) -> None:
        """恢复已保存的调试日志状态。

        Args:
            debug_mode: 是否输出调试日志。
        """
        self.switch_debug_mode.set_checked_silently(debug_mode)

    def set_current_show_timing(self, show_timing: bool) -> None:
        """恢复已保存的耗时显示状态。

        Args:
            show_timing: 是否在标题栏显示耗时。
        """
        self.switch_show_timing.set_checked_silently(show_timing)

    def set_current_show_current_screenshot(self, show_current_screenshot: bool) -> None:
        """设置当前实时截图预览选项。

        Args:
            show_current_screenshot: 是否打开实时截图预览。
        """
        self.switch_show_current_screenshot.set_checked_silently(show_current_screenshot)

    def _on_diagnostic_disk_edited(self) -> None:
        """只提交编辑完成后的磁盘额度，等后端确认后再持久化。"""
        value = self.spin_diagnostic_disk.value()
        if value != float(settings.DIAGNOSTIC_DISK_GIB):
            self.diagnostic_disk_changed.emit(value)

    def set_current_diagnostic_disk(self, value: float) -> None:
        """刷新已保存磁盘额度，不触发保存请求。

        Args:
            value: GiB额度。
        """
        self.spin_diagnostic_disk.blockSignals(True)
        self.spin_diagnostic_disk.setValue(value)
        self.spin_diagnostic_disk.blockSignals(False)

    def set_current_full_diagnostics(self, enabled: bool) -> None:
        """刷新已保存的诊断偏好，不将后台暂停误显示为已关闭。

        Args:
            enabled: 主进程已经确认保存的偏好。
        """
        self.switch_full_diagnostics.set_checked_silently(enabled)

    def set_diagnostic_status(self, status: dict[str, object]) -> None:
        """展示后台实际状态和完整故障原因。

        Args:
            status: 推理进程传回的纯数据快照。
        """
        names = {'off': '未开启', 'starting': '正在准备', 'recording': '正在记录', 'backpressure': '暂缓采样，队列回落后自动恢复', 'stopping': '正在收尾', 'paused': '已暂停，请关闭后重新开启'}
        text = '完整诊断：'+names.get(str(status.get('state')), '等待服务回执')
        text += f"；已保存{status.get('saved_frames', 0)}帧，待保存{status.get('pending_frames', 0)}帧"
        text += f"；主动跳过{status.get('dropped_frames', 0)}帧"
        pending_bytes = status.get('pending_bytes', 0)
        budget = status.get('memory_budget_bytes', 64*1024*1024)
        if isinstance(pending_bytes, int) and isinstance(budget, int):
            text += f'；待写快照 {pending_bytes/1024/1024:.1f} / {budget/1024/1024:.0f} MiB'
        disk = status.get('disk_budget_bytes', 1024**3)
        if isinstance(disk, int):
            text += f'；磁盘上限 {disk/1024**3:.1f} GiB'
        detail = str(status.get('reason') or status.get('daily_log_error') or '')
        self.label_diagnostics.setText(text + ('\n'+detail if detail else ''))
        self.label_diagnostics.setToolTip(str(settings.BASE_DIR)+'/diagnostics/；日常日志在 logs/；'+detail)

    def set_current_manual_inference(self, packed: bool) -> None:
        """恢复后端确认的手动检测方式，不触发新的保存请求。

        Args:
            packed: True使用拼图，False使用原图；不影响自动布局。
        """
        self._set_combo_current_safely(self.combo_manual_inference, 0 if packed else 1)

    def set_current_show_layout_status(self, enabled: bool) -> None:
        """刷新布局提示开关，不触发设置写回。

        Args:
            enabled: 是否显示正常布局状态。
        """
        self.switch_show_layout_status.set_checked_silently(enabled)

    def set_current_model(self, model_name: str):
        """设置当前 YOLO 模型选择。"""
        if not model_name:
            return
        index = self.combo_model.findText(model_name)
        self._set_combo_current_safely(self.combo_model, index)

    def set_current_confidence(self, confidence: float):
        """设置当前 YOLO 置信度阈值。"""
        text = str(confidence)
        index = self.combo_confidence.findText(text)
        if index < 0:
            # 找最接近的
            values = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
            closest = min(values, key=lambda v: abs(v - confidence))
            index = self.combo_confidence.findText(str(closest))
        self._set_combo_current_safely(self.combo_confidence, index)

    def refresh_model_list(self, selected_name=None):
        """刷新模型下拉列表。"""
        from config.settings import _scan_model_dirs

        model_dirs = _scan_model_dirs()
        self._set_combo_items(self.combo_model, model_dirs, default_index=0)
        target = selected_name or (model_dirs[0] if model_dirs else None)
        if target:
            idx = self.combo_model.findText(target)
            if idx >= 0:
                self._set_combo_current_safely(self.combo_model, idx)

    def _setup_about_settings(self):
        """
        在关于标签页中添加控件
        """
        # 创建关于页面的布局
        about_layout = QVBoxLayout(self.tab3)
        about_layout.setContentsMargins(20, 20, 20, 20)
        about_layout.setSpacing(15)

        # 添加标题
        title_label = QLabel("关于 Han 记牌器")
        title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        about_layout.addWidget(title_label)

        # 添加描述文字
        description_label = QLabel()
        description_label.setText("Han 记牌器是一款扑克牌斗地主的棋牌软件。基于 YOLO V11识别。通常不需要繁琐的配置。仅通过识别点数进行统计，不是作弊，也不是外挂，仅提供正常的娱乐使用。")
        description_label.setWordWrap(True)
        description_label.setStyleSheet("font-size: 12px;")
        about_layout.addWidget(description_label)

        # 添加弹性空间
        about_layout.addStretch()

    def _setup_donation_settings(self) -> None:
        """在赞赏标签页中自适应展示项目二维码，不添加说明文字。"""
        donation_layout = QVBoxLayout(self.tab4)
        donation_layout.setContentsMargins(0, 0, 0, 0)
        donation_layout.setSpacing(0)
        image_path = os.path.join(settings.RESOURCE_DIR, "images", "QR.png")
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            image_label = QLabel(self.tab4)
            image_label.setText(f"无法加载赞赏图片：{image_path}")
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            image_label = ResponsiveImageLabel(pixmap, self.tab4)
            image_label.setObjectName("DonationQRCode")
        donation_layout.addWidget(image_label, 1)
