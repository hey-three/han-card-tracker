# -*- coding: utf-8 -*-

"""
设置对话框模块
提供应用程序的设置界面，包括基本设置和高级设置
"""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListView,
    QMessageBox,
    QPushButton,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt, QRect, Signal, QModelIndex, QPersistentModelIndex
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
import config.settings as settings


class LayoutItemDelegate(QStyledItemDelegate):
    """在布局下拉项右侧绘制“删除”按钮。"""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        """绘制布局名称和删除按钮。

        Args:
            painter: Qt 绘图器。
            option: 当前条目的样式及边界。
            index: 当前条目的模型索引。
        """
        # 先绘制文本区域，给右侧按钮留出空间
        text_option = QStyleOptionViewItem(option)
        text_option.rect = option.rect.adjusted(0, 0, -56, 0)
        super().paint(painter, text_option, index)

        btn_rect = self.get_delete_button_rect(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#9e9e9e"), 1))
        painter.setBrush(QColor("#f3f3f3"))
        painter.drawRoundedRect(btn_rect, 4, 4)
        painter.setPen(QColor("#333333"))
        painter.drawText(btn_rect, Qt.AlignmentFlag.AlignCenter, "删除")
        painter.restore()

    @staticmethod
    def get_delete_button_rect(item_rect: QRect) -> QRect:
        width = 44
        height = 22
        x = item_rect.right() - width - 8
        y = item_rect.center().y() - height // 2
        return QRect(x, y, width, height)


class LayoutComboView(QListView):
    """自定义下拉视图：点击“删除”按钮区域时触发布局删除。"""

    def __init__(self, combo, delete_handler, parent=None):
        super().__init__(parent)
        self._combo = combo
        self._delete_handler = delete_handler
        self._delegate = LayoutItemDelegate(self)
        self.setItemDelegate(self._delegate)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """点击删除区域时执行删除回调，其余点击交给下拉列表处理。"""
        index = self.indexAt(event.position().toPoint())
        if index.isValid():
            item_rect = self.visualRect(index)
            btn_rect = self._delegate.get_delete_button_rect(item_rect)
            if btn_rect.contains(event.position().toPoint()):
                layout_name = index.data(Qt.ItemDataRole.DisplayRole)
                self._delete_handler(layout_name)
                return
        super().mousePressEvent(event)


class SettingsDialog(QDialog):
    """
    设置对话框
    提供基本设置和高级设置两个标签页，用于配置应用程序的各种参数
    """

    LABEL_MIN_WIDTH = 80
    PAGE_MARGIN = 20
    PAGE_SPACING = 20
    auto_layout_requested = Signal()
    layout_status_changed = Signal(int)
    packed_inference_changed = Signal(int)

    def __init__(
        self,
        parent=None,
        on_reset_callback=None,
        on_interval_change_callback=None,
        on_layout_change_callback=None,
        on_device_change_callback=None,
        on_reset_time_change_callback=None,
        on_frame_length_change_callback=None,
        on_always_on_top_change_callback=None,
        on_show_played_cards_change_callback=None,
        on_debug_mode_change_callback=None,
        on_save_debug_images_change_callback=None,
        on_show_current_screenshot_change_callback=None,
        on_show_timing_change_callback=None,
        on_layout_editor_callback=None,
        on_layout_delete_callback=None,
        on_model_change_callback=None,
        on_confidence_change_callback=None,
        on_game_change_callback=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumSize(680, 400)

        self.on_reset_callback = on_reset_callback
        self.on_interval_change_callback = on_interval_change_callback
        self.on_layout_change_callback = on_layout_change_callback
        self.on_device_change_callback = on_device_change_callback
        self.on_reset_time_change_callback = on_reset_time_change_callback
        self.on_frame_length_change_callback = on_frame_length_change_callback
        self.on_always_on_top_change_callback = on_always_on_top_change_callback
        self.on_show_played_cards_change_callback = on_show_played_cards_change_callback
        self.on_debug_mode_change_callback = on_debug_mode_change_callback
        self.on_save_debug_images_change_callback = on_save_debug_images_change_callback
        self.on_show_current_screenshot_change_callback = on_show_current_screenshot_change_callback
        self.on_show_timing_change_callback = on_show_timing_change_callback
        self.on_layout_editor_callback = on_layout_editor_callback
        self.on_layout_delete_callback = on_layout_delete_callback
        self.on_model_change_callback = on_model_change_callback
        self.on_confidence_change_callback = on_confidence_change_callback
        self.on_game_change_callback = on_game_change_callback

        # 创建标签页控件
        self.tab_widget = QTabWidget(self)

        # 创建标签页
        self.tab1 = QWidget()
        self.tab2 = QWidget()
        self.tab3 = QWidget()

        # 添加标签页
        self.tab_widget.addTab(self.tab1, "基本设置")
        self.tab_widget.addTab(self.tab2, "高级设置")
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

        row.addWidget(combo)
        row.addStretch()
        parent_layout.addLayout(row)
        return combo

    def _setup_basic_settings(self) -> None:
        """在基本设置标签页中添加控件。"""
        basic_layout = self._new_page_layout(self.tab1)

        reset_layout = QHBoxLayout()
        self.btn_reset = QPushButton("重置记牌器")
        self.btn_reset.setObjectName("BtnReset")
        self.btn_reset.clicked.connect(self._on_reset_clicked)
        reset_layout.addWidget(self.btn_reset)
        reset_layout.addStretch()
        basic_layout.addLayout(reset_layout)

        # 游戏选择
        from config.settings import _scan_game_configs, GAME_NAME
        game_names = _scan_game_configs()
        self.combo_game = self._add_combo_row(
            basic_layout,
            "游戏选择：",
            "GameCombo",
            game_names,
            self._on_game_changed,
            tooltip="选择要支持的卡牌游戏类型",
        )

        self.combo_layout = QComboBox()
        self.combo_layout.setObjectName("LayoutCombo")
        self.combo_layout.setMinimumWidth(300)
        self.combo_layout.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_layout.setMinimumContentsLength(18)
        self.combo_layout.setView(LayoutComboView(self.combo_layout, self._on_layout_delete_requested, self))
        from config.settings import WINDOW_LAYOUTS

        layout_names = list(WINDOW_LAYOUTS.keys())
        self._set_combo_items(self.combo_layout, layout_names, default_index=0)
        self.combo_layout.currentIndexChanged.connect(self._on_layout_changed)

        layout_row = QHBoxLayout()
        layout_label = QLabel("布局配置管理：")
        layout_label.setMinimumWidth(self.LABEL_MIN_WIDTH)
        layout_row.addWidget(layout_label)
        layout_row.addWidget(self.combo_layout, 1)
        self.btn_refresh_layout = QPushButton("\u21bb")
        self.btn_refresh_layout.setFixedSize(28, 28)
        self.btn_refresh_layout.setToolTip("刷新布局列表")
        from PySide6.QtGui import QFont
        font = self.btn_refresh_layout.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        self.btn_refresh_layout.setFont(font)
        self.btn_refresh_layout.clicked.connect(lambda: self.refresh_layout_list(selected_name=self.combo_layout.currentText()))
        layout_row.addWidget(self.btn_refresh_layout)
        layout_row.addSpacing(8)

        self.btn_layout_editor = QPushButton("可视化编辑")
        self.btn_layout_editor.setObjectName("BtnLayoutEditor")
        self.btn_layout_editor.clicked.connect(self._on_layout_editor_clicked)
        layout_row.addWidget(self.btn_layout_editor)
        basic_layout.addLayout(layout_row)

        auto_row = QHBoxLayout()
        self.btn_auto_layout = QPushButton("自动布局…")
        self.btn_auto_layout.setToolTip("选择游戏窗口即可；请在开局前启用，自动识别手牌、底牌和出牌区")
        self.btn_auto_layout.clicked.connect(self.auto_layout_requested.emit)
        auto_row.addWidget(self.btn_auto_layout)
        auto_row.addWidget(QLabel("选择窗口，自动定位牌区；支持单张、长牌组和多行出牌"))
        auto_row.addStretch()
        basic_layout.addLayout(auto_row)

        self.combo_device = self._add_combo_row(
            basic_layout,
            "设备选择：",
            "DeviceCombo",
            ["CPU", "GPU"],
            self._on_device_changed,
        )
        self.combo_always_on_top = self._add_combo_row(
            basic_layout,
            "显示在最上层：",
            "AlwaysOnTopCombo",
            ["否", "是"],
            self._on_always_on_top_changed,
        )
        self.combo_show_played_cards = self._add_combo_row(
            basic_layout,
            "显示出牌：",
            "ShowPlayedCardsCombo",
            ["否", "是"],
            self._on_show_played_cards_changed,
        )
        self.combo_show_layout_status = self._add_combo_row(
            basic_layout,
            "显示布局状态提示：",
            "ShowLayoutStatusCombo",
            ["否", "是"],
            self.layout_status_changed.emit,
            tooltip="显示底部的自动定位和等待画面提示；关闭后仍显示故障和恢复入口",
        )

        basic_layout.addStretch()

    def _setup_advanced_settings(self) -> None:
        """在高级设置标签页中添加控件。"""
        advanced_layout = self._new_page_layout(self.tab2)

        self.combo_debug_mode = self._add_combo_row(
            advanced_layout,
            "输出调试日志：",
            "DebugModeCombo",
            ["否", "是"],
            self._on_debug_mode_changed,
            tooltip="在终端输出每帧各牌区的识别结果，不负责保存截图",
        )

        # 模型选择
        from config.settings import _scan_model_dirs, YOLO_MODEL_NAME
        model_dirs = _scan_model_dirs()
        self.combo_model = self._add_combo_row(
            advanced_layout,
            "YOLO 模型：",
            "ModelCombo",
            model_dirs,
            self._on_model_changed,
            tooltip="选择 YOLO 推理模型，将 yolo/weights/ 下的子文件夹放入 best.pt 即可识别",
        )

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
            "重置时间：",
            "ResetTimeCombo",
            ["1.0秒", "1.5秒", "2.0秒", "2.5秒", "3.0秒", "3.5秒", "4.0秒", "4.5秒", "5.0秒"],
            self._on_reset_time_changed,
            tooltip="多久没有检测到地主底牌，就自动重置计牌器",
        )

        self.combo_frame_length = self._add_combo_row(
            advanced_layout,
            "帧长度：",
            "FrameLengthCombo",
            ["1", "2", "3", "4", "5", "6"],
            self._on_frame_length_changed,
            tooltip="连续多少帧检测相同内容才确认，避免误检",
        )

        self.combo_save_debug_images = self._add_combo_row(
            advanced_layout,
            "保存游戏截图：",
            "SaveDebugImagesCombo",
            ["否", "是"],
            self._on_save_debug_images_changed,
            tooltip="开启后会保存每帧的截图和 YOLO 标注图到 games_images 目录，用于排查识别问题。只保留最近 3 局游戏图片",
        )

        self.combo_show_current_screenshot = self._add_combo_row(
            advanced_layout,
            "实时显示截图：",
            "ShowCurrentScreenshotCombo",
            ["否", "是"],
            self._on_show_current_screenshot_changed,
            tooltip="开启后在独立窗口实时显示当前捕获的游戏画面",
        )

        self.combo_show_timing = self._add_combo_row(
            advanced_layout,
            "显示耗时：",
            "ShowTimingCombo",
            ["否", "是"],
            self._on_show_timing_changed,
            tooltip="在标题栏显示 YOLO 推理耗时和整轮流程耗时",
        )

        self.combo_packed_inference = self._add_combo_row(
            advanced_layout,
            "识别路线：",
            "PackedInferenceCombo",
            ["整图（默认）", "保护拼图（试验）"],
            self._on_packed_inference_changed,
            tooltip="自动布局校准后尝试重叠拼图；手牌继续检测。切边、冲突、区外变化或定期复查时回到整图。静态模型不保证降低推理输入尺寸。",
        )
        self.set_current_packed_inference(settings.PACKED_INFERENCE)
        advanced_layout.addStretch()

    def _on_packed_inference_changed(self, index: int) -> None:
        """通知主窗口应用识别路线。

        Args:
            index: 整图/试验拼图索引。
        """
        self.packed_inference_changed.emit(index)

    def set_current_packed_inference(self, enabled: bool) -> None:
        """无副作用地同步已保存路线。

        Args:
            enabled: 是否开启保护拼图。
        """
        self._set_combo_current_safely(self.combo_packed_inference, int(enabled))

    def _on_reset_clicked(self):
        """
        重置按钮点击事件
        """
        if self.on_reset_callback:
            self.on_reset_callback()

    def _on_interval_changed(self, index):
        """
        检测间隔改变事件

        参数:
            index: 选择的索引
        """
        if self.on_interval_change_callback:
            self.on_interval_change_callback(index)

    def _on_layout_changed(self, index):
        """
        布局配置改变事件

        参数:
            index: 选择的索引
        """
        if self.on_layout_change_callback:
            self.on_layout_change_callback(index)

    def _on_game_changed(self, index):
        """游戏选择改变事件。"""
        if self.on_game_change_callback:
            self.on_game_change_callback(index)

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

    def _on_save_debug_images_changed(self, index):
        """
        保存游戏图片改变事件
        """
        if self.on_save_debug_images_change_callback:
            self.on_save_debug_images_change_callback(index)

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

    def _on_layout_editor_clicked(self):
        """打开可视化布局编辑器。"""
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

    def refresh_layout_list(self, selected_name=None):
        """刷新布局下拉列表并可选指定当前项。"""
        import config.settings as settings

        names = list(settings.WINDOW_LAYOUTS.keys())
        self._set_combo_items(self.combo_layout, names, default_index=0)
        target_name = selected_name or settings.CURRENT_LAYOUT
        if target_name:
            idx = self.combo_layout.findText(target_name)
            if idx >= 0:
                self._set_combo_current_safely(self.combo_layout, idx)
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

    def set_current_layout(self, layout_name):
        """
        设置当前布局配置

        参数:
            layout_name: 布局配置名称
        """
        index = self.combo_layout.findText(layout_name)
        self._set_combo_current_safely(self.combo_layout, index)

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

    def set_current_game(self, game_name: str):
        """设置当前游戏选择。

        Args:
            game_name: 游戏名称（如 ``"doudizhu"``）。
        """
        index = self.combo_game.findText(game_name)
        self._set_combo_current_safely(self.combo_game, index)

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

    def set_current_always_on_top(self, always_on_top):
        """
        设置当前是否显示在最上层

        参数:
            always_on_top: 是否显示在最上层（True/False）
        """
        always_on_top_text = "是" if always_on_top else "否"
        index = self.combo_always_on_top.findText(always_on_top_text)
        self._set_combo_current_safely(self.combo_always_on_top, index)

    def set_current_show_played_cards(self, show_played_cards):
        """
        设置当前是否显示玩家所出的牌

        参数:
            show_played_cards: 是否显示玩家所出的牌（True/False）
        """
        show_played_cards_text = "是" if show_played_cards else "否"
        index = self.combo_show_played_cards.findText(show_played_cards_text)
        self._set_combo_current_safely(self.combo_show_played_cards, index)

    def set_current_debug_mode(self, debug_mode):
        """
        设置当前调试模式

        参数:
            debug_mode: 是否开启调试模式（True/False）
        """
        debug_mode_text = "是" if debug_mode else "否"
        index = self.combo_debug_mode.findText(debug_mode_text)
        self._set_combo_current_safely(self.combo_debug_mode, index)

    def set_current_save_debug_images(self, save_debug_images):
        """
        设置当前保存游戏图片

        参数:
            save_debug_images: 是否保存游戏图片（True/False）
        """
        text = "是" if save_debug_images else "否"
        index = self.combo_save_debug_images.findText(text)
        self._set_combo_current_safely(self.combo_save_debug_images, index)

    def set_current_show_timing(self, show_timing):
        """
        设置当前显示耗时

        参数:
            show_timing: 是否显示耗时（True/False）
        """
        text = "是" if show_timing else "否"
        index = self.combo_show_timing.findText(text)
        self._set_combo_current_safely(self.combo_show_timing, index)

    def set_current_show_current_screenshot(self, show_current_screenshot: bool) -> None:
        """设置当前实时截图预览选项。

        Args:
            show_current_screenshot: 是否打开实时截图预览。
        """
        text = "是" if show_current_screenshot else "否"
        index = self.combo_show_current_screenshot.findText(text)
        self._set_combo_current_safely(self.combo_show_current_screenshot, index)

    def set_current_show_layout_status(self, enabled: bool) -> None:
        """刷新布局提示开关，不触发设置写回。

        Args:
            enabled: 是否显示正常布局状态。
        """
        self._set_combo_current_safely(self.combo_show_layout_status, int(enabled))

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
