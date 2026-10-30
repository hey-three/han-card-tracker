"""本地预览元数据与GUI叠加不污染模型输入的回归。"""

from io import BytesIO
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QWidget

from core.auto_layout import AutoLayoutAnalyzer, CardBox
from ui.window_metrics import COMPACT_WINDOW_WIDTH
from core.frame_packing import PackingPlan, Tile
from core.preview_regions import preview_regions
from core.square_packing import make_rect_plan
from ui.screenshot_preview import ScreenshotPreviewDialog


class PreviewRegionTests(unittest.TestCase):
    """验证标注真实来源、区域完整性、裁片归属与原图无修改。"""

    @classmethod
    def setUpClass(cls) -> None:
        """保留Qt应用供显示副本绘制。"""
        cls.app = QApplication.instance() or QApplication([])

    def test_manual_full_returns_saved_boxes_without_modifying_them(self) -> None:
        """人工边界保持原值，不外扩、不按模型尺寸换坐标。"""
        layout = {'player_hand': [.1, .6, .9, .9], 'landlord_cards': [.4, .1, .6, .2]}
        actual = preview_regions(layout)
        self.assertEqual(actual, {key: tuple(value) for key, value in layout.items()})
        actual.clear()
        self.assertEqual(len(layout), 2)

    def test_actual_painter_uses_one_character_per_region(self) -> None:
        """原图、搜索提示和拼图均实际绘制单字，不附加搜索或长名称。"""
        dialog = ScreenshotPreviewDialog()
        captured: list[str] = []
        original = QPainter.drawText

        def record(painter: QPainter, rect: QRect, flags: Qt.AlignmentFlag, text: str) -> None:
            """记录本次真实绘制文字后调用原始绘制。

            Args:
                painter: 当前绘图器。
                rect: 文字位置。
                flags: 对齐方式。
                text: 实际文字。
            """
            captured.append(text)
            original(painter, rect, flags, text)

        try:
            dialog.resize(800, 450)
            dialog.show()
            self.app.processEvents()
            stream = BytesIO()
            Image.new('RGB', (800, 400), (30, 40, 50)).save(stream, format='JPEG')
            regions = {'opponent_left': (.05, .2, .2, .4), 'opponent_right': (.25, .2, .4, .4),
                       'player_played': (.45, .2, .6, .4), 'packed:0:landlord_cards': (.65, .2, .8, .4),
                       'search:player_hand': (.05, .65, .8, .9)}
            with patch.object(QPainter, 'drawText', new=record):
                dialog.update_image(stream.getvalue(), regions, {'landlord_cards': '地主底牌'})
            self.assertEqual(captured, ['上', '下', '本', '底', '手'])
        finally:
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_preview_default_width_does_not_follow_horizontal_parent(self) -> None:
        """横排主窗口不能放大预览默认宽度，预览仍允许用户调大。"""
        parent = QWidget()
        parent.resize(900, 100)
        dialog = ScreenshotPreviewDialog(parent)
        try:
            self.assertEqual(dialog.width(), COMPACT_WINDOW_WIDTH)
            dialog.resize(720, 450)
            self.assertEqual(dialog.width(), 720)
        finally:
            parent.close()
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_search_regions_exist_before_cards_without_becoming_business_observations(self) -> None:
        """空画面也画宽泛搜索带，但不会据此判定看到了底牌或手牌。"""
        analyzer = AutoLayoutAnalyzer()
        observation = analyzer.observe([], (1800, 1072), 3)
        overlays = preview_regions({}, observed=observation.regions, search=analyzer.preview_search_regions,
                                   fixed={'landlord_cards': analyzer.bottom_region})
        self.assertEqual(set(overlays), {'landlord_cards', 'search:player_hand'})
        self.assertEqual(overlays['landlord_cards'], (.15, 0., .85, .25))
        self.assertFalse(observation.regions.get('player_hand'))
        self.assertFalse(observation.regions.get('landlord_cards'))
        self.assertFalse(analyzer.ready)

    def test_auto_boxes_enclose_complete_group_and_skip_unknown_or_empty_roles(self) -> None:
        """自动牌区合并整组两行，而非逐张画框；不猜测未确认玩家区域。"""
        cards = [CardBox('A', (.1, .2, .2, .3)), CardBox('K', (.2, .4, .4, .5))]
        actual = preview_regions({}, observed={'opponent_left': cards, 'opponent_right': []})
        self.assertEqual(actual, {'opponent_left': (.1, .2, .4, .5)})

    def test_opening_and_recording_tiles_follow_actual_plan_bounds(self) -> None:
        """两区与四区均逐个标注实际裁片，同一区拆成多片也不漏归属。"""
        layout = {'landlord_cards': (.35, .03, .65, .12), 'player_hand': (.05, .7, .95, .9),
                  'player_played': (.3, .5, .7, .64), 'opponent_left': (.05, .3, .35, .5),
                  'opponent_right': (.65, .3, .95, .5)}
        for roles in (('landlord_cards', 'player_hand'),
                      ('landlord_cards', 'player_played', 'opponent_left', 'opponent_right')):
            selected = {key: layout[key] for key in roles}
            plan = make_rect_plan((1600, 900), list(selected.values()), [1.]*len(roles), (960, 608))
            actual = preview_regions(selected, plan=plan)
            self.assertEqual(len(actual), len(plan.tiles))
            self.assertEqual({key.split(':', 2)[-1] for key in actual}, set(roles))
            for bounds, tile in zip(actual.values(), plan.tiles):
                self.assertEqual(bounds, tuple(value/(960 if index%2 == 0 else 608)
                                               for index, value in enumerate(tile.bounds)))

    def test_identical_source_regions_are_not_arbitrarily_mislabeled(self) -> None:
        """人工框完全重叠时保留两个名称，不任意宣称唯一归属。"""
        layout = {'landlord_cards': (.2, .2, .8, .5), 'player_hand': (.2, .2, .8, .5)}
        plan = make_rect_plan((1000, 800), list(layout.values()), [1., 1.], (960, 608))
        actual = preview_regions(layout, plan=plan)
        self.assertTrue(actual)
        self.assertTrue(all(key.endswith('landlord_cards+player_hand') for key in actual))

    def test_split_hand_labels_each_overlapping_piece(self) -> None:
        """手牌保护重叠切成两片时，各片独立标名称且使用自己的目标坐标。"""
        source = (100, 360, 900, 540)
        tiles = [Tile((100, 360, 550, 540), 10, 20, (225, 90), source),
                 Tile((450, 360, 900, 540), 300, 20, (225, 90), source)]
        plan = PackingPlan((1000, 600), tiles, (960, 608))
        actual = preview_regions({'player_hand': (.1, .6, .9, .9)}, plan=plan)
        self.assertEqual(set(actual), {'packed:0:player_hand', 'packed:1:player_hand'})
        self.assertEqual(actual['packed:0:player_hand'], (10/960, 20/608, 235/960, 110/608))
        self.assertEqual(actual['packed:1:player_hand'], (300/960, 20/608, 525/960, 110/608))

    def test_original_has_outline_but_packed_only_has_label_and_source_remains_clean(self) -> None:
        """同一显示位置仅原图画边框，标签只绘在副本，原JPEG及源像素都不变。"""
        dialog = ScreenshotPreviewDialog()
        try:
            dialog.resize(820, 450)
            dialog.show()
            self.app.processEvents()
            stream = BytesIO()
            Image.new('RGB', (800, 400), (30, 40, 50)).save(stream, format='JPEG')
            payload = stream.getvalue()
            rect = (.1, .2, .9, .8)
            labels = {'player_hand': '本家手牌'}
            dialog.update_image(payload, {'player_hand': rect}, labels)
            image = dialog.image_label.pixmap().toImage()
            x, y = round(image.width()*.1), round(image.height()*.5)
            original_edge = image.pixelColor(x, y)
            self.assertGreater(original_edge.green(), original_edge.red()+40)
            source = dialog.image_label._source_pixmap.toImage()
            self.assertLess(source.pixelColor(80, 200).green(), 60)
            dialog.update_image(payload, {'packed:0:player_hand': rect}, labels, '手动两区拼图')
            packed = dialog.image_label.pixmap().toImage()
            self.assertLess(packed.pixelColor(x, y).green(), 60)
            tag = packed.pixelColor(x+2, round(packed.height()*.2)+3)
            self.assertGreater(tag.green(), 60)
            self.assertEqual(payload, stream.getvalue())
            dialog.reset_frame('等待')
            self.assertFalse(dialog.image_label._regions)
        finally:
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
