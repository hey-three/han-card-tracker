"""YOLO 实际输入预览窗口，兼容旧区域标注接口。"""

from collections.abc import Mapping

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QResizeEvent, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QSizePolicy, QVBoxLayout, QWidget


class ScreenshotPreviewLabel(QLabel):
    """等比例呈现当前 YOLO 输入图，保留旧区域标注参数的兼容能力。

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

    def __init__(self, parent: QDialog) -> None:
        """创建用于呈现截图的标签。"""
        super().__init__(parent)
        self._source_pixmap = QPixmap()
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
        """读取 JPEG，在截图上绘制每个已确认牌区的整体边框和名称。

        Args:
            image_data: 推理进程传来的 JPEG 编码截图。
            regions: 自动布局当前确认的牌区归一化边界。
            region_labels: 区域键到可读名称的映射。
        """
        image = QImage()
        if not image.loadFromData(image_data):
            self._source_pixmap = QPixmap()
            self.setText("截图读取失败")
            return
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont("Microsoft YaHei", max(9, image.width() // 100))
        font.setBold(True)
        painter.setFont(font)
        line_width = max(2, image.width() // 700)
        for region_key, bounds in (regions or {}).items():
            left, top, right, bottom = bounds
            x1 = round(max(0.0, min(1.0, left)) * image.width())
            y1 = round(max(0.0, min(1.0, top)) * image.height())
            x2 = round(max(0.0, min(1.0, right)) * image.width())
            y2 = round(max(0.0, min(1.0, bottom)) * image.height())
            if x1 >= x2 or y1 >= y2:
                continue
            color = self.REGION_COLORS.get(region_key, QColor("#45d7d0"))
            painter.setPen(QPen(color, line_width))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRect(x1, y1, x2 - x1, y2 - y1))
            label = (region_labels or {}).get(region_key, region_key)
            text_width = painter.fontMetrics().horizontalAdvance(label) + 12
            text_height = painter.fontMetrics().height() + 6
            label_x = min(x1, max(0, image.width() - text_width))
            label_y = max(0, y1 - text_height)
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
        self._source_pixmap = QPixmap.fromImage(image)
        self._refresh_pixmap()

    def clear_image(self, message: str) -> None:
        """清除旧帧，避免把切换前或窗口不可用时的截图误认成当前输入。

        Args:
            message: 等待原因。
        """
        self._source_pixmap = QPixmap()
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
            self.setPixmap(self._source_pixmap.scaled(
                available, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))


class ScreenshotPreviewDialog(QDialog):
    """持续展示本轮最后一次 YOLO 调用的原图或拼图输入。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建可调整大小的截图预览窗口。

        Args:
            parent: 可选的 Qt 父窗口。
        """
        super().__init__(parent)
        self.setWindowTitle("YOLO 输入预览")
        width = parent.width() if parent is not None else 550
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
            regions: 自动布局当前确认的牌区归一化边界。
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
