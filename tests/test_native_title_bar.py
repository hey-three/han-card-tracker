"""用真实Windows窗口检查标题栏按钮位置、命中区域和图标回调。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from config import settings
from core.inference_process import InferenceWorker
from ui.main_window import CardUI


@unittest.skipUnless(sys.platform == 'win32' and os.environ.get('QT_QPA_PLATFORM') == 'windows',
                     '仅在显式选择windows平台时检查真实系统标题栏')
class NativeTitleBarTests(unittest.TestCase):
    """不启动模型，不触碰用户配置，保留系统原生窗口按钮。

    Attributes:
        window: 使用真实Windows平台的待测主窗口。
    """

    def setUp(self) -> None:
        """建立隔离设置与真实窗口，替换会打开模态框的回调。"""
        self.app = QApplication.instance() or QApplication([])
        temporary = self.enterContext(tempfile.TemporaryDirectory(dir='tests/.tmp'))
        self.enterContext(patch.object(settings, 'CONFIG_PATH', str(Path(temporary)/'config.yaml')))
        self.enterContext(patch.object(settings, 'SHOW_CURRENT_SCREENSHOT', False))
        self.enterContext(patch.object(settings, 'ALWAYS_ON_TOP', False))
        self.enterContext(patch.object(InferenceWorker, 'start'))
        self.settings_calls = self.reset_calls = 0
        self.enterContext(patch.object(CardUI, 'on_settings_clicked', self.record_settings))
        self.enterContext(patch.object(CardUI, 'on_reset_clicked', self.record_reset))
        self.window = CardUI()
        self.window.setStyleSheet(Path('ui/ui.qss').read_text(encoding='utf-8'))
        self.window.show()
        self.app.processEvents()
        self.user = ctypes.WinDLL('user32')
        self.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user.SendMessageW.restype = ctypes.c_ssize_t
        self.user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.GetWindowLongW.restype = ctypes.c_long

    def tearDown(self) -> None:
        """在临时配置撤销前关闭、销毁窗口并排空Qt延迟事件。"""
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def record_settings(self) -> None:
        """使用普通Python回调计数，避免把Mock对象直接连接到Qt原生信号。"""
        self.settings_calls += 1

    def record_reset(self) -> None:
        """记录重置图标点击，隔离后端命令与真实用户牌局。"""
        self.reset_calls += 1

    def caption_geometry(self) -> tuple[wintypes.RECT, wintypes.RECT, wintypes.POINT]:
        """读取原生按钮可见矩形、窗口外框与客户区原点，全部为物理像素。

        Returns:
            相对窗口的按钮矩形、屏幕窗口外框和屏幕客户区原点。
        """
        outer, buttons, origin = wintypes.RECT(), wintypes.RECT(), wintypes.POINT()
        handle = int(self.window.winId())
        query = ctypes.WinDLL('dwmapi').DwmGetWindowAttribute
        query.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        self.assertEqual(query(handle, 5, ctypes.byref(buttons), ctypes.sizeof(buttons)), 0)
        self.assertTrue(self.user.GetWindowRect(handle, ctypes.byref(outer)))
        self.assertTrue(self.user.ClientToScreen(handle, ctypes.byref(origin)))
        # 最大化窗口的外框含屏幕外的不可见缩放边框，只比较实际可见的一行。
        visible = wintypes.RECT()
        self.assertEqual(query(handle, 9, ctypes.byref(visible), ctypes.sizeof(visible)), 0)
        buttons.top = max(buttons.top, visible.top-outer.top)
        buttons.bottom = min(buttons.bottom, visible.bottom-outer.top)
        return outer, buttons, origin

    def assert_native_alignment(self) -> None:
        """三个图标与原生按钮同高，标题文字避开原生按钮且鼠标交互不被吞。"""
        outer, buttons, origin = self.caption_geometry()
        scale = self.window.devicePixelRatioF()
        native_midpoint = outer.top+(buttons.top+buttons.bottom)/2
        self.assertEqual(self.window.contentsMargins().top(), 0)
        self.assertEqual(self.window.central_widget.y(), 0)
        for button in (self.window.btn_settings, self.window.btn_pause, self.window.btn_reset):
            point = button.mapTo(self.window, button.rect().center())
            x, y = origin.x+round(point.x()*scale), origin.y+round(point.y()*scale)
            self.assertLessEqual(abs(y-native_midpoint), 3*scale)
            self.assertEqual(self.user.SendMessageW(int(self.window.winId()), 0x84, 0,
                                                  ((y & 0xffff) << 16) | (x & 0xffff)), 1)
        caption = self.window.title_bar.caption_label
        right = caption.mapTo(self.window, QPoint(caption.width(), 0)).x()
        self.assertLessEqual(origin.x+right*scale, outer.left+buttons.left)
        # Qt扩展标题栏在鼠标未按下时会把系统按钮命中也返回HTCLIENT，不能误判为按钮失效。
        style = self.user.GetWindowLongW(int(self.window.winId()), -16)
        for flag in (0x80000, 0x20000, 0x10000):
            self.assertTrue(style & flag)
        self.assertGreater(buttons.right-buttons.left, 0)

    def test_icons_share_native_caption_row_after_maximize_and_topmost_changes(self) -> None:
        """正常、最大化、还原和切换置顶后保持原生标题栏同行及系统按钮命中。"""
        self.assert_native_alignment()
        self.window.showMinimized()
        self.app.processEvents()
        self.assertTrue(self.window.isMinimized())
        self.window.showNormal()
        self.app.processEvents()
        self.assert_native_alignment()
        self.window.showMaximized()
        self.app.processEvents()
        self.assert_native_alignment()
        self.window.showNormal()
        self.app.processEvents()
        self.assert_native_alignment()
        before = self.window.geometry()
        # 通过应用的置顶入口测试几何恢复；直接调用Qt底层标志会绕开生产恢复逻辑。
        for enabled in (True, False, True, False):
            self.window._apply_always_on_top_state(enabled)
            self.app.processEvents()
            self.assertEqual(self.window.geometry(), before)
            self.assert_native_alignment()

    def test_title_icons_keep_settings_pause_resume_and_reset_callbacks(self) -> None:
        """图标仍连接既有操作，暂停后变继续，基本设置不再有重置入口。"""
        QTest.mouseClick(self.window.btn_settings, Qt.MouseButton.LeftButton)
        self.assertEqual(self.settings_calls, 1)
        QTest.mouseClick(self.window.btn_pause, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.is_paused)
        self.assertEqual(self.window.btn_pause.toolTip(), '继续检测')
        QTest.mouseClick(self.window.btn_pause, Qt.MouseButton.LeftButton)
        self.assertFalse(self.window.is_paused)
        self.assertEqual(self.window.btn_pause.toolTip(), '暂停检测')
        QTest.mouseClick(self.window.btn_reset, Qt.MouseButton.LeftButton)
        self.assertEqual(self.reset_calls, 1)
