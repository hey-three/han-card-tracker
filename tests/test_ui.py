"""真实 Qt 控件与推理服务之间的交互回归。"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from config import settings
from core.inference_process import InferenceWorker
from ui.main_window import CardUI
from ui.settings_dialog import SettingsDialog
from ui.layout_editor_dialog import LayoutEditorDialog


class UITests(unittest.TestCase):
    """使用无窗口 Qt 平台验证状态变化，不加载模型。"""

    @classmethod
    def setUpClass(cls) -> None:
        """保留 QApplication 实例。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """创建真实界面，并拦截自动启动和弹窗。"""
        with patch.object(InferenceWorker, "start"):
            self.window = CardUI()
        self.warning = patch("ui.main_window.QMessageBox.warning")
        self.warning.start()
        self.addCleanup(self.warning.stop)
        self.addCleanup(self.window.close)

    def test_rejected_detection_does_not_latch_busy(self) -> None:
        """服务拒绝请求时 UI 不会永远 busy。"""
        with patch.object(self.window.worker, "request_detect", return_value=False):
            self.window.request_one_update()
        self.assertFalse(self.window._busy)

    def test_failure_shows_restart_and_stops_timer(self) -> None:
        """故障原因和恢复入口均在窗口上可见。"""
        self.window.timer.start()
        self.window.worker.fail("missing model")
        self.assertFalse(self.window.timer.isActive())
        self.assertFalse(self.window.btn_retry.isHidden())
        self.assertIn("missing model", self.window.statusBar().currentMessage())
        with patch.object(self.window.worker, "restart") as restart:
            self.window.btn_retry.click()
        restart.assert_called_once()

    def test_ready_does_not_override_user_pause(self) -> None:
        """重启成功后尊重用户原有的暂停状态。"""
        self.window.is_paused = True
        self.window.worker._set_state("ready")
        self.assertFalse(self.window.timer.isActive())

    def test_reset_clears_played_text_while_paused(self) -> None:
        """暂停时重置也会清除已有出牌记录文本。"""
        self.window.played_cards_labels["left"].setText("old cards")
        self.window._reset_ui_to_total()
        self.assertNotIn("old cards", self.window.played_cards_labels["left"].text())

    def test_pending_topmost_selection_survives_other_setting_ack(self) -> None:
        """其他设置回执不覆盖尚未在关闭窗口时应用的置顶选择。"""
        dialog = SettingsDialog(self.window)
        self.window._settings_dialog = dialog
        dialog.set_current_always_on_top(not settings.ALWAYS_ON_TOP)
        expected = dialog.combo_always_on_top.currentIndex()
        self.window._refresh_open_settings()
        self.assertEqual(dialog.combo_always_on_top.currentIndex(), expected)
        self.window._settings_dialog = None
        dialog.close()

    def test_start_failure_during_constructor_has_recovery_controls(self) -> None:
        """构造时同步启动失败也能显示恢复按钮。"""
        with patch.object(InferenceWorker, "start", autospec=True, side_effect=lambda worker: worker.fail("startup")):
            window = CardUI()
        self.addCleanup(window.close)
        self.assertFalse(window.btn_retry.isHidden())
        self.assertIn("startup", window.statusBar().currentMessage())

    def test_current_layout_is_not_deleted_before_switch_ack(self) -> None:
        """删除当前布局时，替代布局切换失败必须保留原配置。"""
        dialog = SettingsDialog(self.window)
        self.window.worker.state = "ready"
        old_name = self.window.layout_name
        with patch.object(self.window.worker, "switch_layout", return_value=17), patch.object(
            settings, "delete_window_layout",
        ) as delete:
            self.assertTrue(self.window.on_layout_delete_clicked(dialog, old_name))
            delete.assert_not_called()
            self.window.worker.command_completed.emit(17, "switch_layout", False, "failed")
            delete.assert_not_called()
        self.assertEqual(self.window.layout_name, old_name)
        dialog.close()

    def test_editor_does_not_report_success_after_save_failure(self) -> None:
        """底层保存失败时编辑器保持打开且没有成功消息。"""
        with patch("ui.layout_editor_dialog.list_visible_window_titles", return_value=["Game"]):
            dialog = LayoutEditorDialog(self.window, initial_layout_name="new")
        with patch.object(dialog, "_preview_layout", return_value=True), patch.object(
            dialog, "_build_normalized_layout", return_value=({}, None),
        ), patch("ui.layout_editor_dialog.get_layout_config", return_value=None), patch(
            "ui.layout_editor_dialog.save_layout", side_effect=PermissionError("locked"),
        ), patch("ui.layout_editor_dialog.QMessageBox.critical") as failure, patch(
            "ui.layout_editor_dialog.QMessageBox.information",
        ) as success:
            dialog._save_layout()
        failure.assert_called_once()
        success.assert_not_called()
        self.assertIsNone(dialog.saved_layout_name)
        dialog.close()


if __name__ == "__main__":
    unittest.main()
