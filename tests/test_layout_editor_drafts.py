"""布局编辑草稿、同名覆盖及截图目标一致性；本轮按用户要求仅编写未执行。"""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
import yaml

from config import settings
from config.store import read_config
from ui.layout_editor_dialog import LayoutEditorDialog
from utils.layout_editor.coord import REGION_KEYS, sanitize_pixel_rect


class LayoutDraftTests(unittest.TestCase):
    """使用隔离配置和合成图片，不截图、不访问个人布局。

    Attributes:
        editor: 编辑非当前布局乙的真实Qt编辑器。
        path: 工作区临时配置。
    """

    @classmethod
    def setUpClass(cls) -> None:
        """保留Qt应用实例。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """构造甲为当前、乙为编辑目标的配置，检验不会误覆盖活动布局。"""
        directory = self.enterContext(tempfile.TemporaryDirectory(dir='tests/.tmp'))
        self.path = Path(directory)/'config.yaml'
        regions = {key: [.1, .1, .4, .3] for key in REGION_KEYS}
        layouts = {name: {'mode': 'manual', 'window_title': 'Game', 'layout': deepcopy(regions)}
                   for name in ('甲', '乙')}
        self.path.write_text(yaml.safe_dump({'window_layouts': layouts, 'current_layout': '甲'}), encoding='utf-8')
        self.addCleanup(setattr, settings, 'config', deepcopy(settings.config))
        self.enterContext(patch.object(settings, 'CONFIG_PATH', str(self.path)))
        self.enterContext(patch.object(settings, 'WINDOW_LAYOUTS', deepcopy(layouts)))
        self.enterContext(patch.object(settings, 'CURRENT_LAYOUT', '甲'))
        self.enterContext(patch('ui.layout_editor_dialog.list_visible_window_titles', return_value=['Game', 'Other']))
        self.editor = LayoutEditorDialog(initial_layout_name='乙')
        self.enterContext(patch.object(self.editor, '_send_app_windows_to_bottom'))
        self.enterContext(patch.object(self.editor, '_restore_app_windows_after_capture'))

    def tearDown(self) -> None:
        """在撤销临时配置前销毁编辑器及子定时器，不弹放弃修改确认。"""
        self.dispose_editor(self.editor)

    def dispose_editor(self, editor: LayoutEditorDialog) -> None:
        """销毁指定编辑器及延迟事件。

        Args:
            editor: 当前测试创建的编辑器。
        """
        editor._dirty = False
        editor.close()
        editor.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def capture(self, size: tuple[int, int] = (1000, 600)) -> None:
        """使用合成截图走实际编辑器接收流程。

        Args:
            size: 原图尺寸。
        """
        with patch('ui.layout_editor_dialog.capture_window_by_title', return_value=Image.new('RGB', size)):
            self.editor._capture_window()

    def change_hand(self) -> None:
        """模拟框选改变本家手牌，其他牌区保持不变。"""
        rects = self.editor.canvas.get_rects()
        rects['player_hand'] = (50, 60, 400, 180)
        self.editor.canvas.set_rects(rects)
        self.editor._on_canvas_rect_changed('player_hand')

    def test_same_name_edit_overwrites_only_that_layout_and_keeps_current_until_ack(self) -> None:
        """乙同名保存确实更新磁盘和内存，甲及当前布局选择不被提前修改。"""
        self.capture()
        self.change_hand()
        with patch('ui.layout_editor_dialog.QMessageBox.question') as question:
            self.editor._save_layout()
        question.assert_not_called()
        config = read_config(str(self.path))
        self.assertEqual(config['window_layouts']['乙']['layout']['player_hand'], [.05, .1, .4, .3])
        self.assertEqual(config['window_layouts']['甲']['layout']['player_hand'], [.1, .1, .4, .3])
        self.assertEqual(config['current_layout'], '甲')
        self.assertEqual(self.editor.saved_layout_name, '乙')

    def test_recapture_rescales_unsaved_draft_instead_of_reloading_saved_rects(self) -> None:
        """截图尺寸翻倍时草稿框跟随翻倍，不恢复成旧配置。"""
        self.capture()
        self.change_hand()
        self.capture((2000, 1200))
        self.assertEqual(self.editor.canvas.get_rects()['player_hand'], (100, 120, 800, 360))
        self.assertTrue(self.editor._dirty)
        self.assertIsNone(self.editor.saved_layout_name)

    def test_reopening_loads_saved_reference_and_all_regions_without_capturing(self) -> None:
        """保存后重新编辑直接读取本机截图和五区，不调用实时截图接口。"""
        self.capture()
        self.change_hand()
        self.editor._save_layout()
        with patch('ui.layout_editor_dialog.capture_window_by_title') as capture:
            reopened = LayoutEditorDialog(initial_layout_name='乙')
            self.addCleanup(self.dispose_editor, reopened)
            capture.assert_not_called()
        self.assertEqual(reopened.canvas.image_size, (1000, 600))
        self.assertEqual(set(reopened.canvas.get_rects()), set(REGION_KEYS))
        self.assertEqual(reopened.canvas.get_rects()['player_hand'], (50, 60, 400, 180))
        self.assertFalse(reopened._dirty)
        self.assertTrue(reopened.btn_save.isEnabled())

    def test_capture_and_recapture_do_not_resize_or_move_editor(self) -> None:
        """大图在当前视口内缩放，初次/再次截图均保留用户设置的外窗尺寸和位置。"""
        self.editor.show()
        self.app.processEvents()
        geometry = self.editor.geometry()
        self.capture((4000, 2400))
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(self.editor.geometry(), geometry)
        self.editor.resize(1024, 700)
        self.app.processEvents()
        geometry = self.editor.geometry()
        self.capture((1600, 900))
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(self.editor.geometry(), geometry)
        self.assertLessEqual(self.editor.canvas.width(), self.editor.canvas_scroll.viewport().width())
        self.assertLessEqual(self.editor.canvas.height(), self.editor.canvas_scroll.viewport().height())

    def test_changed_target_requires_fresh_capture_and_does_not_reuse_other_windows_boxes(self) -> None:
        """切换窗口后禁止保存旧图，成功截取新窗口时从空牌区开始。"""
        self.capture()
        self.editor.combo_window_title.setCurrentText('Other')
        self.assertFalse(self.editor.btn_save.isEnabled())
        layout, error = self.editor._build_normalized_layout()
        self.assertIsNone(layout)
        self.assertIn('目标窗口', error or '')
        self.capture()
        self.assertEqual(self.editor._captured_window_title, 'Other')
        self.assertEqual(self.editor.canvas.get_rects(), {})

    def test_capture_failure_keeps_existing_unsaved_boxes(self) -> None:
        """截图报错保留已有图和草稿，恢复截图按钮并显示原因。"""
        self.capture()
        self.change_hand()
        before = self.editor.canvas.get_rects()
        with patch('ui.layout_editor_dialog.capture_window_by_title', side_effect=OSError('capture failed')), \
                patch('ui.layout_editor_dialog.QMessageBox.warning') as warning:
            self.editor._capture_window()
        self.assertEqual(self.editor.canvas.get_rects(), before)
        self.assertFalse(self.editor._capturing)
        self.assertTrue(self.editor.btn_capture.isEnabled())
        warning.assert_called_once()

    def test_reverse_drag_can_cover_the_complete_right_and_bottom_edges(self) -> None:
        """从右下向左上框选可以完整覆盖全图，不丢失最右列或最下行。"""
        self.assertEqual(sanitize_pixel_rect((1200, 800, -10, -20), 1000, 600), (0, 0, 1000, 600))

    def test_direct_save_still_rejects_incomplete_regions(self) -> None:
        """移除独立预览后，直接保存仍阻止缺少任何牌区的配置写盘。"""
        self.capture()
        rects = self.editor.canvas.get_rects()
        del rects['landlord_cards']
        self.editor.canvas.set_rects(rects)
        with patch('ui.layout_editor_dialog.save_layout') as save, \
                patch('ui.layout_editor_dialog.QMessageBox.warning') as warning:
            self.editor._save_layout()
        save.assert_not_called()
        warning.assert_called_once()
        self.assertIsNone(self.editor.saved_layout_name)
