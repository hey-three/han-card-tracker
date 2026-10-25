# -*- coding: utf-8 -*-

"""
主窗口模块
提供斗地主记牌器的主界面，包括剩余牌统计、出牌显示等功能
"""

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QGridLayout, QPushButton, QHBoxLayout, QMainWindow, QSizePolicy, QMessageBox
)
from core.inference_process import InferenceWorker
from config.settings import TOTAL_CARDS
from utils.trans_yolo_names_to_string import trans_yolo_names_to_string
from ui.layout_editor_dialog import LayoutEditorDialog
from ui.settings_dialog import SettingsDialog
from ui.settings_controller import SettingsController
import config.settings as settings
import time


INTERVAL_TEXT_OPTIONS = ["0.1秒", "0.15秒", "0.2秒", "0.25秒", "0.3秒", "0.35秒", "0.4秒", "0.45秒", "0.5秒"]
RESET_TIME_OPTIONS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
FRAME_LENGTH_OPTIONS = [1, 2, 3, 4, 5, 6]


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

        # -------------------------
        # 窗口基础设置（保持你原设置）
        # -------------------------
        self.setWindowTitle("Han记牌器")
        self.setMinimumWidth(550) # 最小宽度
        self.setMinimumHeight(100) # 允许窗口缩小到最小高度
        self.resize(550, 100) # 设置初始窗口大小

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

        # 创建中央部件
        self.central_widget = QWidget()
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
        self.root_layout.setContentsMargins(8, 8, 8, 8) # 内边距（布局边缘到窗口边缘的距离），四个数值分别对应：左、上、右、下（单位：像素）。
        self.root_layout.setSpacing(6)          #设置布局内子控件之间的间距（单位：像素）。



        self.grid = QGridLayout() # 创建一个网格布局对象（QGridLayout）
        self.grid.setHorizontalSpacing(6) # 列与列之间的水平间距（单位：像素）。
        self.grid.setVerticalSpacing(4) #行与行之间的垂直间距（单位：像素）。
        # root.addLayout(self.grid)

        # -------------------------
        # 第一大行：左侧控制按钮 + 右侧扑克牌和数量
        # -------------------------
        first_row_layout = QHBoxLayout()
        first_row_layout.setContentsMargins(0, 0, 0, 10)  # 底部间距
        first_row_layout.setSpacing(2)
        self.root_layout.addLayout(first_row_layout)

        # 左侧控制按钮区域（垂直布局）
        left_controls_layout = QVBoxLayout()
        left_controls_layout.setContentsMargins(0, 0, 0, 0)
        left_controls_layout.setSpacing(10)
        first_row_layout.addLayout(left_controls_layout)

        # 设置按钮
        self.btn_settings = QPushButton("设置")
        self.btn_settings.setObjectName("BtnSettings")
        self.btn_settings.setFixedWidth(50)
        self.btn_settings.clicked.connect(self.on_settings_clicked)
        left_controls_layout.addWidget(self.btn_settings)

        # 暂停按钮
        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setObjectName("BtnPause")
        self.btn_pause.setFixedWidth(50)
        self.btn_pause.clicked.connect(self.on_pause_clicked)
        left_controls_layout.addWidget(self.btn_pause)

        # 初始化暂停状态
        self.is_paused = False

        # 右侧扑克牌和数量
        right_cards_layout = QVBoxLayout()
        right_cards_layout.setContentsMargins(0, 0, 0, 0)
        right_cards_layout.addLayout(self.grid)
        first_row_layout.addLayout(right_cards_layout, 1)  # 右侧占主要空间

        # -------------------------
        # 第二大行：显示上家、本家、下家的三行字符串
        # -------------------------
        self.second_row_layout = QVBoxLayout()
        self.second_row_layout.setContentsMargins(0, 0, 0, 0)
        self.second_row_layout.setSpacing(3)
        # 注意：这里不立即添加到root，而是在_update_played_cards_visibility中根据设置添加

        # 初始化出牌标签（从游戏配置动态生成）
        self.played_cards_labels: dict[str, QLabel] = {}
        for zone in settings.PLAYED_ZONES:
            key = zone["key"]
            label = zone["label"]
            lbl = QLabel(self.played_cards[key])
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            lbl.setObjectName("InfoLabel")
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
        self.worker = InferenceWorker(self.layout_name, game_name=settings.GAME_NAME)
        self.worker.result_ready.connect(self.on_result_ready)
        self.worker.error.connect(self.on_worker_error)
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
        self.btn_retry = QPushButton("重启识别", self)
        self.btn_retry.setToolTip("重新加载推理服务，并从头初始化记牌状态")
        self.btn_retry.clicked.connect(self.on_restart_clicked)
        self.statusBar().addPermanentWidget(self.btn_retry)
        self.btn_retry.hide()
        self.worker.start()

    def on_settings_clicked(self) -> None:
        """
        点击设置按钮时打开设置对话框
        """
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
            on_reset_callback=self.on_reset_clicked,
            on_interval_change_callback=self.on_interval_changed,
            on_layout_change_callback=self.on_layout_changed,
            on_device_change_callback=self.on_device_changed,
            on_reset_time_change_callback=self.on_reset_time_changed,
            on_frame_length_change_callback=self.on_frame_length_changed,
            on_always_on_top_change_callback=None,
            on_show_played_cards_change_callback=self.on_show_played_cards_changed,
            on_debug_mode_change_callback=self.on_debug_mode_changed,
            on_save_debug_images_change_callback=self.on_save_debug_images_changed,
            on_show_timing_change_callback=self.on_show_timing_changed,
            on_layout_editor_callback=self.on_layout_editor_clicked,
            on_layout_delete_callback=self.on_layout_delete_clicked,
            on_model_change_callback=self.on_model_changed,
            on_confidence_change_callback=self.on_confidence_changed,
            on_game_change_callback=self.on_game_changed,
        )

        self._settings_dialog = dialog
        dialog.set_current_always_on_top(settings.ALWAYS_ON_TOP)
        if settings.ALWAYS_ON_TOP:
            dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._refresh_open_settings()

        try:
            dialog.exec()
            # 对于 "显示在最上层" 我们在对话框关闭后统一应用，避免在 modal dialog 打开时改变 window flags
            try:
                always_index = dialog.combo_always_on_top.currentIndex()
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
        dialog.refresh_layout_list(selected_name=self.layout_name)

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

        # 设置当前调试模式
        from config.settings import DEBUG_MODE
        dialog.set_current_debug_mode(DEBUG_MODE)

        # 设置当前保存游戏图片
        from config.settings import SAVE_DEBUG_IMAGES
        dialog.set_current_save_debug_images(SAVE_DEBUG_IMAGES)

        # 设置当前显示耗时
        from config.settings import SHOW_TIMING
        dialog.set_current_show_timing(SHOW_TIMING)

        # 设置当前模型选择
        from config.settings import YOLO_MODEL_NAME
        dialog.set_current_model(YOLO_MODEL_NAME)

        # 设置当前游戏选择
        from config.settings import GAME_NAME
        dialog.set_current_game(GAME_NAME)

        # 设置当前置信度阈值
        from config.settings import YOLO_CONFIDENCE_THRESHOLD
        dialog.set_current_confidence(YOLO_CONFIDENCE_THRESHOLD)

    def on_layout_editor_clicked(self, settings_dialog: SettingsDialog) -> None:
        """打开可视化布局编辑器并刷新设置对话框中的布局列表。"""
        editor = LayoutEditorDialog(
            settings_dialog,
            initial_layout_name=self.layout_name,
            on_restore_topmost=lambda: self._reapply_topmost_if_enabled(settings_dialog),
            stay_on_top=settings.ALWAYS_ON_TOP,
        )
        if editor.exec() != editor.DialogCode.Accepted:
            return

        new_layout_name = editor.saved_layout_name
        if not new_layout_name:
            return

        should_set_current = bool(getattr(editor, "saved_set_current", True))
        if should_set_current or new_layout_name == self.layout_name:
            self._switch_layout_by_name(new_layout_name)

        QTimer.singleShot(0, lambda: settings_dialog.refresh_layout_list(selected_name=self.layout_name))

    def _switch_layout_by_name(self, layout_name: str) -> bool:
        """按布局名切换，避免设置窗口刷新后索引和内存列表不同步。"""
        layout_names = list(settings.WINDOW_LAYOUTS.keys())
        if layout_name not in layout_names:
            return False
        self.on_layout_changed(layout_names.index(layout_name))
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

    def on_pause_clicked(self):
        """
        点击暂停按钮时切换暂停/恢复状态
        """
        if self.is_paused:
            # 恢复检测
            self.btn_pause.setText("暂停")
            self.is_paused = False
            self._start_timer_if_allowed()

            # 更新最后检测时间，避免因为暂停时间过长而立即重置
            self._touch_no_target_time()

            print("检测已恢复")
        else:
            # 暂停检测
            self._stop_timer_if_exists()
            self.btn_pause.setText("恢复")
            self.is_paused = True
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
    def on_result_ready(self, remain_cards: dict, zone_cards: dict, inference_ms: float):
        """
        收到 worker 的识别结果：
        - 更新剩余牌数量
        - v <= 0 时样式变灰
        - v > 0 时样式恢复正常
        - 更新各区域出牌记录
        """
        # 计算整轮耗时（从本轮开始到收到结果）
        now = time.perf_counter()
        self._last_round_ms = (now - self._last_cycle_start) * 1000

        # 更新标题栏
        if settings.SHOW_TIMING:
            self.setWindowTitle(f"Han记牌器  ·  推理 {inference_ms:.0f}ms  ·  整轮 {self._last_round_ms:.0f}ms")
        else:
            self.setWindowTitle("Han记牌器")

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
                    lbl.setText(f"   {label}     " + trans_yolo_names_to_string(zone_cards.get(key, [])))
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
                label.setText(f"   {zone['label']}     ")

    def _ensure_widgets_attached(self):
        """
        Ensure all main widgets (name_labels, count_labels, played cards labels)
        are attached to their layouts/parents. This repairs UI if a native
        window flag change detached them.
        """
        # Ensure grid labels are present in the grid layout
        for col, card in enumerate(self.card_order):
            name = self.name_labels.get(card)
            cnt = self.count_labels.get(card)
            try:
                if name is not None and name.parent() is None:
                    self.grid.addWidget(name, 0, col)
                if cnt is not None and cnt.parent() is None:
                    self.grid.addWidget(cnt, 1, col)
            except Exception:
                pass

        # 如果 grid 中项数量不完整，重建 grid 布局（防止在 setWindowFlags 后 native layout 丢失）
        try:
            expected = len(self.card_order) * 2  # name + count per card
            actual = self.grid.count()
            if actual < expected:
                # 清理 grid 中残留项
                while self.grid.count() > 0:
                    item = self.grid.takeAt(0)
                    if item is None:
                        break
                # 重新添加所有 card widgets
                for col, card in enumerate(self.card_order):
                    name = self.name_labels.get(card)
                    cnt = self.count_labels.get(card)
                    try:
                        if name is not None:
                            self.grid.addWidget(name, 0, col)
                        if cnt is not None:
                            self.grid.addWidget(cnt, 1, col)
                    except Exception:
                        pass
        except Exception:
            pass

        # Ensure played cards labels are present in second_row_layout when visible
        if self._show_played_cards:
            for key, lbl in self.played_cards_labels.items():
                try:
                    if lbl.parent() is None:
                        self.second_row_layout.addWidget(lbl)
                        lbl.setVisible(True)
                except Exception:
                    pass
        else:
            for key, lbl in self.played_cards_labels.items():
                try:
                    lbl.setVisible(False)
                except Exception:
                    pass
        # Force a geometry and repaint pass
        try:
            self.central_widget.updateGeometry()
            self.central_widget.update()
            self.central_widget.repaint()
            self.adjustSize()
        except Exception:
            pass
        # Debug: log how many children and layout items exist
        try:
            if settings.DEBUG_MODE:
                print(f"[DEBUG] central_widget children: {len(self.central_widget.children())}, grid count: {self.grid.count()}, root_layout count: {self.root_layout.count()}")
        except Exception:
            pass

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

    def on_layout_changed(self, index: int) -> None:
        """提交完整布局，执行成功后才确认选择并清空牌局。

        Args:
            index: 布局下拉列表索引。
        """
        names = list(settings.WINDOW_LAYOUTS)
        if not 0 <= index < len(names):
            return
        name = names[index]
        self.settings_controller.apply(
            save=lambda: settings.save_current_layout(name),
            request=lambda: self.worker.switch_layout(name),
            after=lambda: self._apply_layout_selection(name),
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
        """公共函数：统一应用主窗口与设置窗口的置顶状态。"""

        # 简化：直接切换 flag，然后确保 widgets 已连接并刷新界面
        try:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, always_on_top)
        except Exception:
            try:
                # 兜底
                self.setWindowFlags(self.windowFlags())
            except Exception:
                pass

        # 确保控件仍然附着在布局上（并打印调试信息）
        self._ensure_widgets_attached()

        # 重新显示并提升
        try:
            self.show()
            self.raise_()
        except Exception:
            pass

        if settings_dialog is not None:
            try:
                settings_dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, always_on_top)
                settings_dialog.show()
                settings_dialog.raise_()
            except Exception:
                pass

        # 强制重绘
        try:
            self.central_widget.update()
            self.central_widget.repaint()
            self.update()
            self.repaint()
            self.adjustSize()
        except Exception:
            pass

    def _reapply_topmost_if_enabled(self, settings_dialog):
        """自动下沉截图后：若设置为置顶，则复用同一逻辑恢复置顶。"""
        from config.settings import ALWAYS_ON_TOP

        if ALWAYS_ON_TOP:
            self._apply_always_on_top_state(True, settings_dialog=settings_dialog)

    def on_show_played_cards_changed(self, index: int) -> None:
        """保存成功后切换出牌记录显示。

        Args:
            index: 否/是选项索引。
        """
        if index in (0, 1):
            self._apply_setting("show_played_cards", bool(index), after=self._apply_played_visibility)

    def on_debug_mode_changed(self, index: int) -> None:
        """同步调试日志开关。

        Args:
            index: 否/是选项索引。
        """
        if index in (0, 1):
            self._apply_setting("debug_mode", bool(index), "DEBUG_MODE")

    def on_save_debug_images_changed(self, index: int) -> None:
        """同步游戏图片保存开关。

        Args:
            index: 否/是选项索引。
        """
        if index in (0, 1):
            self._apply_setting("save_debug_images", bool(index), "SAVE_DEBUG_IMAGES")

    def on_game_changed(self, index: int) -> None:
        """保持当前斗地主功能范围，只接受已注册的斗地主选项。

        Args:
            index: 游戏列表索引。
        """
        names = settings._scan_game_configs()
        if 0 <= index < len(names) and names[index] != settings.GAME_NAME:
            self._on_settings_error("当前版本仅支持斗地主。")

    def on_show_timing_changed(self, index: int) -> None:
        """保存并刷新耗时显示开关。

        Args:
            index: 否/是选项索引。
        """
        if index in (0, 1):
            self._apply_setting("show_timing", bool(index), after=self._apply_timing_visibility)

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

    def _apply_timing_visibility(self) -> None:
        """刷新耗时标题，故障状态保持可见。"""
        if self.worker.state == "failed":
            return
        self.setWindowTitle("Han记牌器  ·  推理 --ms  ·  整轮 --ms" if settings.SHOW_TIMING else "Han记牌器")

    def _on_settings_error(self, message: str) -> None:
        """设置失败时恢复已确认的控件值并显示原因。

        Args:
            message: 错误详情。
        """
        self._refresh_open_settings()
        QMessageBox.warning(self._settings_dialog or self, "设置未生效", message)

    @Slot(str)
    def on_worker_state_changed(self, state: str) -> None:
        """根据生命周期控制定时器与恢复入口。

        Args:
            state: 推理服务状态。
        """
        self._busy = False
        self.btn_retry.setVisible(state == "failed")
        if state == "ready":
            self.statusBar().clearMessage()
            self.statusBar().setToolTip("")
            self._apply_timing_visibility()
            self._start_timer_if_allowed()
        else:
            self.timer.stop()
            labels = {"starting": "正在加载模型…", "failed": "识别已停止，请重启识别", "stopped": "识别已停止"}
            self.statusBar().showMessage(labels.get(state, state))
            self.setWindowTitle("Han记牌器  ·  " + labels.get(state, state))

    @Slot()
    def on_restart_clicked(self) -> None:
        """重启推理服务并重新初始化牌局显示。"""
        self._busy = False
        self._reset_ui_to_total()
        self.worker.restart()

    def _update_played_cards_visibility(self) -> None:
        """
        根据设置更新玩家所出的牌的可见性
        """
        visible = self._show_played_cards

        if visible:
            # 检查second_row_layout是否已经在root_layout中
            if self.second_row_layout.parent() is None:
                self.root_layout.addLayout(self.second_row_layout)

            for key, lbl in self.played_cards_labels.items():
                if lbl.parent() is None:
                    self.second_row_layout.addWidget(lbl)
                lbl.setVisible(True)
                lbl.setMaximumHeight(16777215)
                lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        else:
            # 隐藏并从布局移除标签（不要 setParent(None)，否则控件会脱离父窗口并可能无法恢复）
            for key, lbl in self.played_cards_labels.items():
                try:
                    self.second_row_layout.removeWidget(lbl)
                except Exception:
                    pass
                lbl.setVisible(False)
            # 不要移除 second_row_layout 的父关系，保留布局对象以便再次添加时能稳定工作

        # 强制窗口调整大小
        self.adjustSize()

        # 强制更新窗口几何形状
        self.central_widget.updateGeometry()
        self.root_layout.update()

        # 使用更强制的方法调整窗口大小
        size_hint = self.central_widget.sizeHint()

        # 调整为合适大小（不要使用 setFixedSize，会导致后续更改窗口标志或重绘时出现空白）
        # 使用 resize 让窗口适应内容同时保持可调整
        # 保证不会变得比最小尺寸还小，避免窗口内容被压扁成空白
        new_w = max(size_hint.width(), 550)
        new_h = max(size_hint.height(), 100)
        self.resize(new_w, new_h)
        # 保持合适的最小尺寸限制
        self.setMinimumSize(550, 100)
        self.setMaximumSize(16777215, 16777215)





    @Slot(str)
    def on_worker_error(self, err_text: str) -> None:
        """显示推理错误，保留详情供排查。

        Args:
            err_text: 错误详情。
        """
        print("Worker error:\n", err_text)
        message = err_text.strip().splitlines()[-1] if err_text.strip() else "识别失败"
        self.statusBar().showMessage(message)
        self.statusBar().setToolTip(err_text)

    @Slot()
    def on_worker_finished(self):
        """
        worker 一轮结束（保持你原逻辑）：
        - 解除 busy，允许下一轮定时触发
        """
        self._busy = False

    def closeEvent(self, event):
        """
        窗口关闭时清理：
        - 停止 timer
        - 停止子进程
        """
        self.timer.stop()
        if hasattr(self, 'worker'):
            self.worker.stop()
        super().closeEvent(event)
