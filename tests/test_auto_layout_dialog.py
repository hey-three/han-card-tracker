"""自动布局草稿校验、编辑恢复与长标题窗口尺寸回归。"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from config import settings
from ui.auto_layout_dialog import AutoLayoutDialog


class AutoLayoutDialogTests(unittest.TestCase):
    """只验证草稿与实际Qt尺寸，不访问个人配置或真实模型。"""

    @classmethod
    def setUpClass(cls) -> None:
        """保留Qt应用供实际控件布局使用。"""
        cls.app = QApplication.instance() or QApplication([])

    def make_dialog(self, titles: list[str], name: str | None = None) -> AutoLayoutDialog:
        """建立并登记测试结束后的控件清理。

        Args:
            titles: 完整窗口标题。
            name: 需要恢复的自动布局名称。
        Returns:
            待验证对话框。
        """
        dialog = AutoLayoutDialog(titles, initial_layout_name=name)
        self.addCleanup(self.dispose_dialog, dialog)
        return dialog

    def dispose_dialog(self, dialog: AutoLayoutDialog) -> None:
        """销毁草稿及定时事件。

        Args:
            dialog: 测试窗口。
        """
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_long_title_does_not_expand_dialog_or_truncate_saved_target(self) -> None:
        """长窗口标题只在显示上省略，确认后仍保留完整原始标题。"""
        title = '很长的游戏窗口标题-' * 70
        dialog = self.make_dialog([title])
        dialog.show()
        self.app.processEvents()
        self.assertLessEqual(dialog.width(), 480)
        self.assertGreaterEqual(dialog.combo_window.width(), 100)
        dialog.combo_window.showPopup()
        self.app.processEvents()
        self.assertLessEqual(dialog.combo_window.view().width(), 440)
        dialog.combo_window.hidePopup()
        dialog.edit_name.setText('我的自动布局')
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.window_title, title)
        self.assertEqual(dialog.layout_name, '我的自动布局')

    def test_user_name_survives_target_change(self) -> None:
        """用户主动命名后切换目标不会被自动生成的默认名称覆盖。"""
        dialog = self.make_dialog(['A', 'B'])
        dialog.edit_name.selectAll()
        QTest.keyClicks(dialog.edit_name, 'Saved game')
        dialog.combo_window.setCurrentIndex(1)
        self.assertEqual(dialog.edit_name.text(), 'Saved game')
        self.assertEqual(dialog.combo_window.toolTip(), 'B')

    def test_existing_preset_reopens_target_even_when_window_is_not_running(self) -> None:
        """编辑已保存项可以恢复名称和暂未运行的窗口，取消不落配置。"""
        with patch.object(settings, 'WINDOW_LAYOUTS', {
            '已保存': {'window_title': 'Not running', 'mode': 'auto', 'layout': {}},
        }):
            dialog = self.make_dialog(['Other'], '已保存')
            self.assertEqual(dialog.edit_name.text(), '已保存')
            self.assertEqual(dialog.combo_window.currentText(), 'Not running')
            with patch.object(settings, 'save_window_layout') as save:
                dialog.reject()
                save.assert_not_called()

    def test_validation_collision_and_overwrite_decline_keep_draft(self) -> None:
        """空名、手动同名、拒绝覆盖均不接受，确认自动覆盖后才返回草稿。"""
        with patch.object(settings, 'WINDOW_LAYOUTS', {
            'Manual': {'window_title': 'M', 'layout': {}},
            'Auto': {'window_title': 'A', 'mode': 'auto', 'layout': {}},
        }):
            dialog = self.make_dialog(['Game'])
            for name in (' ', 'Manual'):
                dialog.edit_name.setText(name)
                with patch('ui.auto_layout_dialog.QMessageBox.warning') as warning:
                    dialog.accept()
                    warning.assert_called_once()
                self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
            dialog.edit_name.setText('Auto')
            with patch('ui.auto_layout_dialog.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
                dialog.accept()
            self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
            with patch('ui.auto_layout_dialog.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
                dialog.accept()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)


if __name__ == '__main__':
    unittest.main()
