"""四角标记定位截图、原图模型入口和预览渲染是否丢失边缘。"""

from io import BytesIO
import os
import sys
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw
from PIL.ImageQt import fromqimage
import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from core.packed_pipeline import PackedPipeline
from ui.screenshot_preview import ScreenshotPreviewDialog

COLORS = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))


def marked_image(size: tuple[int, int]) -> Image.Image:
    """生成四个不同颜色的角标，便于区分完整缩放和裁切。

    Args:
        size: 图像实际宽高。
    Returns:
        四角各50像素角标的RGB图。
    """
    image = Image.new('RGB', size, (80, 80, 80))
    draw = ImageDraw.Draw(image)
    width, height = size
    boxes = ((0, 0, 49, 49), (width-50, 0, width-1, 49),
             (0, height-50, 49, height-1), (width-50, height-50, width-1, height-1))
    for color, box in zip(COLORS, boxes):
        draw.rectangle(box, fill=color)
    return image


class CaptureIntegrityTests(unittest.TestCase):
    """原图链路及实际Qt绘制的边缘完整性回归。"""

    @classmethod
    def setUpClass(cls) -> None:
        """持有Qt应用，运行平台由测试命令选择。"""
        cls.app = QApplication.instance() or QApplication([])

    def test_unselected_window_never_matches_an_unrelated_desktop_window(self) -> None:
        """未选择窗口时直接返回未知，不以空条件查找任意窗口。"""
        from core.screen_capture import ScreenCapture

        with patch('core.screen_capture.win32gui.FindWindow') as find:
            for title in (None, ''):
                capture = ScreenCapture(title)
                self.assertIsNone(capture.capture_window())
                self.assertEqual(capture.last_capture_method, 'unavailable')
            find.assert_not_called()

    def test_full_route_preserves_original_image(self) -> None:
        """原图模式跳过拼图，模型和预览保留整张同一Image。"""
        source = marked_image((1600, 900))
        detector = Mock()
        detector.detect.return_value = (object(), 1.)
        pipeline = PackedPipeline()
        pipeline.detect(source, detector, None)
        self.assertIs(detector.detect.call_args.args[0], source)
        self.assertIs(pipeline.last_input, source)
        detector.detect_packed.assert_not_called()

    def test_preview_paints_all_four_corners(self) -> None:
        """宽图、竖图及方图缩放到预览时，实际绘制仍包含四角。"""
        dialog = ScreenshotPreviewDialog()
        dialog.resize(700, 470)
        dialog.show()
        try:
            for size in ((1600, 900), (900, 1600), (960, 960)):
                with self.subTest(size=size):
                    source = marked_image(size)
                    buffer = BytesIO()
                    source.save(buffer, format='JPEG', quality=95)
                    dialog.update_image(buffer.getvalue())
                    self.app.processEvents()
                    rendered = fromqimage(dialog.image_label.grab().toImage()).convert('RGB')
                    pixels = np.asarray(rendered, dtype=np.int16)
                    for color in COLORS:
                        self.assertTrue(np.any(np.abs(pixels-np.array(color)).sum(axis=2) < 35), color)
                    self.assertIn(f'{size[0]}×{size[1]}', dialog.windowTitle())
        finally:
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_clear_frame_does_not_keep_last_packed_image(self) -> None:
        """模式切换或无有效帧后清除旧图，避免把旧拼图当作新原图。"""
        dialog = ScreenshotPreviewDialog()
        try:
            buffer = BytesIO()
            marked_image((960,608)).save(buffer,format='JPEG')
            dialog.update_image(buffer.getvalue(), source_description='手动2区拼图')
            self.assertIn('拼图',dialog.windowTitle())
            dialog.update_image(buffer.getvalue(), source_description='整图：拼图漏底牌，复查原图')
            self.assertIn('原图',dialog.windowTitle())
            self.assertNotIn('拼图',dialog.windowTitle())
            dialog.reset_frame('等待原图')
            self.assertTrue(dialog.image_label._source_pixmap.isNull())
            self.assertEqual(dialog.image_label.text(),'等待原图')
        finally:
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)

    @unittest.skipUnless(sys.platform == 'win32' and os.environ.get('QT_QPA_PLATFORM') == 'windows',
                         '显式windows平台下核对真实桌面截图')
    def test_native_capture_contains_entire_marked_window(self) -> None:
        """真实窗口四角均能进入原始截图，排除DPI坐标裁掉部分客户区。"""
        from core.screen_capture import ScreenCapture

        class MarkedWindow(QWidget):
            """固定四角颜色的可控原始窗口。"""

            def paintEvent(self, event: QPaintEvent) -> None:
                """绘制完整客户区和四角标记。

                Args:
                    event: Qt绘制事件。
                """
                painter = QPainter(self)
                painter.fillRect(self.rect(), QColor(80,80,80))
                positions = ((0,0), (self.width()-50,0), (0,self.height()-50), (self.width()-50,self.height()-50))
                for (x,y), color in zip(positions,COLORS):
                    painter.fillRect(x,y,50,50,QColor(*color))
                painter.end()

        window = MarkedWindow()
        window.setWindowTitle('Han capture integrity calibration')
        window.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        window.setGeometry(30,30,640,400)
        window.show()
        window.raise_()
        QTest.qWait(120)
        try:
            capture = ScreenCapture(window.windowTitle())
            image = capture.capture_window()
            self.assertIsNotNone(image)
            width,height = image.size
            points = ((10,10),(width-11,10),(10,height-11),(width-11,height-11))
            for point,color in zip(points,COLORS):
                self.assertEqual(image.getpixel(point), color)
            for returned in (0,1):
                with patch('core.screen_capture.ctypes.windll.user32.PrintWindow',return_value=returned):
                    image = capture.capture_window()
                    self.assertEqual(capture.last_capture_method,'desktop')
                    for point,color in zip(points,COLORS):
                        self.assertEqual(image.getpixel(point),color)
            cover = QWidget()
            cover.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
            cover.setStyleSheet('background: white;')
            cover.setGeometry(window.geometry())
            cover.show()
            cover.raise_()
            QTest.qWait(120)
            try:
                image = capture.capture_window()
                self.assertEqual(capture.last_capture_method,'window')
                for point,color in zip(points,COLORS):
                    self.assertEqual(image.getpixel(point),color)
            finally:
                cover.close()
                cover.deleteLater()
            window.showMinimized()
            QTest.qWait(80)
            self.assertIsNone(capture.capture_window())
            self.assertEqual(capture.last_capture_method,'unavailable')
        finally:
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
