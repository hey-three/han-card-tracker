"""YOLO 实际输入预览窗口，兼容旧区域标注接口。"""

from collections.abc import Mapping

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QResizeEvent, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QSizePolicy, QVBoxLayout, QWidget
from core.preview_regions import PACKED_PREVIEW_PREFIX, SEARCH_PREVIEW_PREFIX
from ui.window_metrics import COMPACT_WINDOW_WIDTH


class ScreenshotPreviewLabel(QLabel):
    """等比例呈现实际输入，原图画牌区框，拼图裁片仅标名称。

    Attributes:
        REGION_COLORS: 区域键到稳定标注颜色的映射。
    """

    REGION_COLORS: dict[str, QColor] = {
        "player_hand": QColor("#31d17c"),
        "landlord_cards": QColor("#ffd43b"),
        "player_played": QColor("#35c9ff"),
        "opponent_left": QColor("#ff9638"),
        "opponent_right": QColor("#ec68ff"),
    }
    REGION_LABELS = {'opponent_left': '上', 'opponent_right': '下', 'player_played': '本',
                     'landlord_cards': '底', 'player_hand': '手'}

    def __init__(self, parent: QDialog) -> None:
        """创建用于呈现截图的标签。"""
        super().__init__(parent)
        self._source_pixmap = QPixmap()
        self._regions: dict[str, tuple[float, float, float, float]] = {}
        self._region_labels: dict[str, str] = {}
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setScaledContents(False)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setText("等待 YOLO 输入…")

    def set_image(
        self,
        image_data: bytes,
        regions: Mapping[str, tuple[float, float, float, float]] | None = None,
        region_labels: Mapping[str, str] | None = None,
    ) -> None:
        """保留未标注的原始JPEG副本，标注只在当前预览尺寸上绘制。

        Args:
            image_data: 推理进程传来的 JPEG 编码截图。
            regions: 原图牌区或带packed前缀的裁片归一化边界。
            region_labels: 区域键到可读名称的映射。
        """
        image = QImage()
        if not image.loadFromData(image_data):
            self.clear_image('截图读取失败')
            return
        self._source_pixmap = QPixmap.fromImage(image)
        self._regions = dict(regions or {})
        self._region_labels = dict(region_labels or {})
        self._refresh_pixmap()

    def _draw_regions(self, pixmap: QPixmap) -> None:
        """按显示坐标画标注，保证缩放后文字清楚且不会写入模型输入。

        Args:
            pixmap: 与原图等比例的独立显示副本。
        """
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont('Microsoft YaHei')
        font.setPixelSize(13)
        font.setBold(True)
        painter.setFont(font)
        for key, bounds in self._regions.items():
            packed = key.startswith(PACKED_PREVIEW_PREFIX)
            search = key.startswith(SEARCH_PREVIEW_PREFIX)
            region_key = key.split(':', 2)[-1] if packed else (key[len(SEARCH_PREVIEW_PREFIX):] if search else key)
            left, top, right, bottom = bounds
            x1 = round(max(0.0, min(1.0, left)) * pixmap.width())
            y1 = round(max(0.0, min(1.0, top)) * pixmap.height())
            x2 = round(max(0.0, min(1.0, right)) * pixmap.width())
            y2 = round(max(0.0, min(1.0, bottom)) * pixmap.height())
            if x1 >= x2 or y1 >= y2:
                continue
            color = self.REGION_COLORS.get(region_key, QColor("#45d7d0"))
            painter.setPen(QPen(color, 2, Qt.PenStyle.DashLine if search else Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if not packed:
                painter.drawRect(QRect(x1, y1, x2 - x1, y2 - y1))
            label = ' / '.join(self.REGION_LABELS.get(name, self._region_labels.get(name, name))
                               for name in region_key.split('+'))
            available = max(0, min(x2-x1 if packed else pixmap.width()-x1, pixmap.width())-12)
            label = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, available)
            text_width = painter.fontMetrics().horizontalAdvance(label) + 12
            text_height = painter.fontMetrics().height() + 6
            label_x = min(x1, max(0, pixmap.width() - text_width))
            label_y = min(y1 if packed else max(0, y1-text_height), max(0, pixmap.height()-text_height))
            background = QColor(color)
            background.setAlpha(220)
            painter.fillRect(QRect(label_x, label_y, text_width, text_height), background)
            painter.setPen(QPen(QColor("#151515")))
            painter.drawText(
                QRect(label_x + 6, label_y + 2, text_width - 12, text_height - 4),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )
        painter.end()

    def clear_image(self, message: str) -> None:
        """清除旧帧，避免把切换前或窗口不可用时的截图误认成当前输入。

        Args:
            message: 等待原因。
        """
        self._source_pixmap = QPixmap()
        self._regions.clear()
        self._region_labels.clear()
        self.clear()
        self.setText(message)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """窗口尺寸变化后重新缩放当前截图。"""
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        """在实际内容区内完整等比显示，不用含边框/内边距的外尺寸放大裁切。"""
        available = self.contentsRect().size()
        if not self._source_pixmap.isNull() and available.width() > 0 and available.height() > 0:
            pixmap = self._source_pixmap.scaled(
                available, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._draw_regions(pixmap)
            self.setPixmap(pixmap)


class ScreenshotPreviewDialog(QDialog):
    """持续展示本轮最后一次 YOLO 调用的原图或拼图输入。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建可调整大小的截图预览窗口。

        Args:
            parent: 可选的 Qt 父窗口。
        """
        super().__init__(parent)
        self.setWindowTitle("YOLO 输入预览")
        width = COMPACT_WINDOW_WIDTH
        self.resize(width, round(width * 9 / 16) + 22)
        self.image_label = ScreenshotPreviewLabel(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.image_label)

    def update_image(
        self,
        image_data: bytes,
        regions: Mapping[str, tuple[float, float, float, float]] | None = None,
        region_labels: Mapping[str, str] | None = None,
        source_description: str = '',
    ) -> None:
        """将最新截图和已确认牌区边界交给预览标签显示。

        Args:
            image_data: JPEG 编码的当前截图。
            regions: 原图牌区或仅标名称的拼图裁片归一化边界。
            region_labels: 区域键到可读名称的映射。
            source_description: 此帧实际路线和捕获方式，不根据当前下拉选择猜测。
        """
        self.image_label.set_image(image_data, regions or {}, region_labels or {})
        pixmap = self.image_label._source_pixmap
        full = '整图' in source_description or '原图' in source_description
        kind = '拼图' if '拼图' in source_description and not full else '原图'
        capture = ' · 桌面回退（需无遮挡）' if '桌面回退' in source_description else ''
        self.setWindowTitle(f'YOLO 输入预览 · {kind} {pixmap.width()}×{pixmap.height()}{capture}')

    def reset_frame(self, message: str) -> None:
        """清除已经不代表当前输入的旧帧。

        Args:
            message: 等待新帧的说明。
        """
        self.setWindowTitle('YOLO 输入预览 · 等待新帧')
        self.image_label.clear_image(message)
