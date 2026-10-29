# -*- coding: utf-8 -*-

"""
主窗口模块
提供斗地主记牌器的主界面，包括剩余牌统计、出牌显示等功能
"""

from collections.abc import Callable

from PySide6.QtCore import QEvent, QMargins, Qt, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QGridLayout, QMainWindow, QSizePolicy, QMessageBox,
    QBoxLayout, QFrame, QStyle,
)
from core.inference_process import InferenceWorker
from config.settings import TOTAL_CARDS
from utils.trans_yolo_names_to_string import trans_yolo_names_to_string
from ui.layout_editor_dialog import LayoutEditorDialog
from ui.auto_layout_dialog import AutoLayoutDialog
from ui.settings_dialog import SettingsDialog
from ui.settings_controller import SettingsController
from ui.title_bar import MainTitleBar, clip_native_title_overlay, native_caption_button_width
from ui.layout_status import compact_layout_status
from ui.screenshot_preview import ScreenshotPreviewDialog
from utils.layout_editor.service import list_visible_window_titles
import config.settings as settings
import time


INTERVAL_TEXT_OPTIONS = ["0.1秒", "0.15秒", "0.2秒", "0.25秒", "0.3秒", "0.35秒", "0.4秒", "0.45秒", "0.5秒"]
RESET_TIME_OPTIONS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
FRAME_LENGTH_OPTIONS = [1, 2, 3, 4, 5, 6]
COMPACT_WINDOW_WIDTH = 495  # 原默认550逻辑像素缩减10%。
HORIZONTAL_WINDOW_WIDTH = 900
HORIZONTAL_PANEL_MIN_WIDTH = 440
PLAYED_ZONE_TITLES = {"left": "上", "self": "本", "right": "下"}


class CardUI(QMainWindow):
    """
    两行记牌器主窗口：
    - 第 0 行：牌名
    - 第 1 行：剩余数量

    特点：
    - 使用 QGridLayout：每张牌占一列，结构最清晰；
    - 使用多进程 worker：将 YOLO 推理放到独立子进程，彻底绕过 GIL；
    - 定时器触发 worker 识别；
    - busy 防抖：上一轮未结束不触发下一轮。
    """

    def __init__(self) -> None:
        """创建窗口、设置协调器和可恢复的推理服务。"""
        super().__init__()
        # QMainWindow自身也会应用安全区；只关闭central_widget的避让仍会下移整行。
        self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)

        # -------------------------
        # 窗口基础设置（保持你原设置）
        # -------------------------
        self.setWindowTitle("Han记牌器")
        self.setWindowFlag(Qt.WindowType.ExpandedClientAreaHint, True)
        # 右侧Qt标题叠加层先绘制不透明底色，避免深色悬停的半透明白色合成成白块。
        # 左侧仍由clip_native_title_overlay裁掉，不会重新显示默认标题/图标。
        self.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, False)
        self.setMinimumWidth(COMPACT_WINDOW_WIDTH)
        self.setMinimumHeight(100) # 允许窗口缩小到最小高度
        self.resize(COMPACT_WINDOW_WIDTH, 100)

        # 应用是否显示在最上层设置
        from config.settings import ALWAYS_ON_TOP
        # 使用 setWindowFlag 以避免重建窗口导致子控件丢失
        try:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, ALWAYS_ON_TOP)
        except Exception:
            # 兜底：无论如何不要使窗口重建后丢失 central widget
            pass

        # 应用是否显示玩家所出的牌设置
        from config.settings import SHOW_PLAYED_CARDS
        self._show_played_cards = SHOW_PLAYED_CARDS
        self._panel_arrangement = settings.PANEL_ARRANGEMENT

        # 创建中央部件
        self.central_widget = QWidget()
        self.central_widget.setAttribute(
            Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False,
        )
        self.setCentralWidget(self.central_widget)

        # 设置窗口大小策略，允许窗口根据内容自动调整大小
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        # 自动选择字典中第一个可用的配置
        from config.settings import WINDOW_LAYOUTS, CURRENT_LAYOUT
        available_layouts = list(WINDOW_LAYOUTS.keys())
        if not available_layouts:
            raise ValueError("WINDOW_LAYOUTS 字典为空，没有可用的配置")

        # 优先使用配置文件中保存的 CURRENT_LAYOUT，如果不存在则使用第一个
        if CURRENT_LAYOUT and CURRENT_LAYOUT in available_layouts:
            self.layout_name = CURRENT_LAYOUT
        else:
            self.layout_name = available_layouts[0]
            # 如果配置中没有CURRENT_LAYOUT，可以将默认值写回内存（无需立刻写文件）
            settings.CURRENT_LAYOUT = self.layout_name

        self.detect_interval_sec = settings.DETECT_INTERVAL_SEC
        self.played_cards: dict[str, str] = {z["key"]: "" for z in settings.PLAYED_ZONES}

        # -------------------------
        # 牌序：按 TOTAL_CARDS 的 key 顺序 的逆序
        # （如果你想固定顺序，可以改 TOTAL_CARDS 或在这里写死 list）
        # -------------------------
        self.card_order = list(TOTAL_CARDS.keys())
        self.card_order.reverse()

        # -------------------------
        # UI 结构：根布局 + 网格布局（两行）
        # -------------------------
        self.root_layout = QVBoxLayout(self.central_widget) # 创建一个垂直布局（QVBoxLayout）
        # 标题栏横跨整个客户区；牌面内容自己保留原有左右边距。
        self.root_layout.setContentsMargins(0, 0, 0, 4)
        self.root_layout.setSpacing(0)

        # 此控件占用扩展后的原生标题栏区域，与系统窗口按钮处于同一行。
        self.is_paused = False
        self._safe_area_signal_connected = False
        self._initial_size_pending = True
        self.title_bar = MainTitleBar(
            self.central_widget,
            on_settings_clicked=self.on_settings_clicked,
            on_pause_clicked=self.on_pause_clicked,
            on_reset_clicked=self.on_reset_clicked,
            on_restart_clicked=self.on_restart_clicked,
            on_donation_clicked=self.on_donation_clicked,
        )
        self.title_bar.set_caption(self.windowTitle())
        self.btn_settings = self.title_bar.settings_button
        self.btn_pause = self.title_bar.pause_button
        self.btn_reset = self.title_bar.reset_button
        self.btn_donation = self.title_bar.donation_button
        self.btn_retry = self.title_bar.retry_button
        self.root_layout.addWidget(self.title_bar)
        self._title_bar_update_timer = QTimer(self)
        self._title_bar_update_timer.setSingleShot(True)
        self._title_bar_update_timer.timeout.connect(self._refresh_title_bar_geometry)

        self.grid = QGridLayout() # 创建一个网格布局对象（QGridLayout）
        self.grid.setHorizontalSpacing(4)
        self.grid.setVerticalSpacing(4) #行与行之间的垂直间距（单位：像素）。
        # root.addLayout(self.grid)

        # -------------------------
        # 第一大行：显示扑克牌和数量
        # -------------------------
        self.content_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.content_layout.setContentsMargins(4, 1, 4, 0)
        self.content_layout.setSpacing(5)
        self.root_layout.addLayout(self.content_layout)

        self.card_panel = QFrame(self.central_widget)
        self.card_panel.setObjectName("CardCountPanel")
        self.card_panel.setAccessibleName("剩余牌数")
        self.card_panel_layout = QVBoxLayout(self.card_panel)
        self.card_panel_layout.setContentsMargins(5, 4, 5, 4)
        self.card_panel_layout.addLayout(self.grid)
        self.content_layout.addWidget(self.card_panel)

        # -------------------------
        # 第二大行：显示上家、本家、下家的三行字符串
        # -------------------------
        self.played_panel = QFrame(self.central_widget)
        self.played_panel.setObjectName("PlayedCardsPanel")
        self.played_panel.setAccessibleName("出牌记录")
        self.second_row_layout = QVBoxLayout(self.played_panel)
        self.second_row_layout.setContentsMargins(4, 4, 4, 4)
        self.second_row_layout.setSpacing(3)
        self.content_layout.addWidget(self.played_panel)

        # 初始化出牌标签（从游戏配置动态生成）
        self.played_cards_labels: dict[str, QLabel] = {}
        for zone in settings.PLAYED_ZONES:
            key = zone["key"]
            label = zone["label"]
            lbl = QLabel(f"{PLAYED_ZONE_TITLES.get(key, label[:1])}  ")
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            lbl.setObjectName("InfoLabel")
            lbl.setTextFormat(Qt.TextFormat.PlainText)
            lbl.setMinimumWidth(0)
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.played_cards_labels[key] = lbl

        # 根据设置决定是否显示玩家所出的牌
        self._update_played_cards_visibility()

        # -------------------------
        # 保存 label 引用：后续更新用（保持你原逻辑）
        # name_labels：牌名 QLabel
        # count_labels：数量 QLabel
        # -------------------------
        self.name_labels: dict[str, QLabel] = {}
        self.count_labels: dict[str, QLabel] = {}

        # -------------------------
        # 创建两行：
        # row 0：牌名
        # row 1：数量
        # -------------------------
        for col, card in enumerate(self.card_order):
            # --- 牌名 ---
            name = QLabel(str(card))
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)

            # 给 QLabel 设置 objectName，方便 QSS 精准匹配
            # （QSS 中使用 #CardNameLabel 来选中）
            name.setObjectName("CardNameLabel")

            # 牌名也需要区分"已用完"状态（v<=0 时变灰）
            # 用 dynamicProperty：depleted=True/False，让 QSS 控制颜色
            name.setProperty("depleted", False)

            self.grid.addWidget(name, 0, col)
            self.name_labels[card] = name

            # --- 数量 ---
            cnt = QLabel(str(TOTAL_CARDS.get(card, 0)))
            cnt.setAlignment(Qt.AlignmentFlag.AlignCenter)

            # 数量 label 同样设置 objectName，QSS 中用 #CardCountLabel
            cnt.setObjectName("CardCountLabel")
            cnt.setProperty("depleted", False)

            self.grid.addWidget(cnt, 1, col)
            self.count_labels[card] = cnt

        # 加载布局配置选项（现在在设置对话框中加载）
        # self._load_layout_options()

        # -------------------------
        # 多进程推理 worker
        # -------------------------
        self.worker = InferenceWorker(self.layout_name)
        self.worker.result_ready.connect(self.on_result_ready)
        self.worker.performance_ready.connect(self.on_performance_ready)
        self.worker.diagnostic_status_changed.connect(self.on_diagnostic_status)
        self.worker.model_loading_changed.connect(self.on_model_loading_changed)
        self.worker.error.connect(self.on_worker_error)
        self.worker.screenshot_ready.connect(self.on_screenshot_ready)
        self._screenshot_preview: ScreenshotPreviewDialog | None = None
        self._preview_requested = settings.SHOW_CURRENT_SCREENSHOT
        self._diagnostic_status: dict[str, object] = {}
        self._diagnostic_warning = ""
        self._diagnostic_notice = ""
        self._layout_status = ""
        self._status_error = ""
        self._closing = False
        self.worker.finished.connect(self.on_worker_finished)

        # 初始化定时器
        self._busy = False
        self._last_cycle_start = time.perf_counter()  # 整轮耗时计时起点
        self._last_round_ms = 0.0  # 上一轮整轮耗时
        self.timer = QTimer(self)
        self.timer.setInterval(int(self.detect_interval_sec * 1000))
        self.timer.timeout.connect(self.request_one_update)

        self._is_settings_open = False

        # UI 刷新缓存：仅在状态变化时更新文本/样式，减少主线程开销
        self._last_count_values: dict[str, int] = {}
        self._last_depleted_values: dict[str, bool] = {}
        self._last_played_signature: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] | None = None

        self._settings_dialog: SettingsDialog | None = None
        self.settings_controller = SettingsController(self.worker, self)
        self.settings_controller.failed.connect(self._on_settings_error)
        self.settings_controller.applied.connect(self._refresh_open_settings)
        self.worker.state_changed.connect(self.on_worker_state_changed)
        self.worker.layout_status_changed.connect(self.on_layout_status_changed)
        self.worker.start()
        self._apply_panel_arrangement()
        self._apply_screenshot_preview_visibility()

    def on_settings_clicked(self) -> None:
        """点击设置按钮时打开既有设置对话框。"""
        self._open_settings()

    def on_donation_clicked(self) -> None:
        """打开同一个设置对话框并直接选中赞赏页，不建立额外页面。"""
        self._open_settings(donation=True)

    def _open_settings(self, *, donation: bool = False) -> None:
        """统一打开设置并暂停检测，赞赏入口复用正常关闭恢复流程。

        Args:
            donation: True直接显示既有赞赏页，False保持默认基本页。
        """
        if self._settings_dialog is not None:
            if donation:
                self._settings_dialog.tab_widget.setCurrentWidget(self._settings_dialog.tab4)
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        # 打开设置期间暂停检测
        self._is_settings_open = True
        try:
            if hasattr(self, 'timer') and self.timer.isActive():
                self.timer.stop()
        except Exception:
            pass

        # 注意：不要把 on_always_on_top_change_callback 传给 dialog（避免在对话框打开时立即变更窗口 flags 导致显示问题）
        dialog = SettingsDialog(
            self,
            on_interval_change_callback=self.on_interval_changed,
            on_layout_change_callback=self.on_layout_changed,
            on_device_change_callback=self.on_device_changed,
            on_reset_time_change_callback=self.on_reset_time_changed,
            on_frame_length_change_callback=self.on_frame_length_changed,
            on_always_on_top_change_callback=None,
            on_show_played_cards_change_callback=self.on_show_played_cards_changed,
            on_debug_mode_change_callback=self.on_debug_mode_changed,
            on_show_current_screenshot_change_callback=self.on_show_current_screenshot_changed,
            on_show_timing_change_callback=self.on_show_timing_changed,
            on_layout_editor_callback=self.on_layout_editor_clicked,
            on_layout_delete_callback=self.on_layout_delete_clicked,
            on_model_change_callback=self.on_model_changed,
            on_confidence_change_callback=self.on_confidence_changed,
        )
        dialog.manual_inference_changed.connect(self.on_manual_inference_changed)
        dialog.diagnostics_changed.connect(self.on_full_diagnostics_changed)
        dialog.diagnostic_disk_changed.connect(self.on_diagnostic_disk_changed)

        self._settings_dialog = dialog
        dialog.auto_layout_requested.connect(lambda: self.on_auto_layout_clicked(dialog))
        dialog.auto_layout_edit_requested.connect(lambda name: self.on_auto_layout_clicked(dialog, name))
        dialog.layout_edit_requested.connect(lambda name: self.on_layout_editor_clicked(dialog, name))
        dialog.layout_status_changed.connect(self.on_show_layout_status_changed)
        dialog.panel_arrangement_changed.connect(self.on_panel_arrangement_changed)
        dialog.set_current_always_on_top(settings.ALWAYS_ON_TOP)
        if settings.ALWAYS_ON_TOP:
            dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._refresh_open_settings()
        if donation:
            dialog.tab_widget.setCurrentWidget(dialog.tab4)

        try:
            dialog.exec()
            # 对于 "显示在最上层" 我们在对话框关闭后统一应用，避免在 modal dialog 打开时改变 window flags
            try:
                always_index = int(dialog.switch_always_on_top.isChecked())
                # 如果用户选择了不同值，则调用处理函数
                if always_index is not None:
                    self.on_always_on_top_changed(always_index)
            except Exception:
                pass
        finally:
            self._settings_dialog = None
            self._is_settings_open = False
            self._touch_no_target_time()
            self._start_timer_if_allowed()

    def _refresh_open_settings(self) -> None:
        """将已确认的运行设置显示到当前设置对话框。"""
        dialog = self._settings_dialog
        if dialog is None:
            return
        # 设置当前值
        dialog.set_current_interval(f"{self.detect_interval_sec}秒")
        layout_config = settings.WINDOW_LAYOUTS.get(self.layout_name, {})
        layout_mode = "auto" if layout_config.get("mode") == "auto" else "manual"
        dialog.refresh_layout_list(selected_name=self.layout_name)
        dialog.set_current_layout_mode(layout_mode, str(layout_config.get("window_title", "")))

        # 设置当前设备选择
        from config.settings import DEVICE_CHOICE
        dialog.set_current_device(DEVICE_CHOICE)

        # 设置当前重置时间
        from config.settings import RESET_TIME
        dialog.set_current_reset_time(RESET_TIME)

        # 设置当前帧长度
        from config.settings import FRAME_LENGTH
        dialog.set_current_frame_length(FRAME_LENGTH)

        # 设置当前是否显示玩家所出的牌
        from config.settings import SHOW_PLAYED_CARDS
        dialog.set_current_show_played_cards(SHOW_PLAYED_CARDS)
        dialog.set_current_panel_arrangement(settings.PANEL_ARRANGEMENT)

        # 设置当前调试模式
        from config.settings import DEBUG_MODE
        dialog.set_current_debug_mode(DEBUG_MODE)

        dialog.set_current_show_current_screenshot(self._preview_requested)
        dialog.set_current_manual_inference(settings.PACKED_INFERENCE)
        dialog.set_current_full_diagnostics(settings.FULL_DIAGNOSTICS)
        dialog.set_current_diagnostic_disk(float(settings.DIAGNOSTIC_DISK_GIB))
        dialog.set_diagnostic_status(self._diagnostic_status)
        dialog.set_current_show_layout_status(settings.SHOW_LAYOUT_STATUS)

        # 设置当前显示耗时
        from config.settings import SHOW_TIMING
        dialog.set_current_show_timing(SHOW_TIMING)

        # 设置当前模型选择
        from config.settings import YOLO_MODEL_NAME
        dialog.set_current_model(YOLO_MODEL_NAME)

        # 设置当前置信度阈值
        from config.settings import YOLO_CONFIDENCE_THRESHOLD
        dialog.set_current_confidence(YOLO_CONFIDENCE_THRESHOLD)

    def on_layout_editor_clicked(self, settings_dialog: SettingsDialog, layout_name: str | None = None) -> None:
        """编辑明确指定的布局，保存后立即更新列表并重新应用同名活动配置。

        Args:
            settings_dialog: 当前设置窗口。
            layout_name: 点击编辑的手动布局；None为新建。
        """
        editor = LayoutEditorDialog(
            settings_dialog, initial_layout_name=layout_name,
            on_restore_topmost=lambda: self._reapply_topmost_if_enabled(settings_dialog),
            stay_on_top=settings.ALWAYS_ON_TOP,
        )
        try:
            if editor.exec() != editor.DialogCode.Accepted or not editor.saved_layout_name:
                return
            saved_name = editor.saved_layout_name
            apply_saved = editor.saved_set_current or saved_name == self.layout_name
        finally:
            editor.deleteLater()
        settings_dialog.refresh_layout_list(selected_name=saved_name if apply_saved else self.layout_name)
        # 名字相同不代表配置相同，仍须提交完整新坐标并等待后端确认。
        if apply_saved:
            self.on_layout_changed(saved_name)

    def on_auto_layout_clicked(self, dialog: SettingsDialog, layout_name: str | None = None) -> None:
        """命名创建或编辑自动布局，后端确认成功才保存并设为当前。

        Args:
            dialog: 当前设置窗口。
            layout_name: 已有自动布局名称，None为新建。
        """
        try:
            titles = [title for title in list_visible_window_titles()
                      if not title.startswith("Han记牌器") and title != dialog.windowTitle()]
            if not titles and layout_name is None:
                QMessageBox.information(dialog, "自动布局", "请先打开游戏窗口，再选择自动布局。")
                return
            editor = AutoLayoutDialog(titles, dialog, initial_layout_name=layout_name)
            try:
                if editor.exec() != editor.DialogCode.Accepted:
                    return
                name, title = editor.layout_name, editor.window_title
            finally:
                editor.deleteLater()
            config = settings.validate_layout({"window_title": title, "mode": "auto", "layout": {}})
            self.settings_controller.apply(
                save=lambda: settings.save_window_layout(name, title, {}, mode="auto"),
                request=lambda: self.worker.switch_layout(name, config),
                after=lambda: self._apply_layout_selection(name),
            )
        except Exception as exc:
            self._on_settings_error(f"无法创建自动布局: {exc}")

    @Slot(str)
    def on_layout_status_changed(self, message: str) -> None:
        """显示自动定位进度及归属不确定状态。

        Args:
            message: 来自推理进程的布局状态；手动模式为空。
        """
        self._layout_status = message
        if self.worker.state == "ready":
            self._status_error = ""
            self._refresh_title_status()

    def on_show_layout_status_changed(self, index: int) -> None:
        """保存布局提示开关，故障信息不受该开关影响。

        Args:
            index: 否/是选项索引。
        """
        if index in (0, 1):
            self._apply_setting("show_layout_status", bool(index), after=self._refresh_title_status)

    def _refresh_title_status(self) -> None:
        """全部状态只在顶部显示，优先故障并在悬停详情中保留并存告警。"""
        alerts = [item for item in (self._status_error, self._diagnostic_warning,
                                    self._diagnostic_notice) if item]
        failed = self.worker.state == 'failed'
        self.btn_retry.setVisible(failed)
        if self._status_error or failed:
            text = '识别异常' if self._status_error else '识别已停止'
            if not self._status_error:
                alerts.insert(0, '识别已停止，请点击顶部“重启”恢复')
        elif self.worker.is_loading_model:
            text = '首次可能较久'
            alerts.insert(0, '正在加载模型。首次需要转换ONNX或TensorRT时可能较久，请稍候。')
        elif self._diagnostic_warning:
            text = '诊断暂停' if self._diagnostic_status.get('state') == 'paused' else '日志异常'
        elif self._diagnostic_notice:
            text = '诊断暂缓'
        else:
            detail = self._layout_status if settings.SHOW_LAYOUT_STATUS else ''
            self.title_bar.set_layout_status(compact_layout_status(detail), detail)
            return
        self.title_bar.set_layout_status(text, '\n\n'.join(alerts))

    def _switch_layout_by_name(self, layout_name: str) -> bool:
        """按布局名切换，避免设置窗口刷新后索引和内存列表不同步。"""
        if layout_name not in settings.WINDOW_LAYOUTS:
            return False
        self.on_layout_changed(layout_name)
        return True

    def on_layout_delete_clicked(self, settings_dialog: SettingsDialog, layout_name: str) -> bool:
        """删除当前布局前先确认替代布局可用。

        Args:
            settings_dialog: 当前设置窗口。
            layout_name: 待删除名称。

        Returns:
            已完成删除或已提交必要切换时为 True。
        """
        names = list(settings.WINDOW_LAYOUTS)
        if layout_name not in names or len(names) <= 1:
            self._on_settings_error("布局不存在或只剩最后一个布局。")
            return False
        request = None
        replacement = self.layout_name
        after = self._refresh_open_settings
        if layout_name == self.layout_name:
            replacement = next(name for name in names if name != layout_name)
            request = lambda: self.worker.switch_layout(replacement)
            after = lambda: self._apply_layout_selection(replacement)
        return self.settings_controller.apply(
            save=lambda: self._delete_saved_layout(layout_name),
            request=request,
            after=after,
        )

    def _delete_saved_layout(self, name: str) -> None:
        """通过统一配置入口删除布局。

        Args:
            name: 待删除名称。

        Raises:
            ValueError: 布局不存在、只剩一个布局或配置损坏。
            OSError: 配置读写失败。
        """
        settings.delete_window_layout(name)

    def on_pause_clicked(self) -> None:
        """切换检测暂停状态，并更新标题栏图标。

        暂停时停止定时检测；恢复时重置超时参考时间后重新启动定时器。
        """
        if self.is_paused:
            self.is_paused = False
            self.title_bar.set_paused(False)
            self._start_timer_if_allowed()
            self._touch_no_target_time()
            print("检测已恢复")
        else:
            self._stop_timer_if_exists()
            self.is_paused = True
            self.title_bar.set_paused(True)
            print("检测已暂停")

    def _load_layout_options(self):
        """
        加载所有可用的布局配置选项到下拉框（现在在设置对话框中加载）
        """
        pass

    def _touch_no_target_time(self):
        """通知子进程刷新 no_target_time，避免暂停或交互后立即触发超时重置。"""
        if hasattr(self, 'worker'):
            self.worker.touch_time()

    def _start_timer_if_allowed(self) -> None:
        """仅在服务就绪且未暂停、未打开设置时启动检测。"""
        if (hasattr(self, "timer") and self.worker.state == "ready"
                and not self.is_paused and not self._is_settings_open):
            self.timer.start()

    def _stop_timer_if_exists(self):
        """安全停止定时器。"""
        if hasattr(self, 'timer'):
            self.timer.stop()

    @Slot()
    def request_one_update(self) -> None:
        """只有请求真正入队时才进入 busy 状态。"""
        if self._busy or self.is_paused or self._is_settings_open:
            return
        self._last_cycle_start = time.perf_counter()
        self._busy = self.worker.request_detect()

    @Slot(dict, dict, float)
    def on_result_ready(self, remain_cards: dict[str, int], zone_cards: dict[str, list[list[str]]], inference_ms: float) -> None:
        """
        收到 worker 的识别结果：
        - 更新剩余牌数量
        - v <= 0 时样式变灰
        - v > 0 时样式恢复正常
        - 更新各区域出牌记录

        Args:
            remain_cards: 各牌点剩余数量。
            zone_cards: 各区域已经确认的出牌历史。
            inference_ms: 当轮模型调用耗时，单位毫秒。
        """
        # 计算整轮耗时（从本轮开始到收到结果）
        now = time.perf_counter()
        self._last_round_ms = (now - self._last_cycle_start) * 1000

        # 更新标题栏
        if settings.SHOW_TIMING:
            self._set_window_caption(
                f"Han记牌器 · 推理{inference_ms:.0f}ms · 整轮{self._last_round_ms:.0f}ms",
            )
        else:
            self._set_window_caption("Han记牌器")

        # 更新出牌文本（仅在内容变化时更新）
        played_signature = tuple(
            (key, tuple(tuple(x) if isinstance(x, list) else x for x in zone_cards.get(key, [])))
            for zone in settings.PLAYED_ZONES
            for key in [zone["key"]]
        )
        if played_signature != self._last_played_signature:
            for zone in settings.PLAYED_ZONES:
                key = zone["key"]
                label = zone["label"]
                lbl = self.played_cards_labels.get(key)
                if lbl:
                    history = trans_yolo_names_to_string(zone_cards.get(key, []))
                    lbl.setText(f"{PLAYED_ZONE_TITLES.get(key, label[:1])}  {history}")
                    lbl.setToolTip(f"{label}：{history.strip()}")
            self._last_played_signature = played_signature

        for card in self.card_order:
            v = remain_cards.get(card, 0)
            prev_v = self._last_count_values.get(card)

            # 1) 更新数量文字和 count 属性（仅在数量变化时）
            if prev_v != v:
                self.count_labels[card].setText(str(v)) # .setText(...)：修改标签内容
                self.count_labels[card].setProperty("count", str(v))

            # 2) 设置 depleted 属性：仅在状态变化时
            depleted = (v <= 0)
            prev_depleted = self._last_depleted_values.get(card)
            if prev_depleted != depleted:
                self.name_labels[card].setProperty("depleted", depleted)
                self.count_labels[card].setProperty("depleted", depleted)

            # 更新缓存
            self._last_count_values[card] = v
            self._last_depleted_values[card] = depleted

            # 3) 仅在属性变动时重刷样式，避免无效 unpolish/polish
            if prev_v != v or prev_depleted != depleted:
                self.name_labels[card].style().unpolish(self.name_labels[card])
                self.name_labels[card].style().polish(self.name_labels[card])

                self.count_labels[card].style().unpolish(self.count_labels[card])
                self.count_labels[card].style().polish(self.count_labels[card])

    def _reset_ui_to_total(self) -> None:
        """
        把 UI 的显示重置为 TOTAL_CARDS：
        - 数量恢复成总数
        - depleted 属性恢复 False
        - 强制刷新 QSS
        """
        for card in self.card_order:
            v = TOTAL_CARDS.get(card, 0)
            self.count_labels[card].setText(str(v))

            self.name_labels[card].setProperty("depleted", False)
            self.count_labels[card].setProperty("depleted", False)

            # 设置 count 属性，用于QSS样式控制（当数量等于4时显示红色）
            self.count_labels[card].setProperty("count", str(v))

            # 强制刷新样式（让 QSS 立即响应 depleted 变化）
            self.name_labels[card].style().unpolish(self.name_labels[card])
            self.name_labels[card].style().polish(self.name_labels[card])

            self.count_labels[card].style().unpolish(self.count_labels[card])
            self.count_labels[card].style().polish(self.count_labels[card])

            # 重置缓存，确保下一帧增量更新一致
            self._last_count_values[card] = v
            self._last_depleted_values[card] = False

        self._last_played_signature = None
        for zone in settings.PLAYED_ZONES:
            label = self.played_cards_labels.get(zone["key"])
            if label is not None:
                label.setText(f"{PLAYED_ZONE_TITLES.get(zone['key'], zone['label'][:1])}  ")
                label.setToolTip("")

    def on_reset_clicked(self) -> None:
        """等待重置回执后清空显示；服务故障时改为重启。"""
        if self.worker.state in ("failed", "stopped"):
            self.on_restart_clicked()
            return
        self.settings_controller.apply(
            save=lambda: None, request=self.worker.request_reset,
            after=self._reset_ui_to_total,
        )

    def on_interval_changed(self, index: int) -> None:
        """保存成功后调整 UI 定时器间隔。

        Args:
            index: 间隔选项索引。
        """
        if 0 <= index < len(INTERVAL_TEXT_OPTIONS):
            value = float(INTERVAL_TEXT_OPTIONS[index].replace("秒", ""))
            self._apply_setting("detect_interval_sec", value, after=self._apply_interval)

    def on_layout_changed(self, layout_name: str) -> None:
        """提交选中的命名布局，成功后确认选择并清空牌局。

        Args:
            layout_name: 已保存的自动或手动布局名称。
        """
        layout_config = settings.WINDOW_LAYOUTS.get(layout_name)
        if not isinstance(layout_config, dict):
            return
        try:
            validated_layout = settings.validate_layout(layout_config)
        except (TypeError, ValueError) as exc:
            self._on_settings_error(f'布局内容无效：{exc}')
            return
        self.settings_controller.apply(
            save=lambda: settings.save_current_layout(layout_name),
            request=lambda: self.worker.switch_layout(layout_name, validated_layout),
            after=lambda: self._apply_layout_selection(layout_name),
        )

    def on_device_changed(self, index: int) -> None:
        """设备切换成功后再保存。

        Args:
            index: CPU/GPU 索引。
        """
        if index not in (0, 1):
            return
        device = ("cpu", "cuda")[index]
        self.settings_controller.apply(
            save=lambda: settings.save_device_choice(device),
            request=lambda: self.worker.switch_device(device),
        )

    def on_model_changed(self, index: int) -> None:
        """模型切换成功后保存，并重置记牌显示。

        Args:
            index: 模型列表索引。
        """
        models = settings._scan_model_dirs()
        if not 0 <= index < len(models):
            return
        name = models[index]
        self.settings_controller.apply(
            save=lambda: settings.save_model_choice(name),
            request=lambda: self.worker.switch_model(name),
            after=self._reset_ui_to_total,
        )

    def on_confidence_changed(self, index: int) -> None:
        """更新推理置信度。

        Args:
            index: 置信度选项索引。
        """
        values = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
        if 0 <= index < len(values):
            self._apply_setting("yolo_confidence_threshold", values[index], "YOLO_CONFIDENCE_THRESHOLD")

    def on_reset_time_changed(self, index: int) -> None:
        """更新无目标超时时间。

        Args:
            index: 超时选项索引。
        """
        if 0 <= index < len(RESET_TIME_OPTIONS):
            self._apply_setting("reset_time", RESET_TIME_OPTIONS[index], "RESET_TIME")

    def on_frame_length_changed(self, index: int) -> None:
        """更新连续帧确认阈值。

        Args:
            index: 帧长度选项索引。
        """
        if 0 <= index < len(FRAME_LENGTH_OPTIONS):
            self._apply_setting("frame_length", FRAME_LENGTH_OPTIONS[index], "FRAME_LENGTH")

    def on_always_on_top_changed(self, index: int) -> None:
        """保存成功后应用置顶状态。

        Args:
            index: 否/是选项索引。
        """
        if index in (0, 1) and bool(index) != settings.ALWAYS_ON_TOP:
            self._apply_setting(
                "always_on_top", bool(index),
                after=lambda: self._apply_always_on_top_state(settings.ALWAYS_ON_TOP),
            )

    def _apply_always_on_top_state(self, always_on_top: bool, settings_dialog: QWidget | None = None) -> None:
        """切换置顶时保留窗口位置、尺寸和原有显示状态。

        Args:
            always_on_top: 是否置顶。
            settings_dialog: 可选的设置窗口。
        """
        for widget in (self, settings_dialog):
            if widget is None or bool(widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) == always_on_top:
                continue
            geometry = widget.saveGeometry()
            client_geometry = widget.geometry()
            normal = not (widget.isMaximized() or widget.isMinimized() or widget.isFullScreen())
            visible = widget.isVisible()
            widget.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, always_on_top)
            widget.restoreGeometry(geometry)
            if visible:
                widget.show()
            if normal:
                # 扩展标题栏在分数DPI下整矩形恢复会再次折算边框，位置/宽度可漂移1像素。
                # 分别向原生QWindow恢复Qt逻辑位置和尺寸，避免QWidget缓存误判为无需调整。
                handle = widget.windowHandle()
                if handle is None:
                    widget.setGeometry(client_geometry)
                else:
                    handle.resize(client_geometry.size())
                    handle.setPosition(client_geometry.topLeft())
        self.central_widget.update()

    def _reapply_topmost_if_enabled(self, settings_dialog: QWidget) -> None:
        """按当前设置恢复置顶，不重复改变已有窗口的尺寸。

        Args:
            settings_dialog: 当前设置窗口。
        """
        self._apply_always_on_top_state(settings.ALWAYS_ON_TOP, settings_dialog)

    def on_show_played_cards_changed(self, index: int) -> None:
        """保存成功后切换出牌记录显示。

        Args:
            index: 否/是选项索引。
        """
        if index in (0, 1):
            self._apply_setting("show_played_cards", bool(index), after=self._apply_played_visibility)

    @Slot(int)
    def on_panel_arrangement_changed(self, index: int) -> None:
        """原子保存显示排列，不向推理进程发送命令或重置牌局。

        Args:
            index: 0表示上下排列，1表示左右排列。
        """
        if index in (0, 1):
            value = ('vertical', 'horizontal')[index]
            if value != settings.PANEL_ARRANGEMENT:
                self._apply_setting('panel_arrangement', value, after=self._apply_panel_arrangement)

    def _apply_panel_arrangement(self) -> None:
        """切换两区排列和单边分隔线方向，并按内容重新收拢尺寸。"""
        self._panel_arrangement = settings.PANEL_ARRANGEMENT
        horizontal = self._panel_arrangement == 'horizontal'
        self.played_panel.setProperty('arrangement', self._panel_arrangement)
        self.played_panel.style().unpolish(self.played_panel)
        self.played_panel.style().polish(self.played_panel)
        self.played_panel.update()
        self.content_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if horizontal else QBoxLayout.Direction.TopToBottom,
        )
        # 两块使用相同最小宽度及伸缩系数，窗口拉宽时也始终各占一半。
        paired = horizontal and self._show_played_cards
        panel_minimum = max(HORIZONTAL_PANEL_MIN_WIDTH, self.grid.minimumSize().width() + 10) if paired else 0
        self.card_panel.setMinimumWidth(panel_minimum)
        self.played_panel.setMinimumWidth(panel_minimum)
        self.content_layout.setStretch(0, 1 if horizontal else 0)
        self.content_layout.setStretch(1, 1 if horizontal else 0)
        self.card_panel_layout.setContentsMargins(5, 1 if horizontal else 4, 5, 1 if horizontal else 4)
        self.second_row_layout.setContentsMargins(4, 1 if horizontal else 4, 4, 1 if horizontal else 4)
        self.second_row_layout.setSpacing(0 if horizontal else 3)
        self.grid.setVerticalSpacing(2 if horizontal else 4)
        self.root_layout.setContentsMargins(0, 0, 0, 1 if horizontal else 4)
        self._fit_main_window(reset_width=True)

    def on_debug_mode_changed(self, index: int) -> None:
        """同步调试日志开关。

        Args:
            index: 否/是选项索引。
        """
        if index in (0, 1):
            self._apply_setting("debug_mode", bool(index), "DEBUG_MODE")

    @Slot(float)
    def on_diagnostic_disk_changed(self, value: float) -> None:
        """经现有回执和原子保存流程更新磁盘额度，不改变RAM预算。

        Args:
            value: 用户选择的GiB额度。
        """
        self._apply_setting('diagnostic_disk_gib', value, 'DIAGNOSTIC_DISK_GIB')

    @Slot(int)
    def on_full_diagnostics_changed(self, index: int) -> None:
        """经后端回执和原子保存流程切换完整诊断。

        Args:
            index: 否为零，是为一。
        """
        self._apply_setting('full_diagnostics', bool(index), 'FULL_DIAGNOSTICS')

    @Slot(dict)
    def on_diagnostic_status(self, status: dict[str, object]) -> None:
        """即使隐藏普通布局提示，也展示诊断暂停或日志故障。

        Args:
            status: 后台真实运行状态，不改变识别服务生命周期。
        """
        value = status.get('at_monotonic_ns', 0)
        stamp = value if isinstance(value, int) else 0
        if stamp and stamp < int(str(self._diagnostic_status.get('at_monotonic_ns', 0))):
            return
        self._diagnostic_status = dict(status)
        reason = str(status.get('reason', ''))
        warning = ('完整诊断已暂停：'+(reason or '未知原因').splitlines()[-1]+'；关闭后重新开启') if status.get('state') == 'paused' else ''
        notice = ('完整诊断暂缓：'+(reason or '保存队列拥堵').splitlines()[-1]+'；队列回落后自动恢复'
                  if status.get('state') == 'backpressure' else '')
        if status.get('daily_log_error'):
            warning += ' 日常日志写入失败：'+str(status['daily_log_error'])
        self._diagnostic_warning = warning
        self._diagnostic_notice = notice
        self._refresh_title_status()
        if self._settings_dialog is not None:
            self._settings_dialog.set_diagnostic_status(status)

    def on_manual_inference_changed(self, index: int) -> None:
        """后端确认后保存手动布局检测方式，不清空正在进行的牌局。

        Args:
            index: 0为拼图，1为原图。
        """
        if index in (0, 1):
            self._apply_setting('packed_inference', index == 0, 'PACKED_INFERENCE',
                                after=self._clear_input_preview)

    def _clear_input_preview(self) -> None:
        """检测方式切换确认后丢弃旧画面，设置窗口关闭并继续检测后接收新帧。"""
        if self._screenshot_preview is not None:
            self._screenshot_preview.reset_frame('检测方式已切换；关闭设置并继续检测后显示新的完整输入。')


    def on_show_timing_changed(self, index: int) -> None:
        """保存并刷新耗时显示开关。

        Args:
            index: 否/是选项索引。
        """
        if index in (0, 1):
            self._apply_setting("show_timing", bool(index), after=self._apply_timing_visibility)

    def on_show_current_screenshot_changed(self, index: int) -> None:
        """立即保存主界面预览偏好，再同步子进程的截图传输开关。

        Args:
            index: 否/是选项索引。
        """
        if index in (0, 1):
            self._apply_setting(
                "show_current_screenshot", bool(index), after=self._apply_screenshot_preference,
            )

    def _apply_screenshot_preference(self) -> None:
        """按已保存偏好更新窗口，后端故障或退出不回滚预览显示偏好。"""
        self._preview_requested = settings.SHOW_CURRENT_SCREENSHOT
        self._apply_screenshot_preview_visibility()
        if self.worker.state in ("starting", "ready"):
            self.settings_controller.apply(
                save=lambda: None,
                request=lambda: self.worker.update_settings({"SHOW_CURRENT_SCREENSHOT": self._preview_requested}),
            )

    def _apply_screenshot_preview_visibility(self) -> None:
        """根据已保存选项显示或关闭截图预览窗口。"""
        if self._preview_requested and settings.SHOW_CURRENT_SCREENSHOT:
            self._show_screenshot_preview()
        elif self._screenshot_preview is not None:
            self._screenshot_preview.hide()

    def _show_screenshot_preview(self) -> None:
        """创建或显示截图预览窗口。"""
        if self._screenshot_preview is None:
            self._screenshot_preview = ScreenshotPreviewDialog(self)
            self._screenshot_preview.rejected.connect(self._on_screenshot_preview_closed)
        if not self._screenshot_preview.isVisible() and not self._closing:
            self._screenshot_preview.show()

    def _on_screenshot_preview_closed(self) -> None:
        """用户按关闭或 Esc 时禁用预览；主程序退出不改用户设置。"""
        if not self._closing:
            self.on_show_current_screenshot_changed(0)

    @Slot(bytes, dict)
    def on_screenshot_ready(
        self, image_data: bytes, regions: dict[str, tuple[float, float, float, float]] | None = None,
    ) -> None:
        """显示推理进程返回的最新截图。

        Args:
            image_data: JPEG 编码的游戏窗口截图。
            regions: 自动布局当前确认的各牌区归一化边界；旧调用省略时不画区域。
        """
        if self._closing or not self._preview_requested or not settings.SHOW_CURRENT_SCREENSHOT:
            return
        self._show_screenshot_preview()
        if self._screenshot_preview is not None:
            if not image_data:
                self._screenshot_preview.reset_frame('当前未取得有效画面，请恢复目标窗口并继续检测。')
                return
            region_labels = {item["key"]: item["label"] for item in settings.LAYOUT_REGIONS}
            self._screenshot_preview.update_image(image_data, regions or {}, region_labels,
                                                  source_description=self._layout_status)

    @Slot(dict)
    def on_performance_ready(self, performance: dict[str, float]) -> None:
        """把分阶段耗时显示在设置按钮提示中。

        Args:
            performance: 当前代次的子进程指标；缺失项不伪装成已测。
        """
        labels = {"capture_ms": "截图", "prepare_ms": "拼图准备（含规划与裁剪）", "plan_ms": "其中：拼图规划",
                  "model_ms": "模型调用（含模型内预处理/后处理）",
                  "restore_ms": "坐标恢复/去重", "preview_ms": "预览编码", "layout_save_ms": "布局与记牌",
                  "pipeline_ms": "子进程整轮", "diagnostic_snapshot_ms": "诊断快照（含图片复制）"}
        lines = ["上次检测；主进程整轮耗时另见标题栏"]
        lines.extend(f"{label}：{performance[key]:.1f} ms" for key, label in labels.items() if key in performance)
        if "model_calls" in performance:
            lines.append(f"模型调用次数：{int(performance['model_calls'])}")
        for key, label in (("source_coverage", "裁片覆盖原图"), ("source_with_overlap", "含重叠的源面积"),
                           ("padding_fraction", "画布未占用面积")):
            if key in performance:
                lines.append(f"{label}：{performance[key]:.1%}")
        if "tile_count" in performance:
            lines.append(f"拼图块数：{int(performance['tile_count'])}")
        if "region_count" in performance:
            lines.append(f"参与区域：{int(performance['region_count'])}")
        self.btn_settings.setToolTip("\n".join(lines))

    def _apply_setting(
        self, key: str, value: object, runtime_key: str | None = None,
        after: Callable[[], None] | None = None,
    ) -> None:
        """将设置回调统一交给协调器。

        Args:
            key: 持久化字段。
            value: 新值。
            runtime_key: 需要同步到子进程的大写属性；纯 UI 设置为 None。
            after: 应用成功后的刷新操作。
        """
        request = None
        if runtime_key is not None:
            request = lambda: self.worker.update_settings({runtime_key: value})
        self.settings_controller.apply(lambda: settings.save_setting(key, value), request, after)

    def _apply_layout_selection(self, name: str) -> None:
        """发布已确认的布局并重置显示。

        Args:
            name: 已应用布局名。
        """
        self.layout_name = name
        self._layout_status = ""
        self._refresh_title_status()
        self._reset_ui_to_total()
        self._refresh_open_settings()

    def _apply_interval(self) -> None:
        """从已保存配置刷新定时器。"""
        self.detect_interval_sec = settings.DETECT_INTERVAL_SEC
        self.timer.setInterval(int(self.detect_interval_sec * 1000))

    def _apply_played_visibility(self) -> None:
        """从已保存配置刷新出牌记录显示。"""
        self._show_played_cards = settings.SHOW_PLAYED_CARDS
        self._update_played_cards_visibility()

    def _set_window_caption(self, caption: str) -> None:
        """窗口保留固定名称，状态与耗时只在应用标题栏绘制一次。

        Args:
            caption: 主窗口需要展示的标题文本。
        """
        self.setWindowTitle("Han记牌器")
        self.setAccessibleName(caption)
        self.title_bar.set_caption(caption)

    def showEvent(self, event: QShowEvent) -> None:
        """窗口显示后读取安全区域，避开右侧系统标题栏按钮。

        Args:
            event: Qt窗口显示事件。
        """
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None:
            if not self._safe_area_signal_connected:
                handle.safeAreaMarginsChanged.connect(self._update_title_bar_safe_area)
                handle.screenChanged.connect(self._schedule_title_bar_refresh)
                self._safe_area_signal_connected = True
            self._update_title_bar_safe_area(handle.safeAreaMargins())
            clip_native_title_overlay(self)
        # 首显不能把创建期间的临时高度当作用户尺寸保留；重显才只允许增高。
        self._fit_main_window(shrink=self._initial_size_pending)
        self._title_bar_update_timer.start(0)

    def event(self, event: QEvent) -> bool:
        """阻止Qt默认帮助提示自动创建底栏，其余事件保持原有行为。

        Args:
            event: Qt分发的窗口事件。
        Returns:
            帮助状态事件已接管时为True，其他事件交由主窗口处理。
        """
        if event.type() == QEvent.Type.StatusTip:
            event.accept()
            return True
        return super().event(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """尺寸变化后重新读取已更新的系统按钮边界。

        Args:
            event: Qt窗口尺寸事件。
        """
        super().resizeEvent(event)
        if hasattr(self, '_title_bar_update_timer'):
            self._title_bar_update_timer.start(0)

    def _refresh_title_bar_geometry(self) -> None:
        """原生布局生效后更新避让、收拢首显高度并修正分数DPI最小尺寸取整。"""
        handle = self.windowHandle()
        if handle is not None:
            self._update_title_bar_safe_area(handle.safeAreaMargins())
            clip_native_title_overlay(self)
        if self._initial_size_pending and self.isVisible():
            # 先清标记，避免本次resize触发的下一次回调继续压缩用户窗口。
            self._initial_size_pending = False
            self._fit_main_window()
        if handle is not None and self.isVisible() and not (
            self.isMaximized() or self.isMinimized() or self.isFullScreen()
        ):
            # 原生边框取整可能让客户区比minimumSize少1逻辑像素，随后重建窗口才被补齐。
            # 在既有resize事件回调内补齐，避免首显/预览和置顶后的宽度前后不一致。
            size = self.size().expandedTo(self.minimumSize())
            if size != self.size():
                handle.resize(size)

    @Slot()
    def _schedule_title_bar_refresh(self) -> None:
        """切换屏幕后按新的缩放比例重新设置标题层裁剪区域。"""
        self._title_bar_update_timer.start(0)

    def _update_title_bar_safe_area(self, margins: QMargins) -> None:
        """按系统标题栏高度和安全边距对齐控件、避开系统按钮。

        Args:
            margins: Qt报告的窗口安全区边距。
        """
        title_height = self.style().pixelMetric(QStyle.PixelMetric.PM_TitleBarHeight, None, self)
        title_button = self.style().pixelMetric(QStyle.PixelMetric.PM_TitleBarButtonSize, None, self)
        self.title_bar.setFixedHeight(max(32, title_height, margins.top()))
        # PM_TitleBarButtonSize可能只有16px，不能据此估算Windows的宽按钮组。
        native_width = max(native_caption_button_width(self), 144)
        right_inset = max(margins.right() + 8, native_width + 8, title_button * 3 + 12)
        self.title_bar.set_right_safe_area_inset(right_inset)

    def _apply_timing_visibility(self) -> None:
        """刷新耗时标题，故障状态保持可见。"""
        if self.worker.state == "failed":
            return
        if self.worker.is_loading_model:
            self._set_window_caption("正在加载模型")
            return
        self._set_window_caption(
            "Han记牌器 · 推理--ms · 整轮--ms" if settings.SHOW_TIMING else "Han记牌器",
        )

    def _on_settings_error(self, message: str) -> None:
        """设置失败时恢复已确认的控件值并显示原因。

        Args:
            message: 错误详情。
        """
        self._preview_requested = settings.SHOW_CURRENT_SCREENSHOT
        self._apply_screenshot_preview_visibility()
        self._refresh_open_settings()
        QMessageBox.warning(self._settings_dialog or self, "设置未生效", message)

    @Slot(bool)
    def on_model_loading_changed(self, loading: bool) -> None:
        """模型/设备切换时只刷新顶部提示，不释放检测或设置请求。

        Args:
            loading: 是否仍有模型加载请求，启动状态由worker生命周期维护。
        """
        if loading:
            self._status_error = ""
        self._apply_timing_visibility()
        self._refresh_title_status()

    @Slot(str)
    def on_worker_state_changed(self, state: str) -> None:
        """根据生命周期控制定时器与恢复入口。

        Args:
            state: 推理服务状态。
        """
        self._busy = False
        self.btn_retry.setVisible(state == "failed")
        self._status_error = ""
        self._layout_status = ""
        if state == "ready":
            self._apply_timing_visibility()
            self._start_timer_if_allowed()
        else:
            self.timer.stop()
            labels = {"starting": "正在加载模型…", "failed": "识别已停止，请重启识别", "stopped": "识别已停止"}
            self._set_window_caption(labels[state] if state == "starting" else "Han记牌器  ·  " + labels.get(state, state))
        self._refresh_title_status()

    @Slot()
    def on_restart_clicked(self) -> None:
        """重启推理服务并重新初始化牌局显示。"""
        self._busy = False
        self._reset_ui_to_total()
        self.worker.restart()

    def _update_played_cards_visibility(self) -> None:
        """保留出牌标签的布局归属，仅切换可见性。"""
        for label in self.played_cards_labels.values():
            if self.second_row_layout.indexOf(label) < 0:
                self.second_row_layout.addWidget(label)
            label.setVisible(self._show_played_cards)
        self.played_panel.setVisible(self._show_played_cards)
        self._apply_panel_arrangement()

    def _fit_main_window(self, shrink: bool = True, *, reset_width: bool = False) -> None:
        """按整个主窗口计算高度，重显窗口时保留用户已调整的尺寸。

        Args:
            shrink: 是否按内容收起高度；首次显示为True，后续重显/置顶重建为False。
            reset_width: 用户主动切换显示排列或显隐时按新布局收拢宽度。
        """
        previous_width = self.width()
        self.setMinimumSize(0, 0)
        self.central_widget.updateGeometry()
        self.root_layout.invalidate()
        self.root_layout.activate()
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        minimum = self.minimumSizeHint()
        base_width = (HORIZONTAL_WINDOW_WIDTH if self._panel_arrangement == 'horizontal'
                      and self._show_played_cards else COMPACT_WINDOW_WIDTH)
        base_height = 80 if self._panel_arrangement == 'horizontal' else 100
        self.setMinimumSize(max(base_width, minimum.width()), max(base_height, minimum.height()))
        height = max(base_height, self.sizeHint().height(), 0 if shrink else self.height())
        self.resize(self.minimumWidth() if reset_width else max(previous_width, self.minimumWidth()), height)

    @Slot(str)
    def on_worker_error(self, err_text: str) -> None:
        """显示推理错误，保留详情供排查。

        Args:
            err_text: 错误详情。
        """
        print("Worker error:\n", err_text)
        self._status_error = err_text.strip() or "识别失败"
        self._refresh_title_status()

    @Slot()
    def on_worker_finished(self):
        """
        worker 一轮结束（保持你原逻辑）：
        - 解除 busy，允许下一轮定时触发
        """
        self._busy = False

    def closeEvent(self, event: QCloseEvent) -> None:
        """
        窗口关闭时清理：
        - 停止 timer
        - 停止子进程
        """
        self._closing = True
        if self._screenshot_preview is not None:
            self._screenshot_preview.hide()
        self.timer.stop()
        if hasattr(self, 'worker'):
            self.worker.stop()
        super().closeEvent(event)
