"""截图预览、截图恢复和主窗口尺寸的交互回归。"""

from copy import deepcopy
from io import BytesIO
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtGui import QPixmap
from shiboken6 import delete

from config import settings
from config.store import read_config
from core.inference_process import InferenceWorker
from ui.layout_editor_dialog import LayoutEditorDialog, PreviewDialog
from ui.main_window import CardUI
from ui.screenshot_preview import ScreenshotPreviewDialog
from ui.settings_dialog import SettingsDialog


class WindowBehaviorTests(unittest.TestCase):
    """使用真实 Qt 控件、独立临时配置和模拟推理回执验证窗口行为。"""

    @classmethod
    def setUpClass(cls) -> None:
        """复用 Qt 应用，测试平台可由外部指定。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """隔离配置文件、运行参数和窗口，禁止加载真实模型。"""
        root = Path(__file__).resolve().parent / ".tmp"
        directory = root / uuid4().hex
        directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, directory)
        self.config_path = directory / "config.yaml"
        self.config_path.write_text("custom_key: retained\n", encoding="utf-8")
        self.addCleanup(settings.apply_runtime_settings, settings.runtime_snapshot())
        self.addCleanup(setattr, settings, "config", deepcopy(settings.config))
        self.enterContext(patch.object(settings, "CONFIG_PATH", str(self.config_path)))
        self.enterContext(patch.object(settings, "SHOW_CURRENT_SCREENSHOT", False))
        self.enterContext(patch.object(settings, "SHOW_LAYOUT_STATUS", True))
        self.enterContext(patch.object(InferenceWorker, "start"))
        self.enterContext(patch("ui.main_window.QMessageBox.warning"))
        self.window = CardUI()
        self.window.setStyleSheet(Path("ui/ui.qss").read_text(encoding="utf-8"))
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.close)

    def make_editor(self) -> LayoutEditorDialog:
        """创建真实编辑器，不枚举用户正在使用的窗口。

        Returns:
            自动清理的布局编辑器。
        """
        with patch("ui.layout_editor_dialog.list_visible_window_titles", return_value=["Test Game"]):
            editor = LayoutEditorDialog(self.window)
        editor.show()
        self.app.processEvents()
        self.addCleanup(editor.close)
        return editor

    def test_diagnostic_warning_visible_with_layout_status_hidden(self) -> None:
        """诊断写盘失败不因隐藏普通提示而消失，也不强制停止识别。"""
        settings.SHOW_LAYOUT_STATUS = False
        self.window.worker.state = 'ready'
        self.window.on_diagnostic_status({'state': 'paused', 'reason': 'disk full', 'at_monotonic_ns': 2})
        self.assertFalse(self.window.statusBar().isHidden())
        self.assertIn('disk full', self.window.statusBar().currentMessage())
        self.window.on_diagnostic_status({'state': 'recording', 'at_monotonic_ns': 1})
        self.assertIn('disk full', self.window.statusBar().currentMessage())
        self.window.on_diagnostic_status({'state': 'off', 'at_monotonic_ns': 3})
        self.assertTrue(self.window.statusBar().isHidden())
        self.assertEqual(self.window.worker.state, 'ready')

    def test_full_diagnostic_preference_waits_for_ack_and_reopens(self) -> None:
        """后端失败不落盘；成功后重开设置仍显示已保存偏好。"""
        settings.FULL_DIAGNOSTICS = False
        self.window.worker.state = 'ready'
        with patch.object(self.window.worker, 'update_settings', side_effect=[111, 112]):
            self.window.on_full_diagnostics_changed(1)
            self.assertFalse(settings.FULL_DIAGNOSTICS)
            self.window.worker.command_completed.emit(111, 'update_settings', False, 'thread unavailable')
            self.assertNotIn('full_diagnostics', read_config(str(self.config_path)))
            self.window.on_full_diagnostics_changed(1)
            self.window.worker.command_completed.emit(112, 'update_settings', True, '')
        self.assertTrue(read_config(str(self.config_path))['full_diagnostics'])
        dialog = SettingsDialog(self.window)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.combo_full_diagnostics.currentIndex(), 1)
        self.assertEqual(dialog.combo_full_diagnostics.receivers('2currentIndexChanged(int)'), 1)

    def test_preview_width_and_shrinking_after_large_image(self) -> None:
        """预览与主窗口同宽；大图不会强迫窗口扩大或阻止缩小。"""
        self.window.resize(640, self.window.height())
        preview = ScreenshotPreviewDialog(self.window)
        self.addCleanup(preview.close)
        preview.show()
        self.app.processEvents()
        self.assertEqual(preview.width(), self.window.width())
        data = BytesIO()
        Image.new("RGB", (3840, 2160), "white").save(data, format="JPEG")
        preview.update_image(data.getvalue())
        preview.resize(1000, 600)
        self.app.processEvents()
        preview.resize(550, 330)
        self.app.processEvents()
        self.assertEqual(preview.width(), 550)
        self.assertLessEqual(preview.image_label.pixmap().width(), preview.image_label.width())
        self.assertLessEqual(preview.image_label.pixmap().height(), preview.image_label.height())

    def test_close_blocks_late_frames_and_persists_before_ack(self) -> None:
        """点叉后立即停止预览，后端回执前后的截图均不会重新弹窗。"""
        self.window.on_show_current_screenshot_changed(1)
        preview = self.window._screenshot_preview
        self.assertIsNotNone(preview)
        dialog = SettingsDialog(self.window)
        self.addCleanup(dialog.close)
        self.window._settings_dialog = dialog
        self.window.worker.state = "ready"
        with patch.object(self.window.worker, "update_settings", return_value=81) as update:
            preview.close()
            update.assert_called_once_with({"SHOW_CURRENT_SCREENSHOT": False})
            self.assertTrue(preview.isHidden())
            self.assertFalse(read_config(str(self.config_path))["show_current_screenshot"])
            self.assertEqual(dialog.combo_show_current_screenshot.currentIndex(), 0)
            self.window.on_screenshot_ready(b"late image")
            self.assertTrue(preview.isHidden())
            self.window.worker.command_completed.emit(81, "update_settings", True, "")
        self.assertFalse(settings.SHOW_CURRENT_SCREENSHOT)

        self.assertFalse(read_config(str(self.config_path))["show_current_screenshot"])
        self.assertEqual(read_config(str(self.config_path))["custom_key"], "retained")
        self.window.on_screenshot_ready(b"late image")
        self.assertTrue(preview.isHidden())
        self.window.worker.state = "stopped"
        self.window.on_show_current_screenshot_changed(1)
        self.assertTrue(preview.isVisible())

    def test_close_then_process_failure_retains_disabled_preference(self) -> None:
        """关闭后即使回执未到、推理进程就退出，下次启动仍禁用预览。"""
        self.window.on_show_current_screenshot_changed(1)
        self.window.worker.state = "ready"
        with patch.object(self.window.worker, "update_settings", return_value=83):
            self.window._screenshot_preview.close()
            self.window.worker.fail("process exited")
        self.assertFalse(settings.SHOW_CURRENT_SCREENSHOT)
        self.assertFalse(read_config(str(self.config_path))["show_current_screenshot"])
        self.assertTrue(self.window._screenshot_preview.isHidden())

    def test_close_while_enable_ack_is_pending(self) -> None:
        """快速关闭期间收到旧的开启回执也不能重新弹窗。"""
        self.window.on_show_current_screenshot_changed(1)
        self.window.worker.state = "ready"
        with patch.object(self.window.worker, "update_settings", side_effect=[91, 92]):
            self.window.on_show_current_screenshot_changed(1)
            self.window._screenshot_preview.reject()
            self.window.worker.command_completed.emit(91, "update_settings", True, "")
            self.assertTrue(self.window._screenshot_preview.isHidden())
            self.window.worker.command_completed.emit(92, "update_settings", True, "")
        self.assertFalse(settings.SHOW_CURRENT_SCREENSHOT)

    def test_preview_frames_do_not_raise_window(self) -> None:
        """更新画面时不反复抢占设置窗口或游戏的前台层级。"""
        self.window.on_show_current_screenshot_changed(1)
        with patch.object(self.window._screenshot_preview, "raise_") as raise_window:
            self.window.on_screenshot_ready(b"invalid jpeg")
            self.window.on_screenshot_ready(b"invalid jpeg")
        raise_window.assert_not_called()

    def test_shutdown_does_not_disable_saved_preview(self) -> None:
        """退出记牌器会隐藏预览，但保留用户的下次启动偏好。"""
        self.window.on_show_current_screenshot_changed(1)
        self.window.close()
        self.assertTrue(settings.SHOW_CURRENT_SCREENSHOT)
        self.assertTrue(self.window._screenshot_preview.isHidden())
        self.window.on_screenshot_ready(b"late image")
        self.assertTrue(self.window._screenshot_preview.isHidden())

    def test_layout_status_visibility_and_failure(self) -> None:
        """提示开关持久化并收起状态栏，故障仍显示详情及恢复按钮。"""
        self.window.worker.state = "ready"
        self.window.on_layout_status_changed("自动布局：等待有效画面")
        self.window.on_show_layout_status_changed(0)
        self.assertTrue(self.window.statusBar().isHidden())
        self.assertFalse(read_config(str(self.config_path))["show_layout_status"])
        self.window.on_layout_status_changed("自动布局：已定位")
        self.assertTrue(self.window.statusBar().isHidden())
        self.window.on_show_layout_status_changed(1)
        self.assertIn("已定位", self.window.statusBar().currentMessage())
        self.window.on_show_layout_status_changed(0)
        self.window.worker.fail("测试故障")
        self.assertFalse(self.window.statusBar().isHidden())
        self.assertFalse(self.window.btn_retry.isHidden())
        self.assertIn("测试故障", self.window.statusBar().currentMessage())

    def test_played_labels_remain_in_layout_after_repeated_toggles(self) -> None:
        """反复隐藏出牌记录后标签仍正确排版，不与牌数或状态栏重叠。"""
        for visible in (False, True, False, True):
            self.window._show_played_cards = visible
            self.window._update_played_cards_visibility()
            self.app.processEvents()
            for label in self.window.played_cards_labels.values():
                self.assertGreaterEqual(self.window.second_row_layout.indexOf(label), 0)
                self.assertEqual(label.isVisible(), visible)
        rectangles = [label.geometry() for label in self.window.played_cards_labels.values()]
        for first, second in zip(rectangles, rectangles[1:]):
            self.assertLess(first.bottom(), second.top())
        for label in self.window.name_labels.values():
            self.assertGreaterEqual(label.height(), label.minimumSizeHint().height())

    def test_screenshot_restore_preserves_positions_sizes_and_flags(self) -> None:
        """重复截图恢复时使用相同坐标单位，窗口尺寸、置顶和字体区域不变。"""
        editor = self.make_editor()
        windows = (self.window, editor)
        before = [(w.pos(), w.size(), w.windowFlags()) for w in windows]
        with patch.object(editor, "_wait_for_desktop_to_settle"):
            for _ in range(3):
                editor._send_app_windows_to_bottom()
                editor._restore_app_windows_after_capture()
                self.app.processEvents()
                self.assertEqual([(w.pos(), w.size(), w.windowFlags()) for w in windows], before)
        for label in self.window.count_labels.values():
            self.assertTrue(label.isVisible())
            self.assertGreaterEqual(label.height(), label.minimumSizeHint().height())

    def test_capture_error_restores_windows_and_allows_retry(self) -> None:
        """截图抛异常后恢复所有窗口并重新启用按钮。"""
        editor = self.make_editor()
        before = self.window.geometry()
        with patch.object(editor, "_wait_for_desktop_to_settle"), patch(
            "ui.layout_editor_dialog.capture_window_by_title", side_effect=RuntimeError("capture failed"),
        ), patch("ui.layout_editor_dialog.QMessageBox.warning") as warning:
            editor._capture_window()
        self.assertEqual(self.window.geometry(), before)
        self.assertFalse(editor._capturing)
        self.assertFalse(editor._windows_pushed_back)
        self.assertTrue(editor.btn_capture.isEnabled())
        warning.assert_called_once()

    def test_capture_cannot_reenter_while_processing_events(self) -> None:
        """截图等待桌面刷新时的重复点击不会覆盖恢复快照。"""
        editor = self.make_editor()
        before = self.window.geometry()
        with patch.object(editor, "_wait_for_desktop_to_settle", side_effect=editor._capture_window), patch(
            "ui.layout_editor_dialog.capture_window_by_title", return_value=Image.new("RGB", (640, 360)),
        ) as capture:
            editor._capture_window()
        capture.assert_called_once()
        self.assertEqual(self.window.geometry(), before)

    def test_topmost_toggling_preserves_window_geometry(self) -> None:
        """切换置顶不擅自缩小用户调整过的窗口。"""
        self.window.resize(750, 300)
        self.app.processEvents()
        before = self.window.size()
        original = bool(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        for enabled in (not original, original):
            self.window._apply_always_on_top_state(enabled)
            self.app.processEvents()
            self.assertEqual(self.window.size(), before)
            self.assertTrue(self.window.isVisible())

    def test_destroying_preview_cancels_deferred_resize(self) -> None:
        """预览窗口在延迟刷新执行前销毁，不会访问已删除的 Qt 控件。"""
        pixmap = QPixmap(640, 360)
        pixmap.fill(Qt.GlobalColor.white)
        dialog = PreviewDialog(pixmap, self.window)
        dialog.show()
        self.assertTrue(dialog._preview_timer.isActive())
        delete(dialog)
        self.app.processEvents()

    def test_settings_grouping_and_labels(self) -> None:
        """日志、截图存盘和实时预览是高级设置里的三个独立选项。"""
        dialog = SettingsDialog(self.window)
        self.addCleanup(dialog.close)
        for combo in (dialog.combo_debug_mode, dialog.combo_save_debug_images, dialog.combo_show_current_screenshot):
            self.assertTrue(dialog.tab2.isAncestorOf(combo))
        texts = {label.text() for label in dialog.tab2.findChildren(QLabel)}
        self.assertIn("输出调试日志：", texts)
        self.assertIn("保存游戏截图：", texts)
        self.assertTrue(dialog.tab1.isAncestorOf(dialog.combo_show_layout_status))


if __name__ == "__main__":
    unittest.main()
