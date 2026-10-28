import sys
from collections.abc import Callable
from PIL import Image
from typing import Dict, Optional, Tuple

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)

from config import settings

from utils.layout_editor.coord import REGION_KEYS, denormalize_rect, normalize_layout, normalize_rect, sanitize_pixel_rect
from utils.layout_editor.service import (
    capture_window_by_title,
    get_layout_config,
    list_visible_window_titles,
    save_layout,
)
from utils.layout_editor.validator import validate_normalized_layout
from utils.layout_editor.reference_store import load_reference_image, save_reference_image

REGION_NAME_CN = {
    "player_hand": "玩家手牌",
    "player_played": "本家出牌",
    "opponent_left": "上家出牌",
    "opponent_right": "下家出牌",
    "landlord_cards": "地主底牌",
}


class RectCanvas(QLabel):
    """显示截图并框选牌区的画布。"""

    rect_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化画布。

        Args:
            parent: 父控件。
        """
        super().__init__(parent)
        self._base_pixmap: Optional[QPixmap] = None
        self._display_scale = 1.0
        self._active_key = REGION_KEYS[0]
        self._rects: Dict[str, Tuple[int, int, int, int]] = {}
        self._drawing = False
        self._start = QPoint()
        self._current = QPoint()
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setMouseTracking(True)

    @property
    def image_size(self) -> tuple[int, int]:
        """返回原始截图像素尺寸。

        Returns:
            宽和高；未截图时为(0, 0)。
        """
        if not self._base_pixmap:
            return 0, 0
        return self._base_pixmap.width(), self._base_pixmap.height()

    def set_active_key(self, key: str):
        self._active_key = key
        self._render()

    def set_pixmap_from_pil(self, pil_image: Image.Image, viewport_size: QSize | None = None) -> None:
        """载入原图并按已有视口缩放，不让大图先撑大编辑器。

        Args:
            pil_image: 原始截图，框选坐标始终以此像素尺寸为准。
            viewport_size: 当前可用视口尺寸；省略时沿用原始比例。
        """
        qimage = ImageQt(pil_image.convert("RGB"))
        self._base_pixmap = QPixmap.fromImage(qimage)
        if viewport_size is not None:
            self.fit_to_viewport(viewport_size.width(), viewport_size.height())
        else:
            self._apply_scale(1.0)
            self._render()

    def fit_to_viewport(self, viewport_width: int, viewport_height: int):
        if not self._base_pixmap:
            return
        img_w, img_h = self.image_size
        if img_w <= 0 or img_h <= 0:
            return
        # 留出少量边界，避免因滚动条/边框计算差异导致 1px 溢出。
        view_w = max(1, int(viewport_width) - 8)
        view_h = max(1, int(viewport_height) - 8)
        scale = min(1.0, view_w / float(img_w), view_h / float(img_h))
        self._apply_scale(scale)
        # 关键：仅改 fixedSize 不会自动缩放 QLabel 内 pixmap，必须重绘。
        self._render()

    def _apply_scale(self, scale: float):
        if not self._base_pixmap:
            return
        # 允许更小缩放，避免超大截图在小窗口下仍超出可视区。
        self._display_scale = max(0.01, min(1.0, float(scale)))
        # 使用下取整，确保目标尺寸不会因为 round 上溢出可视区。
        target_w = max(1, int(self._base_pixmap.width() * self._display_scale))
        target_h = max(1, int(self._base_pixmap.height() * self._display_scale))
        self.setFixedSize(target_w, target_h)

    def _widget_point_to_image_point(self, p: QPoint) -> QPoint:
        """按实际显示尺寸映射坐标，右下边界允许到width/height。

        Args:
            p: 画布坐标。
        Returns:
            原始截图中的框选边界坐标。
        """
        width, height = self.image_size
        if not self._base_pixmap or self.width() <= 0 or self.height() <= 0:
            return QPoint(p)
        return QPoint(max(0, min(width, round(p.x()*width/self.width()))),
                      max(0, min(height, round(p.y()*height/self.height()))))

    def clear_rects(self):
        self._rects = {}
        self._render()

    def set_rects(self, rects: Dict[str, Tuple[int, int, int, int]]):
        self._rects = dict(rects)
        self._render()

    def get_rects(self):
        return dict(self._rects)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """开始框选当前牌区。

        Args:
            event: 鼠标事件。
        """
        if not self._base_pixmap or event.button() != Qt.MouseButton.LeftButton:
            return
        self._drawing = True
        self._start = self._widget_point_to_image_point(event.position().toPoint())
        self._current = self._start
        self._render()

    def mouseMoveEvent(self, event):
        if not self._base_pixmap or not self._drawing:
            return
        self._current = self._widget_point_to_image_point(event.position().toPoint())
        self._render()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """完成框选当前牌区。

        Args:
            event: 鼠标事件。
        """
        if not self._base_pixmap or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._drawing:
            self._drawing = False
            self._current = self._widget_point_to_image_point(event.position().toPoint())
            w, h = self.image_size
            # 单击或极小拖动不覆盖已有的大区域。
            if abs(self._current.x()-self._start.x())*self._display_scale >= 3 and abs(self._current.y()-self._start.y())*self._display_scale >= 3:
                rect = sanitize_pixel_rect((self._start.x(), self._start.y(), self._current.x(), self._current.y()), w, h)
                self._rects[self._active_key] = rect
                self.rect_changed.emit(self._active_key)
            self._render()

    def _render(self) -> None:
        """按当前缩放绘制截图和区域边框。"""
        if not self._base_pixmap:
            self.setPixmap(QPixmap())
            return

        if self._display_scale < 0.999:
            target_w = max(1, int(self._base_pixmap.width() * self._display_scale))
            target_h = max(1, int(self._base_pixmap.height() * self._display_scale))
            pixmap = self._base_pixmap.scaled(target_w, target_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        else:
            pixmap = QPixmap(self._base_pixmap)

        scale_x = pixmap.width() / float(self._base_pixmap.width())
        scale_y = pixmap.height() / float(self._base_pixmap.height())
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        for key, rect in self._rects.items():
            color = Qt.GlobalColor.yellow if key == self._active_key else Qt.GlobalColor.green
            pen = QPen(color)
            pen.setWidth(2)
            painter.setPen(pen)
            x1, y1, x2, y2 = rect
            sx1 = int(round(x1 * scale_x))
            sy1 = int(round(y1 * scale_y))
            sx2 = int(round(x2 * scale_x))
            sy2 = int(round(y2 * scale_y))
            painter.drawRect(sx1, sy1, max(1, sx2 - sx1), max(1, sy2 - sy1))
            painter.drawText(sx1 + 3, max(12, sy1 - 3), REGION_NAME_CN.get(key, key))

        if self._drawing:
            pen = QPen(Qt.GlobalColor.red)
            pen.setWidth(2)
            painter.setPen(pen)
            w, h = self.image_size
            x1, y1, x2, y2 = sanitize_pixel_rect(
                (self._start.x(), self._start.y(), self._current.x(), self._current.y()),
                w,
                h,
            )
            sx1 = int(round(x1 * scale_x))
            sy1 = int(round(y1 * scale_y))
            sx2 = int(round(x2 * scale_x))
            sy2 = int(round(y2 * scale_y))
            painter.drawRect(sx1, sy1, max(1, sx2 - sx1), max(1, sy2 - sy1))

        painter.end()
        self.setPixmap(pixmap)


class LayoutEditorDialog(QDialog):
    """截图、编辑并保存斗地主牌区布局。

    Attributes:
        saved_layout_name: 成功保存的布局名称。
        saved_set_current: 保存后是否请求主窗口切换。
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        initial_layout_name: str | None = None,
        on_restore_topmost: Callable[[], None] | None = None,
        stay_on_top: bool = False,
    ) -> None:
        """创建编辑窗口。

        Args:
            parent: 父控件。
            initial_layout_name: 待编辑的已有布局。
            on_restore_topmost: 截图后恢复置顶的回调。
            stay_on_top: 是否置顶。
        """
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, stay_on_top)
        self.setObjectName("LayoutEditorDialog")
        self.setWindowTitle("编辑布局" if initial_layout_name else "新建布局")
        self.setMinimumSize(860, 560)
        self.resize(920, 620)

        self.saved_layout_name: Optional[str] = None
        self.saved_set_current = False
        self._editing_layout_name = initial_layout_name
        self._captured_window_title: str | None = None
        self._reference_warning = ''
        self._dirty = False
        self._saving = False
        self._captured_image: Image.Image | None = None
        self._lowered_windows: list[QWidget] = []
        self._window_restore_states: list[tuple[QWidget, QPoint, QSize]] = []
        self._windows_pushed_back = False
        self._capturing = False
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_canvas_to_viewport)
        self._on_restore_topmost = on_restore_topmost
        self._stay_on_top = stay_on_top

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)
        instruction = QLabel('编辑已有布局会载入上次截图与已保存的五区。重新截图会替换参考图，并保留同一窗口的框选。')
        instruction.setObjectName('LayoutEditorHint')
        instruction.setWordWrap(True)
        root.addWidget(instruction)

        top = QGridLayout()
        top.addWidget(QLabel("布局名称:"), 0, 0)
        new_name = '新布局'
        number = 2
        while new_name in settings.WINDOW_LAYOUTS:
            new_name = f'新布局 {number}'
            number += 1
        self.edit_layout_name = QLineEdit(initial_layout_name or new_name)
        self.edit_layout_name.setPlaceholderText('输入布局名称')
        top.addWidget(self.edit_layout_name, 0, 1)

        top.addWidget(QLabel("窗口标题:"), 1, 0)
        self.combo_window_title = QComboBox()
        self.btn_refresh_titles = QPushButton("刷新窗口")
        self.btn_capture = QPushButton("截取画面")
        top.addWidget(self.combo_window_title, 1, 1)
        top.addWidget(self.btn_refresh_titles, 1, 2)
        top.addWidget(self.btn_capture, 1, 3)

        root.addLayout(top)

        content = QHBoxLayout()
        root.addLayout(content, 1)

        self.canvas = RectCanvas()
        self.canvas_scroll = QScrollArea()
        self.canvas_scroll.setWidgetResizable(False)
        self.canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas_scroll.setWidget(self.canvas)
        self.canvas_scroll.viewport().installEventFilter(self)
        content.addWidget(self.canvas_scroll, 3)

        right_panel = QVBoxLayout()
        content.addLayout(right_panel, 1)

        group = QGroupBox("牌区设置")
        group.setMinimumWidth(220)
        group.setMaximumWidth(300)
        group_layout = QVBoxLayout(group)

        self.combo_region = QComboBox()
        for key in REGION_KEYS:
            self.combo_region.addItem(REGION_NAME_CN[key], key)
        group_layout.addWidget(self.combo_region)
        self.lbl_progress = QLabel('已框选 0 / 5')
        group_layout.addWidget(self.lbl_progress)

        self.lbl_pixel = QLabel("像素: -")
        self.lbl_norm = QLabel("归一化: -")
        self.lbl_norm.setWordWrap(True)
        group_layout.addWidget(self.lbl_pixel)
        group_layout.addWidget(self.lbl_norm)

        self.btn_clear_region = QPushButton("清除当前区域")
        group_layout.addWidget(self.btn_clear_region)
        right_panel.addWidget(group)
        self.lbl_editor_status = QLabel('请先截取目标窗口画面。')
        self.lbl_editor_status.setObjectName('LayoutEditorHint')
        self.lbl_editor_status.setWordWrap(True)
        right_panel.addWidget(self.lbl_editor_status)

        self.chk_set_current = QCheckBox("保存后切换为当前布局")
        self.chk_set_current.setChecked(True)
        self.chk_set_current.setToolTip("仅保存新布局时可取消；编辑当前正在使用的同名布局仍会同步更新坐标。")
        right_panel.addWidget(self.chk_set_current)

        right_panel.addStretch()

        bottom = QHBoxLayout()
        self.btn_save = QPushButton("保存布局")
        self.btn_save.setObjectName("LayoutEditorSave")
        self.btn_cancel = QPushButton("取消")
        bottom.addStretch()
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.btn_cancel)
        root.addLayout(bottom)

        self.btn_refresh_titles.clicked.connect(self._reload_window_titles)
        self.btn_capture.clicked.connect(self._capture_window)
        self.combo_region.currentIndexChanged.connect(self._on_region_changed)
        self.canvas.rect_changed.connect(self._on_canvas_rect_changed)
        self.btn_clear_region.clicked.connect(self._clear_current_region)
        self.btn_save.clicked.connect(self._save_layout)
        self.btn_cancel.clicked.connect(self.reject)

        self._reload_window_titles()
        if initial_layout_name:
            self._try_prefill_from_existing(initial_layout_name)
        self.combo_window_title.currentTextChanged.connect(self._on_target_changed)
        self.edit_layout_name.textEdited.connect(self._mark_name_changed)
        self._on_region_changed(self.combo_region.currentIndex())

    def _reload_window_titles(self) -> None:
        """刷新目标窗口列表，保留所选标题，不静默跳到另一个窗口。"""
        current = self.combo_window_title.currentText()
        try:
            own_titles = {widget.windowTitle() for widget in QApplication.topLevelWidgets()}
            titles = [title for title in list_visible_window_titles() if title not in own_titles]
        except Exception as exc:
            QMessageBox.warning(self, '读取窗口失败', str(exc))
            return
        if current and current not in titles:
            titles.append(current)
        self.combo_window_title.blockSignals(True)
        try:
            self.combo_window_title.clear()
            self.combo_window_title.addItems(titles)
            if current:
                self.combo_window_title.setCurrentText(current)
        finally:
            self.combo_window_title.blockSignals(False)

    def _try_prefill_from_existing(self, layout_name: str) -> None:
        """恢复布局目标、上次参考图及已保存的五个区域，目标窗口可以尚未打开。

        Args:
            layout_name: 明确指定的已保存布局名称。
        """
        cfg = get_layout_config(layout_name)
        if cfg:
            title, _ = cfg
            if title:
                if self.combo_window_title.findText(title) < 0:
                    self.combo_window_title.addItem(title)
                self.combo_window_title.setCurrentText(title)
                try:
                    image = load_reference_image(layout_name, title)
                    if image is not None:
                        self._show_reference_image(image, title, cfg[1] if isinstance(cfg[1], dict) else {})
                except (OSError, ValueError) as exc:
                    self._reference_warning = f'参考图无法读取，请重新截图：{exc}'

    def _on_target_changed(self, title: str) -> None:
        """目标切换后禁止将上一窗口截图误存到新标题。

        Args:
            title: 新选中的窗口标题。
        """
        if self._captured_image is not None and title != self._captured_window_title:
            self._dirty = True
        self._sync_region_labels()

    def _mark_name_changed(self, name: str) -> None:
        """记录用户对名称的编辑。

        Args:
            name: 输入框文本。
        """
        self._dirty = True

    def _send_app_windows_to_bottom(self) -> None:
        """截图前把本应用窗口临时移出屏幕，避免遮挡目标窗口。"""
        if self._windows_pushed_back:
            return
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return

        screen = self.screen() or app.primaryScreen()
        if screen is not None:
            virtual_geometry = screen.virtualGeometry()
            offscreen_x = virtual_geometry.left() - 10000
            offscreen_y = virtual_geometry.top() - 10000
        else:
            offscreen_x = -10000
            offscreen_y = -10000

        windows = [w for w in app.topLevelWidgets() if w is not None and w.isVisible()]
        self._lowered_windows = windows
        self._window_restore_states = [(w, w.pos(), w.size()) for w in windows]

        self._windows_pushed_back = True
        for index, w in enumerate(windows):
            try:
                self._move_window_no_activate(w, offscreen_x + index * 20, offscreen_y + index * 20)
            except Exception:
                pass

        app.processEvents()
        self._wait_for_desktop_to_settle()

    def _restore_app_windows_after_capture(self) -> None:
        """截图后恢复本应用窗口位置。"""
        if not self._windows_pushed_back:
            return

        app = QApplication.instance()
        restore_states = self._window_restore_states
        self._windows_pushed_back = False
        self._window_restore_states = []
        for w, position, size in restore_states:
            try:
                if w is not None:
                    self._move_window_no_activate(
                        w,
                        position.x(),
                        position.y(),
                        size.width(),
                        size.height(),
                        resize=True,
                    )
            except Exception:
                pass

        try:
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

        if app is not None:
            app.processEvents()

        self._windows_pushed_back = False
        self._lowered_windows = []
        self._window_restore_states = []

    def _move_window_no_activate(
        self, widget: QWidget, x: int, y: int, width: int = 0, height: int = 0,
        resize: bool = False,
    ) -> None:
        """统一使用 Qt 逻辑坐标移动窗口，避免高 DPI 下混入原生像素。

        Args:
            widget: 需要移动的窗口。
            x: Qt 窗口位置横坐标。
            y: Qt 窗口位置纵坐标。
            width: 恢复时的客户区宽度，不含窗口边框。
            height: 恢复时的客户区高度，不含窗口边框。
            resize: 是否同时恢复客户区尺寸。
        """
        widget.move(x, y)
        if resize:
            widget.resize(width, height)

    def _wait_for_desktop_to_settle(self):
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.dwmapi.DwmFlush()
            except Exception:
                pass

        try:
            import time

            time.sleep(0.25)
        except Exception:
            pass

        if app is not None:
            app.processEvents()

    def _fit_canvas_to_viewport(self) -> None:
        """按当前视口缩放画布；使用随编辑器销毁的定时器调度。"""
        if self._captured_image is None:
            return
        viewport = self.canvas_scroll.viewport().size()
        self.canvas.fit_to_viewport(viewport.width(), viewport.height())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """视口变化时安排画布缩放。

        Args:
            watched: 被观察对象。
            event: Qt 事件。

        Returns:
            父类事件过滤结果。
        """
        if watched is self.canvas_scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_timer.start(0)
        return super().eventFilter(watched, event)

    def _confirm_discard(self) -> bool:
        """有未保存修改时确认是否放弃。

        Returns:
            可以退出时为True。
        """
        if not self._dirty:
            return True
        answer = QMessageBox.question(self, '未保存修改', '放弃当前未保存的布局修改？',
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._dirty = False
        return True

    def reject(self) -> None:
        """取消、Esc和关闭统一保护未保存的框选；截图处理中不重入退出。"""
        if not self._capturing and self._confirm_discard():
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭前恢复窗口并保护尚未保存的编辑。

        Args:
            event: Qt关闭事件。
        """
        if self._capturing or not self._confirm_discard():
            event.ignore()
            return
        self._restore_app_windows_after_capture()
        super().closeEvent(event)

    def _capture_window(self) -> None:
        """截图并在失败时恢复所有窗口，防止事件处理期间重复进入。"""
        if self._capturing:
            return
        window_title = self.combo_window_title.currentText().strip()
        if not window_title:
            QMessageBox.warning(self, "提示", "请先选择窗口标题")
            return

        self._capturing = True
        self.btn_capture.setEnabled(False)
        try:
            self._send_app_windows_to_bottom()
            image = capture_window_by_title(window_title)
        except Exception as exc:
            self._restore_app_windows_after_capture()
            QMessageBox.warning(self, "截图失败", str(exc))
            return
        finally:
            self._restore_app_windows_after_capture()
            self._capturing = False
            self.btn_capture.setEnabled(True)

        if image is None:
            QMessageBox.warning(self, "提示", "截图失败，请确认窗口可见且标题正确")
            return

        # 同一窗口重截图保留草稿，尺寸变化时按归一化比例映射。
        normalized: dict[str, tuple[float, float, float, float]] = {}
        if self._captured_image is not None and self._captured_window_title == window_title:
            old_w, old_h = self.canvas.image_size
            normalized = {key: normalize_rect(rect, old_w, old_h) for key, rect in self.canvas.get_rects().items()}
        elif self._captured_image is None:
            cfg = get_layout_config(self._editing_layout_name or '')
            if cfg and cfg[0] == window_title and isinstance(cfg[1], dict):
                normalized = dict(cfg[1])
        self._show_reference_image(image, window_title, normalized)
        # 已有布局的同一目标立即记住新截图；新名称在成功保存布局后建立关联。
        cfg = get_layout_config(self._editing_layout_name or '')
        if self._editing_layout_name and cfg and cfg[0] == window_title:
            self._persist_reference_image(self._editing_layout_name)
        self._sync_region_labels()

    def _show_reference_image(self, image: Image.Image, window_title: str,
                              normalized: dict[str, tuple[float, float, float, float]]) -> None:
        """在现有编辑器内显示参考图并恢复原图坐标框，不改变外窗尺寸或位置。

        Args:
            image: 新截图或从本机缓存载入的原始图。
            window_title: 与图像绑定的目标窗口。
            normalized: 已保存区域或同窗重截图前的草稿区域。
        """
        self._captured_image = image
        self._captured_window_title = window_title
        self._reference_warning = ''
        self.canvas.clear_rects()
        self.canvas.set_pixmap_from_pil(image, self.canvas_scroll.viewport().size())
        width, height = image.size
        restored: dict[str, tuple[int, int, int, int]] = {}
        for key in REGION_KEYS:
            if key in normalized:
                try:
                    restored[key] = denormalize_rect(normalized[key], width, height)
                except (TypeError, ValueError):
                    # 损坏的旧区域留作未框选，允许用户重新修正，不能阻塞编辑器。
                    continue
        self.canvas.set_rects(restored)
        self._fit_timer.start(0)
        self._sync_region_labels()

    def _persist_reference_image(self, layout_name: str) -> None:
        """缓存最新参考图，失败只提示原因，保留可继续编辑的当前图片和草稿。

        Args:
            layout_name: 参考图归属的布局名称。
        """
        if self._captured_image is None or not self._captured_window_title:
            return
        try:
            save_reference_image(layout_name, self._captured_window_title, self._captured_image)
            self._reference_warning = ''
        except (OSError, ValueError) as exc:
            self._reference_warning = f'参考图缓存失败，下次可能需要重新截图：{exc}'

    def _current_region_key(self) -> str:
        """返回当前选中的牌区键名。"""
        return self.combo_region.currentData()

    def _on_region_changed(self, _index: int) -> None:
        """切换当前绘制牌区。

        Args:
            _index: 区域选项索引。
        """
        self.canvas.set_active_key(self._current_region_key())
        self._sync_region_labels()

    def _on_canvas_rect_changed(self, _key: str) -> None:
        """框选改变后更新完成度。

        Args:
            _key: 改变的牌区键名。
        """
        self._dirty = True
        self._sync_region_labels()

    def _clear_current_region(self) -> None:
        """清除当前区域，其他区域保持不变。"""
        key = self._current_region_key()
        rects = self.canvas.get_rects()
        if key in rects:
            self._dirty = True
            del rects[key]
            self.canvas.set_rects(rects)
        self._sync_region_labels()

    def _sync_region_labels(self) -> None:
        """同步区域完成度、操作可用性及截图目标一致性。"""
        rects = self.canvas.get_rects()
        self.lbl_progress.setText(f'已框选 {len(rects)} / {len(REGION_KEYS)}')
        for index, region in enumerate(REGION_KEYS):
            self.combo_region.setItemText(index, ('✓ ' if region in rects else '○ ')+REGION_NAME_CN[region])
        captured = self._captured_image is not None
        matches = captured and self.combo_window_title.currentText().strip() == self._captured_window_title
        complete = matches and all(region in rects for region in REGION_KEYS)
        self.btn_save.setEnabled(bool(complete) and not self._saving)
        self.btn_clear_region.setEnabled(bool(matches))
        self.canvas.setEnabled(bool(matches))
        if captured and not matches:
            hint = '目标窗口已变化，请重新截取画面。'
        elif complete:
            hint = '五个牌区已就绪，可直接保存；编辑原布局将覆盖同名配置。'
        elif captured:
            hint = '选择尚未完成的牌区，在画面上拖动框选。'
        else:
            hint = '请先截取目标窗口画面。'
        self.lbl_editor_status.setText(hint + ('\n' + self._reference_warning if self._reference_warning else ''))
        key = self._current_region_key()
        rects = self.canvas.get_rects()
        rect = rects.get(key)
        if not rect or not self._captured_image:
            self.lbl_pixel.setText("像素: -")
            self.lbl_norm.setText("归一化: -")
            return

        x1, y1, x2, y2 = rect
        self.lbl_pixel.setText(f"像素: ({x1}, {y1}, {x2}, {y2})")

        w, h = self.canvas.image_size
        norm = normalize_rect(rect, w, h)
        self.lbl_norm.setText(
            "归一化: ({:.4f}, {:.4f}, {:.4f}, {:.4f})".format(*norm)
        )

    def _build_normalized_layout(self) -> tuple[dict[str, tuple[float, float, float, float]] | None, str | None]:
        """校验五个区域及截图目标，生成归一化布局。

        Returns:
            合法布局及空错误，或None和错误原因。
        """
        if self._captured_image is None:
            return None, "请先截图"

        if self.combo_window_title.currentText().strip() != self._captured_window_title:
            return None, '目标窗口已更改，请重新截图后保存'
        rects = self.canvas.get_rects()
        missing = [REGION_NAME_CN[k] for k in REGION_KEYS if k not in rects]
        if missing:
            return None, "缺少区域: " + "、".join(missing)

        w, h = self.canvas.image_size
        normalized = normalize_layout(rects, w, h)
        ok, msg = validate_normalized_layout(normalized)
        if not ok:
            return None, msg
        return normalized, None

    def _save_layout(self) -> None:
        """原子保存布局；同名编辑直接覆盖，当前布局由主窗口重新提交后端。"""
        if self._saving or self._capturing:
            return
        layout_name = self.edit_layout_name.text().strip()
        window_title = self.combo_window_title.currentText().strip()
        if not layout_name or not window_title:
            QMessageBox.warning(self, '无法保存', '请填写布局名称并选择目标窗口。')
            return
        normalized, error = self._build_normalized_layout()
        if error or normalized is None:
            QMessageBox.warning(self, '无法保存', error or '布局无效')
            return
        if layout_name in settings.WINDOW_LAYOUTS and layout_name != self._editing_layout_name:
            answer = QMessageBox.question(self, '覆盖已有布局', f'布局“{layout_name}”已存在，是否覆盖？',
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._saving = True
        self.btn_save.setEnabled(False)
        try:
            save_layout(layout_name, window_title, normalized, False)
        except Exception as exc:
            QMessageBox.critical(self, '保存失败', str(exc))
            return
        finally:
            self._saving = False
            self._sync_region_labels()
        self.saved_layout_name = layout_name
        self.saved_set_current = self.chk_set_current.isChecked()
        self._persist_reference_image(layout_name)
        if self._reference_warning:
            QMessageBox.warning(self, '布局已保存', self._reference_warning)
        self._dirty = False
        self.accept()
