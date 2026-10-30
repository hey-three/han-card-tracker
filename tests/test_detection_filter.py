"""同位置置信度选择、大小王优先与真实诊断首扣回归，不加载模型。"""

import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from config import settings
from core.detection_filter import resolve_duplicate_detections
from core.frame_packing import Detection, PackingPlan, Tile
from core.game_controller import GameController
from core.layout_analyzer import LayoutAnalyzer
from test_packed_pipeline import raw_result


class JokerLetterTests(unittest.TestCase):
    """用已去标识的真实三帧坐标验证误检来源及修复边界。"""

    def test_recorded_packed_boxes_already_contain_letter_and_restore_still_has_it(self) -> None:
        """同一裁片内已经同时存在J和大小王，不是切块产生两个同类副本。"""
        data = json.loads(Path('tests/fixtures/joker_letter_duplicate.json').read_text(encoding='utf-8'))
        for frame in data['opening']:
            tiles = [Tile(tuple(t['source']), t['x'], t['y'], tuple(t['render_size']), tuple(t['source_region']))
                     for t in frame['tiles']]
            plan = PackingPlan(tuple(data['source_size']), tiles, tuple(data['input_size']))
            original = [Detection(c['name'], tuple(c['bbox']), c['confidence']) for c in frame['packed_boxes']]
            restored, _ = plan.restore(original)
            kept, removed = resolve_duplicate_detections(restored)
            self.assertEqual([c.name for c in removed], ['J'])
            self.assertEqual(sum(c.name == 'J' for c in kept), 1)
            self.assertEqual(sum(c.name == 'JOKER' for c in kept), 1)

    def test_joker_beats_overlapping_j_even_with_lower_confidence(self) -> None:
        """J与大小王同位置或文字内嵌时优先大小王；相邻真实J保留。"""
        joker = Detection('JOKER', (100., 100., 150., 250.), .8)
        false_j = Detection('J', (105., 100., 145., 150.), .97)
        normal_j = Detection('J', (155., 100., 195., 160.), .98)
        whole_j = Detection('J', (100., 100., 150., 240.), .9)
        king = Detection('K', false_j.bbox, .9)
        kept, removed = resolve_duplicate_detections([joker, false_j, normal_j, whole_j, king])
        self.assertEqual(removed, [false_j, whole_j])
        self.assertEqual(kept, [joker, normal_j, king])
        uncertain = Detection('joker', joker.bbox, .59)
        self.assertEqual(resolve_duplicate_detections([uncertain, false_j])[1], [false_j])
        for name in ('joker', 'JOKER'):
            low = Detection(name, whole_j.bbox, .2)
            self.assertEqual(resolve_duplicate_detections([whole_j, low])[0], [low])

    def test_generic_conflict_selects_confidence_and_keeps_adjacent_cards(self) -> None:
        """同类/异类都按位置择高分，邻牌和不同位置相同点数保留，同分稳定。"""
        low = Detection('A', (10., 10., 40., 60.), .6)
        high = Detection('K', (11., 10., 41., 60.), .9)
        adjacent = Detection('K', (35., 10., 65., 60.), .8)
        other_row = Detection('A', (10., 80., 40., 130.), .9)
        duplicate = Detection('K', high.bbox, .7)
        kept, removed = resolve_duplicate_detections([low, high, adjacent, other_row, duplicate])
        self.assertEqual(kept, [high, adjacent, other_row])
        self.assertEqual(removed, [low, duplicate])
        tied = Detection('Q', high.bbox, high.confidence)
        self.assertEqual(resolve_duplicate_detections([tied, high])[0], [tied])
        self.assertEqual(resolve_duplicate_detections([high, tied])[0], [high])
        self.assertEqual(resolve_duplicate_detections([]), ([], []))

    def test_custom_joker_class_names_follow_model_mapping(self) -> None:
        """大小王优先使用当前类别映射，不依赖固定训练标签拼写。"""
        jack = Detection('jack', (0., 0., 20., 40.), .99)
        joker = Detection('big', jack.bbox, .3)
        self.assertEqual(resolve_duplicate_detections([jack, joker], joker_labels={'big'}, jack_labels={'jack'})[0],
                         [joker])

    def test_cross_tile_and_user_border_do_not_discard_joker_before_resolution(self) -> None:
        """跨块还原及人工边缘不能提前丢掉低置信度大小王。"""
        first = Tile((100, 100, 300, 200), 0, 0, source_region=(100, 100, 300, 200))
        second = Tile((150, 100, 300, 200), 210, 0, source_region=(150, 100, 300, 200))
        plan = PackingPlan((400, 300), [first, second])
        box = (150., 120., 180., 180.)
        cards, reason = plan.restore([Detection('J', first.project(box), .99),
                                      Detection('JOKER', second.project(box), .3)])
        self.assertEqual(reason, '')
        self.assertEqual([card.name for card in resolve_duplicate_detections(cards)[0]], ['JOKER'])

    def test_controller_filters_before_initial_deduction_on_all_routes(self) -> None:
        """整图、手动拼图及自动入口均先过滤，初始只扣一张J，对手三张J后为零。"""
        data = json.loads(Path('tests/fixtures/joker_letter_duplicate.json').read_text(encoding='utf-8'))
        image = Image.new('RGB', tuple(data['source_size']))
        for mode, packed in (('manual', False), ('manual', True), ('auto', False)):
            with self.subTest(mode=mode, packed=packed), patch.multiple(
                settings, FRAME_LENGTH=3, PACKED_INFERENCE=packed, FULL_DIAGNOSTICS=False,
                SHOW_CURRENT_SCREENSHOT=False, DEBUG_MODE=False,
                WINDOW_LAYOUTS={'Recorded': {'mode': mode, 'window_title': 'Fixture',
                                             'layout': data['layout'] if mode == 'manual' else {}}},
            ), patch.object(GameController, '_create_inferencer', return_value=Mock()), \
                    patch('core.game_controller.ScreenCapture'):
                controller = GameController('Recorded')
                controller.screen_capture.capture_window.return_value = image
                controller.yolo_inferencer.restore_result.side_effect = lambda raw, img, cards: raw_result(list(cards))
                # 使用真实源坐标、布局解析及业务跟踪器，仅替换已完成的模型/拼图边界。
                for frame in [*data['opening'], data['opening'][-1], data['opening'][-1]]:
                    candidates = [Detection(c['name'], tuple(c['bbox']), c['confidence']) for c in frame['boxes']]
                    king = next(c for c in candidates if c.name == 'K')
                    candidates.append(Detection('Q', king.bbox, .1))
                    raw = raw_result(candidates)
                    with patch.object(controller.packed_pipeline, 'detect', return_value=(raw, 1.)):
                        controller.detect()
                self.assertEqual(controller.tracker.state, settings.STARTED_RECORD_CARD)
                self.assertEqual(len(controller.tracker._initial_hand), 17)
                self.assertEqual(controller.tracker.remain_cards['J'], 3)
                if isinstance(controller.layout_analyzer, LayoutAnalyzer):
                    # 沿用已确认的出牌事件回放，不把本家出牌作为补扣途径。
                    for zone in ('opponent_left', 'opponent_right', 'opponent_left'):
                        for _ in range(3):
                            controller.tracker.get_cards_number({'landlord_cards': ['A'], zone: ['J']}, 0.,
                                                                {'landlord_cards', zone})
                        for _ in range(3):
                            controller.tracker.get_cards_number({'landlord_cards': ['A'], zone: []}, 0.,
                                                                {'landlord_cards', zone})
                    self.assertEqual(controller.tracker.remain_cards['J'], 0)


if __name__ == '__main__':
    unittest.main()
