"""真实 Qt 控件与推理服务之间的交互回归。"""

import os
from copy import deepcopy
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel

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

    def test_cpu_bundle_only_offers_cpu_device(self) -> None:
        """CPU发布版本只展示CPU，普通源码与CPU/GPU版仍保留两个选项。"""
        for cpu_only, expected in ((True, ['CPU']), (False, ['CPU', 'GPU'])):
            with self.subTest(cpu_only=cpu_only), patch.object(settings, 'CPU_ONLY_BUILD', cpu_only):
                dialog = SettingsDialog(self.window)
                self.assertEqual([dialog.combo_device.itemText(i) for i in range(dialog.combo_device.count())], expected)
                dialog.close()
                dialog.deleteLater()

    def setUp(self) -> None:
        """隔离个人配置并创建真实界面，清理时主动销毁 Qt 对象与子定时器。"""
        directory = Path(__file__).parent / '.tmp' / uuid4().hex
        directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, directory)
        config_path = directory / 'config.yaml'
        config_path.write_text('custom_key: retained\n', encoding='utf-8')
        self.addCleanup(settings.apply_runtime_settings, settings.runtime_snapshot())
        self.addCleanup(setattr, settings, 'CURRENT_LAYOUT', settings.CURRENT_LAYOUT)
        self.addCleanup(setattr, settings, 'config', deepcopy(settings.config))
        self.enterContext(patch.object(settings, 'CONFIG_PATH', str(config_path)))
        with patch.object(InferenceWorker, "start"):
            self.window = CardUI()
        self.warning = patch("ui.main_window.QMessageBox.warning")
        self.warning.start()
        self.addCleanup(self.warning.stop)
        self.addCleanup(self.dispose_window, self.window)

    def dispose_window(self, window: CardUI) -> None:
        """关闭并销毁测试窗口，避免待处理 Qt 事件进入下个测试。

        Args:
            window: 当前测试创建的主窗口。
        """
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_loading_notice_is_topmost_visible_and_cleared_after_startup(self) -> None:
        """关闭普通布局提示也显示首次较久提示，就绪后恢复正常标题。"""
        from PySide6.QtWidgets import QStatusBar
        with patch.object(settings, 'SHOW_LAYOUT_STATUS', False):
            self.window.worker._set_state('starting')
            self.assertEqual(self.window.title_bar.caption_label.text(), '正在加载模型…')
            self.assertIn('首次', self.window.title_bar.status_label.text())
            self.assertIn('转换', self.window.title_bar.status_label.toolTip())
            self.assertFalse(self.window.findChildren(QStatusBar))
            self.window.worker._set_state('ready')
            self.assertNotIn('正在加载模型', self.window.title_bar.caption_label.text())
            self.assertNotIn('首次', self.window.title_bar.status_label.text())

    def test_switch_loading_notice_does_not_release_inflight_detection(self) -> None:
        """模型切换提示只改标题，待完成检测的busy不受影响。"""
        from core.inference_protocol import Command
        self.window.worker.state = 'ready'
        self.window._busy = True
        self.window.worker._pending[123] = Command(123, 1, 'switch_model', 'next')
        self.window.worker.model_loading_changed.emit(True)
        self.assertEqual(self.window.title_bar.caption_label.text(), '正在加载模型')
        self.assertIn('首次', self.window.title_bar.status_label.text())
        self.assertTrue(self.window._busy)
        self.window.worker._pending.clear()
        self.window.worker.model_loading_changed.emit(False)
        self.assertNotIn('正在加载模型', self.window.title_bar.caption_label.text())
        self.assertTrue(self.window._busy)

    def test_overwritten_current_layout_submits_new_coordinates_even_when_name_is_unchanged(self) -> None:
        """同名当前布局编辑后必须提交完整新配置，不能因名称相同跳过。"""
        name = self.window.layout_name
        updated = deepcopy(settings.WINDOW_LAYOUTS[name])
        updated['mode'] = 'manual'
        from utils.layout_editor.coord import REGION_KEYS
        updated['layout'] = {key: [.1, .1, .4, .3] for key in REGION_KEYS}
        updated['layout']['player_hand'] = [.12, .71, .82, .94]
        with patch.object(settings, 'WINDOW_LAYOUTS', {**settings.WINDOW_LAYOUTS, name: updated}), \
                patch.object(self.window.worker, 'switch_layout', return_value=93) as switch:
            self.window.worker.state = 'ready'
            self.window.on_layout_changed(name)
            switch.assert_called_once_with(name, settings.validate_layout(updated))
            self.window.worker.command_completed.emit(93, 'switch_layout', True, '')
        self.assertEqual(self.window.layout_name, name)

    def test_edit_button_opens_the_requested_item_instead_of_the_current_layout(self) -> None:
        """编辑图标明确指定布局，不使用主窗口当前选中的名称替代。"""
        dialog = SettingsDialog(self.window)
        self.addCleanup(dialog.close)
        with patch('ui.main_window.LayoutEditorDialog') as editor:
            editor.return_value.exec.return_value = 0
            self.window.on_layout_editor_clicked(dialog, '要编辑的另一项')
            self.assertEqual(editor.call_args.kwargs['initial_layout_name'], '要编辑的另一项')

    def test_panel_arrangement_reuses_history_and_does_not_reset_recognition(self) -> None:
        """上下与左右切换保留牌数、历史及控件，不触发后台配置或牌局重置。"""
        from PySide6.QtWidgets import QBoxLayout
        self.window.on_result_ready({'K': 2}, {'left': [['8', '9', '10', 'J', 'Q']]}, 20.)
        labels = dict(self.window.played_cards_labels)
        before = {key: label.text() for key, label in labels.items()}
        with patch.object(self.window.worker, 'update_settings') as update, \
                patch.object(self.window, '_reset_ui_to_total') as reset:
            self.window.on_panel_arrangement_changed(1)
            self.assertEqual(self.window.content_layout.direction(), QBoxLayout.Direction.LeftToRight)
            self.window.on_panel_arrangement_changed(0)
            self.assertEqual(self.window.content_layout.direction(), QBoxLayout.Direction.TopToBottom)
            update.assert_not_called()
            reset.assert_not_called()
        self.assertEqual(self.window.count_labels['K'].text(), '2')
        self.assertEqual(self.window.played_cards_labels, labels)
        self.assertEqual({key: label.text() for key, label in labels.items()}, before)
        self.assertTrue(before['left'].startswith('上  '))
        self.assertTrue(before['self'].startswith('本  '))
        self.assertTrue(before['right'].startswith('下  '))

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
        self.assertIn("missing model", self.window.title_bar.status_label.toolTip())
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

    def test_manual_route_can_select_packed_or_original(self) -> None:
        """自动始终整图，手动布局有明确的两种检测方式。"""
        dialog = SettingsDialog(self.window)
        self.assertTrue(dialog.tab1.isAncestorOf(dialog.btn_layout_auto))
        self.assertTrue(dialog.tab1.isAncestorOf(dialog.btn_layout_manual))
        self.assertTrue(dialog.tab1.isAncestorOf(dialog.combo_model))
        self.assertFalse(dialog.tab2.isAncestorOf(dialog.combo_model))
        self.assertIs(dialog.layout_mode_stack.parentWidget(), dialog.layout_settings_group)
        self.assertTrue(dialog.manual_layout_panel.isAncestorOf(dialog.combo_layout))
        self.assertTrue(dialog.manual_layout_panel.isAncestorOf(dialog.combo_manual_inference))
        self.assertEqual([dialog.combo_manual_inference.itemText(i) for i in range(2)], ['拼图检测', '原图检测'])
        dialog.btn_layout_auto.click()
        self.assertEqual(dialog.layout_mode_stack.currentIndex(), 0)
        self.assertEqual(dialog.layout_settings_group.title(), "")
        self.assertTrue(dialog.auto_layout_panel.isAncestorOf(dialog.btn_auto_layout))
        dialog.btn_layout_manual.click()
        self.assertEqual(dialog.layout_mode_stack.currentIndex(), 1)
        self.assertEqual(dialog.layout_settings_group.title(), "")
        dialog.close()

    def test_layout_mode_shows_only_matching_layout_choices(self) -> None:
        """两种模式各列已保存布局，用户点击模式立即传递对应名称。"""
        layouts = {
            "手动布局 A": {"window_title": "Game A", "layout": {}},
            "自动布局 · Game B": {"window_title": "Game B", "layout": {}, "mode": "auto"},
            "手动布局 C": {"window_title": "Game C", "layout": {}, "mode": "manual"},
        }
        selected_names: list[str] = []
        with patch.object(settings, "WINDOW_LAYOUTS", layouts):
            dialog = SettingsDialog(self.window)
            self.addCleanup(dialog.close)
            self.assertEqual(
                [dialog.combo_layout.itemText(index) for index in range(dialog.combo_layout.count())],
                ["手动布局 A", "手动布局 C"],
            )
            dialog.on_layout_change_callback = selected_names.append

            dialog.set_current_layout_mode("auto", "Game B")
            self.assertEqual(dialog.layout_mode_stack.currentIndex(), 0)
            self.assertEqual(dialog.label_auto_window.text(), "Game B")
            self.assertEqual(dialog.combo_auto_layout.currentText(), '自动布局 · Game B')
            dialog.btn_layout_manual.click()
            dialog.combo_layout.setCurrentIndex(1)
            dialog.combo_layout.activated.emit(1)

            self.assertEqual(dialog.layout_mode_stack.currentIndex(), 1)
            self.assertEqual(selected_names, ["手动布局 A", "手动布局 C"])

    def test_donation_tab_order_and_image_fill_available_area(self) -> None:
        """赞赏页排在关于前面，二维码随页面尺寸变化且保持比例。"""
        dialog = SettingsDialog(self.window)
        self.addCleanup(dialog.close)
        self.assertEqual(
            [dialog.tab_widget.tabText(index) for index in range(dialog.tab_widget.count())],
            ["基本设置", "高级设置", "赞赏", "关于"],
        )
        dialog.resize(800, 560)
        dialog.tab_widget.setCurrentWidget(dialog.tab4)
        dialog.show()
        self.app.processEvents()

        image_label = dialog.findChild(QLabel, "DonationQRCode")
        self.assertIsNotNone(image_label)
        pixmap = image_label.pixmap()
        self.assertIsNotNone(pixmap)
        self.assertGreater(pixmap.width(), 360)
        self.assertLessEqual(pixmap.width(), image_label.contentsRect().width())
        self.assertLessEqual(pixmap.height(), image_label.contentsRect().height())
        self.assertAlmostEqual(pixmap.width() / pixmap.height(), 4 / 3, delta=0.02)
        donation_text = {label.text() for label in dialog.tab4.findChildren(QLabel)}
        self.assertNotIn("感谢支持 Han 记牌器", donation_text)
        self.assertNotIn("如果这个项目对你有帮助，欢迎自愿赞赏支持。", donation_text)

    def test_performance_tooltip_distinguishes_model_and_pipeline(self) -> None:
        """设置按钮提供分阶段指标，不把子进程耗时误称物理显示延迟。"""
        self.window.on_performance_ready({'model_ms': 20., 'pipeline_ms': 35., 'model_calls': 2.})
        text = self.window.btn_settings.toolTip()
        self.assertIn('子进程整轮：35.0 ms', text)
        self.assertIn('模型调用次数：2', text)

    def test_manual_mode_persists_after_ack_and_failed_change_rolls_back(self) -> None:
        """检测方式等后端成功才落盘，重开显示已保存值，失败恢复旧选择。"""
        from config.store import read_config

        settings.PACKED_INFERENCE = True
        from io import BytesIO
        buffer = BytesIO()
        from PIL import Image
        Image.new('RGB',(960,608)).save(buffer,format='JPEG')
        with patch.object(settings,'SHOW_CURRENT_SCREENSHOT',True):
            self.window._preview_requested = True
            self.window._layout_status = '手动2区拼图'
            self.window.on_screenshot_ready(buffer.getvalue())
        self.assertIn('拼图',self.window._screenshot_preview.windowTitle())
        dialog = SettingsDialog(self.window)
        dialog.manual_inference_changed.connect(self.window.on_manual_inference_changed)
        self.window._settings_dialog = dialog
        self.window.worker.state = 'ready'
        with patch.object(self.window.worker, 'update_settings', return_value=41) as update:
            dialog.combo_manual_inference.setCurrentIndex(1)
            update.assert_called_once_with({'PACKED_INFERENCE': False})
            self.assertNotIn('packed_inference', read_config(settings.CONFIG_PATH))
            self.window.worker.command_completed.emit(41, 'update_settings', True, '')
        self.assertTrue(self.window._screenshot_preview.image_label._source_pixmap.isNull())
        self.assertIn('等待新帧',self.window._screenshot_preview.windowTitle())
        self.assertFalse(read_config(settings.CONFIG_PATH)['packed_inference'])
        reopened = SettingsDialog(self.window)
        self.assertEqual(reopened.combo_manual_inference.currentText(), '原图检测')
        reopened.close()
        with patch.object(self.window.worker, 'update_settings', return_value=42):
            dialog.combo_manual_inference.setCurrentIndex(0)
            self.window.worker.command_completed.emit(42, 'update_settings', False, 'backend error')
        self.assertEqual(dialog.combo_manual_inference.currentText(), '原图检测')
        self.assertFalse(read_config(settings.CONFIG_PATH)['packed_inference'])
        dialog.close()

    def test_pending_topmost_selection_survives_other_setting_ack(self) -> None:
        """其他设置回执不覆盖尚未在关闭窗口时应用的置顶选择。"""
        dialog = SettingsDialog(self.window)
        self.window._settings_dialog = dialog
        dialog.set_current_always_on_top(not settings.ALWAYS_ON_TOP)
        expected = dialog.switch_always_on_top.isChecked()
        self.window._refresh_open_settings()
        self.assertEqual(dialog.switch_always_on_top.isChecked(), expected)
        self.window._settings_dialog = None
        dialog.close()

    def test_start_failure_during_constructor_has_recovery_controls(self) -> None:
        """构造时同步启动失败也能显示恢复按钮。"""
        with patch.object(InferenceWorker, "start", autospec=True, side_effect=lambda worker: worker.fail("startup")):
            window = CardUI()
        self.addCleanup(self.dispose_window, window)
        self.assertFalse(window.btn_retry.isHidden())
        self.assertIn("startup", window.title_bar.status_label.toolTip())

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
        with patch.object(
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

    def test_auto_layout_saves_only_after_backend_ack(self) -> None:
        """自动布局选择窗口后无需框选，且等待子进程成功才写配置。"""
        dialog = SettingsDialog(self.window)
        self.window.worker.state = "ready"
        with patch("ui.main_window.list_visible_window_titles", return_value=["Game"]), patch(
            "ui.main_window.AutoLayoutDialog",
        ) as editor, patch.object(self.window.worker, "switch_layout", return_value=51) as switch, patch.object(
            settings, "save_window_layout",
        ) as save:
            editor.return_value.exec.return_value = 1
            editor.return_value.DialogCode.Accepted = 1
            editor.return_value.layout_name = '我的自动布局'
            editor.return_value.window_title = 'Game'
            self.window.on_auto_layout_clicked(dialog)
            save.assert_not_called()
            switch.assert_called_once_with("我的自动布局", {"window_title": "Game", "layout": {}, "mode": "auto"})
            self.window.worker.command_completed.emit(51, "switch_layout", True, "")
            save.assert_called_once_with("我的自动布局", "Game", {}, mode="auto")
        dialog.close()

    def test_auto_layout_cancel_does_not_change_configuration(self) -> None:
        """取消窗口选择不发命令也不写配置。"""
        dialog = SettingsDialog(self.window)
        with patch("ui.main_window.list_visible_window_titles", return_value=["Game"]), patch(
            "ui.main_window.AutoLayoutDialog",
        ) as editor, patch.object(self.window.worker, "switch_layout") as switch, patch.object(
            settings, "save_window_layout",
        ) as save:
            editor.return_value.exec.return_value = 0
            editor.return_value.DialogCode.Accepted = 1
            self.window.on_auto_layout_clicked(dialog)
            switch.assert_not_called()
            save.assert_not_called()
        dialog.close()

    def test_named_auto_preset_persists_after_ack_and_is_reusable_without_window_picker(self) -> None:
        """命名自动布局保存完整目标，重开后直接选用而不再次枚举窗口。"""
        from config.store import read_config
        dialog = SettingsDialog(self.window, on_layout_change_callback=self.window.on_layout_changed)
        self.addCleanup(dialog.close)
        self.window._settings_dialog = dialog
        self.window.worker.state = 'ready'
        with patch('ui.main_window.list_visible_window_titles', return_value=['Game']), \
                patch('ui.main_window.AutoLayoutDialog') as editor, \
                patch.object(self.window.worker, 'switch_layout', return_value=71):
            editor.return_value.exec.return_value = 1
            editor.return_value.DialogCode.Accepted = 1
            editor.return_value.layout_name = '可复用自动布局'
            editor.return_value.window_title = 'Game'
            self.window.on_auto_layout_clicked(dialog)
            self.assertNotIn('可复用自动布局', settings.WINDOW_LAYOUTS)
            self.window.worker.command_completed.emit(71, 'switch_layout', True, '')
        saved = read_config(settings.CONFIG_PATH)
        expected = {'window_title': 'Game', 'mode': 'auto', 'layout': {}}
        self.assertEqual(saved['window_layouts']['可复用自动布局'], expected)
        self.assertEqual(saved['current_layout'], '可复用自动布局')
        reopened = SettingsDialog(self.window, on_layout_change_callback=self.window.on_layout_changed)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.combo_auto_layout.currentText(), '可复用自动布局')
        with patch('ui.main_window.list_visible_window_titles') as picker, \
                patch.object(self.window.worker, 'switch_layout', return_value=72) as switch:
            reopened.combo_auto_layout.activated.emit(reopened.combo_auto_layout.currentIndex())
            switch.assert_called_once_with('可复用自动布局', expected)
            picker.assert_not_called()
            self.window.worker.command_completed.emit(72, 'switch_layout', True, '')

    def test_failed_auto_preset_creation_does_not_save_or_change_current_layout(self) -> None:
        """后端拒绝新自动布局时不写入草稿，也不更改活动布局。"""
        old_name = self.window.layout_name
        dialog = SettingsDialog(self.window)
        self.addCleanup(dialog.close)
        self.window.worker.state = 'ready'
        with patch('ui.main_window.list_visible_window_titles', return_value=['Game']), \
                patch('ui.main_window.AutoLayoutDialog') as editor, \
                patch.object(self.window.worker, 'switch_layout', return_value=73), \
                patch.object(settings, 'save_window_layout') as save:
            editor.return_value.exec.return_value = 1
            editor.return_value.DialogCode.Accepted = 1
            editor.return_value.layout_name = '未成功的自动布局'
            editor.return_value.window_title = 'Game'
            self.window.on_auto_layout_clicked(dialog)
            self.window.worker.command_completed.emit(73, 'switch_layout', False, '拒绝')
            save.assert_not_called()
        self.assertEqual(self.window.layout_name, old_name)

    def test_mode_click_switches_saved_layout_and_rolls_back_after_failure(self) -> None:
        """模式点击提交完整配置，失败恢复实际模式，不把默认首项冒充已应用。"""
        manual = deepcopy(next(value for value in settings.WINDOW_LAYOUTS.values() if value.get('mode') != 'auto'))
        layouts = {'手动一': manual, '手动二': manual,
                   '自动一': {'window_title': 'Game A', 'mode': 'auto', 'layout': {}}}
        with patch.object(settings, 'WINDOW_LAYOUTS', layouts), patch.object(settings, 'CURRENT_LAYOUT', '自动一'), \
                patch.dict(settings.config, {'last_layouts': {'manual': '手动二', 'auto': '自动一'}}):
            self.window.layout_name = '自动一'
            dialog = SettingsDialog(self.window, on_layout_change_callback=self.window.on_layout_changed)
            self.addCleanup(dialog.close)
            self.window._settings_dialog = dialog
            self.window.worker.state = 'ready'
            with patch.object(self.window.worker, 'switch_layout', return_value=63) as switch:
                dialog.btn_layout_manual.click()
                switch.assert_called_once_with('手动二', settings.validate_layout(manual))
                self.assertEqual(settings.CURRENT_LAYOUT, '自动一')
                self.window.worker.command_completed.emit(63, 'switch_layout', False, '失败')
            self.assertEqual(dialog.layout_mode_stack.currentIndex(), 0)
            self.assertEqual(settings.CURRENT_LAYOUT, '自动一')
            self.assertEqual(self.window.layout_name, '自动一')

    def test_mode_ack_persists_both_choices_and_reopens_the_same_layout(self) -> None:
        """模式切换成功原子保存两类历史，重开恢复确认过的模式和名称。"""
        from config.store import read_config
        manual = deepcopy(next(value for value in settings.WINDOW_LAYOUTS.values() if value.get('mode') != 'auto'))
        layouts = {'手动一': manual, '自动一': {'window_title': 'Game A', 'mode': 'auto', 'layout': {}}}
        with patch.object(settings, 'WINDOW_LAYOUTS', layouts), patch.object(settings, 'CURRENT_LAYOUT', '自动一'), \
                patch.dict(settings.config, {'last_layouts': {'manual': '手动一', 'auto': '自动一'}}):
            self.window.layout_name = '自动一'
            dialog = SettingsDialog(self.window, on_layout_change_callback=self.window.on_layout_changed)
            self.addCleanup(dialog.close)
            self.window._settings_dialog = dialog
            self.window.worker.state = 'ready'
            with patch.object(self.window.worker, 'switch_layout', return_value=64):
                dialog.btn_layout_manual.click()
                self.window.worker.command_completed.emit(64, 'switch_layout', True, '')
            saved = read_config(settings.CONFIG_PATH)
            self.assertEqual(saved['current_layout'], '手动一')
            self.assertEqual(saved['last_layouts'], {'manual': '手动一', 'auto': '自动一'})
            # 首次旧配置缺历史时，也应保留切换前的活动自动布局。
            reopened = SettingsDialog(self.window)
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.layout_mode_stack.currentIndex(), 1)
            self.assertEqual(reopened.combo_layout.currentText(), '手动一')
            self.assertEqual(reopened.combo_auto_layout.currentText(), '自动一')

    def test_empty_mode_does_not_silently_apply_another_mode(self) -> None:
        """无自动布局时明确提示新建，不发出手动项或占位名称。"""
        layouts = {'手动一': next(value for value in settings.WINDOW_LAYOUTS.values() if value.get('mode') != 'auto')}
        calls: list[str] = []
        with patch.object(settings, 'WINDOW_LAYOUTS', layouts), patch.object(settings, 'CURRENT_LAYOUT', '手动一'):
            dialog = SettingsDialog(self.window, on_layout_change_callback=calls.append)
            self.addCleanup(dialog.close)
            with patch('ui.settings_dialog.QMessageBox.information') as notice:
                dialog.btn_layout_auto.click()
                notice.assert_called_once()
            self.assertFalse(dialog.combo_auto_layout.isEnabled())
            self.assertEqual(calls, [])

    def test_returning_to_original_mode_before_ack_still_submits_latest_choice(self) -> None:
        """切换尚未回执时点回原模式也发请求，不因旧current_layout误判无操作。"""
        manual = deepcopy(next(value for value in settings.WINDOW_LAYOUTS.values() if value.get('mode') != 'auto'))
        layouts = {'手动一': manual, '自动一': {'window_title': 'Game', 'mode': 'auto', 'layout': {}}}
        calls: list[str] = []
        with patch.object(settings, 'WINDOW_LAYOUTS', layouts), patch.object(settings, 'CURRENT_LAYOUT', '自动一'):
            dialog = SettingsDialog(self.window, on_layout_change_callback=calls.append)
            self.addCleanup(dialog.close)
            dialog.btn_layout_manual.click()
            dialog.btn_layout_auto.click()
            self.assertEqual(calls, ['手动一', '自动一'])

    def test_donation_button_opens_existing_settings_donation_tab(self) -> None:
        """标题栏入口复用正常设置页，不新建二维码页面。"""
        from PySide6.QtWidgets import QDialog
        opened: list[SettingsDialog] = []

        def fake_exec(dialog: SettingsDialog) -> int:
            """检查模态设置当前页并模拟关闭。

            Args:
                dialog: 正常设置对话框实例。
            Returns:
                模拟用户关闭的返回码。
            """
            opened.append(dialog)
            self.assertIs(dialog.tab_widget.currentWidget(), dialog.tab4)
            self.assertTrue(self.window._is_settings_open)
            return QDialog.DialogCode.Rejected

        self.assertFalse(self.window.btn_donation.icon().isNull())
        self.assertEqual(self.window.btn_donation.toolTip(), '赞赏')
        with patch.object(SettingsDialog, 'exec', new=fake_exec):
            self.window.btn_donation.click()
        self.assertEqual(len(opened), 1)
        self.assertFalse(self.window._is_settings_open)
        self.assertIsNone(self.window._settings_dialog)
        opened[0].close()

    def test_settings_keeps_detection_running_and_preserves_manual_pause(self) -> None:
        """设置嵌套事件循环继续检测，关闭不打断连续帧，手动暂停仍有效。"""
        for paused in (False, True):
            with self.subTest(paused=paused):
                self.window.worker.state = 'ready'
                self.window.is_paused = paused
                self.window._busy = False
                self.window.timer.stop()
                self.window._start_timer_if_allowed()

                def fake_exec(dialog: SettingsDialog) -> int:
                    """在设置打开期间检查真实定时器与检测请求入口。

                    Args:
                        dialog: 当前设置窗口。
                    Returns:
                        模拟取消关闭的返回码。
                    """
                    self.assertEqual(self.window.timer.isActive(), not paused)
                    self.window.request_one_update()
                    dialog.deleteLater()
                    return 0

                with patch.object(SettingsDialog, 'exec', new=fake_exec), \
                        patch.object(self.window.worker, 'request_detect', return_value=True) as detect, \
                        patch.object(self.window, '_touch_no_target_time') as touch:
                    self.window._open_settings()
                    self.assertEqual(detect.call_count, int(not paused))
                    touch.assert_not_called()
                self.assertEqual(self.window.is_paused, paused)
                self.assertEqual(self.window.timer.isActive(), not paused)

    def test_donation_entry_reuses_already_open_settings(self) -> None:
        """设置已经打开时只跳页，不再建立第二个设置窗口。"""
        dialog = SettingsDialog(self.window)
        self.addCleanup(dialog.close)
        self.window._settings_dialog = dialog
        with patch('ui.main_window.SettingsDialog') as factory:
            self.window.on_donation_clicked()
            factory.assert_not_called()
        self.assertIs(dialog.tab_widget.currentWidget(), dialog.tab4)

    def test_layout_status_does_not_overwrite_worker_failure(self) -> None:
        """自动定位提示不能覆盖进程异常原因。"""
        self.window.worker.fail("missing model")
        before = self.window.title_bar.status_label.toolTip()
        self.window.on_layout_status_changed("自动布局：已定位")
        self.assertEqual(self.window.title_bar.status_label.toolTip(), before)


if __name__ == "__main__":
    unittest.main()
