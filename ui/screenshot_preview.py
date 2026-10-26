"""实时截图预览窗口。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout


class ScreenshotPreviewLabel(QLabel):
    """按窗口大小等比例显示当前截图。"""

    def __init__(self, parent: QDialog) -> None:
        """创建用于呈现截图的标签。"""
        super().__init__(parent)
        self._source_pixmap = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 180)
        self.setText("等待截图…")

    def set_image(self, image_data: bytes) -> None:
        """读取 JPEG 数据并刷新预览。

        Args:
            image_data: 推理进程传来的 JPEG 编码截图。
        """
        pixmap = QPixmap()
        if not pixmap.loadFromData(image_data, "JPEG"):
            self.setText("截图读取失败")
            return
        self._source_pixmap = pixmap
        self._refresh_pixmap()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """窗口尺寸变化后重新缩放当前截图。"""
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        """在标签范围内等比例缩放截图。"""
        if not self._source_pixmap.isNull():
            self.setPixmap(self._source_pixmap.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))


class ScreenshotPreviewDialog(QDialog):
    """持续展示记牌器当前采集到的游戏窗口截图。"""

    def __init__(self, parent: QDialog | None = None) -> None:
        """创建可调整大小的截图预览窗口。

        Args:
            parent: 可选的 Qt 父窗口。
        """
        super().__init__(parent)
        self.setWindowTitle("当前截图预览")
        self.resize(800, 500)
        self.image_label = ScreenshotPreviewLabel(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.image_label)

    def update_image(self, image_data: bytes) -> None:
        """将最新截图交给预览标签显示。

        Args:
            image_data: JPEG 编码的当前截图。
        """
        self.image_label.set_image(image_data)
