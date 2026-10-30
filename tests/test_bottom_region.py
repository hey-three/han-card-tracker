"""独立底牌区域、真实偏右底牌及学习生命周期回归。"""

import json
from pathlib import Path
import unittest

from core.auto_layout import AutoLayoutAnalyzer, CardBox
from core.bottom_region import BottomRegion
from core.preview_regions import preview_regions
from test_auto_layout import opening


class BottomRegionTests(unittest.TestCase):
    """只用已有检测坐标验证几何，不加载模型。"""

    def test_centered_seventy_percent_uses_card_center(self) -> None:
        """左右15%外及中央翻牌不接受，中心在边界内即接受，不再参考手牌。"""
        inside = [CardBox('A', (.14, .1, .16, .14)), CardBox('K', (.84, .1, .86, .14))]
        outside = [CardBox('A', (.1, .1, .12, .14)), CardBox('K', (.88, .1, .9, .14)),
                   CardBox('Q', (.45, .37, .5, .42))]
        self.assertEqual(BottomRegion().observe(inside+outside), inside)

    def test_two_frames_learn_height_without_requiring_three_cards(self) -> None:
        """一张牌两帧即可学习下沿加半框高，不比较牌点。"""
        region = BottomRegion()
        first = CardBox('A', (.7, .07, .73, .11))
        second = CardBox('three', first.bbox)
        self.assertEqual(region.observe([first]), [first])
        self.assertFalse(region.learned)
        region.observe([second])
        self.assertTrue(region.learned)
        self.assertAlmostEqual(region.bounds[3], .13)
        bounds = region.bounds
        for cards in ([], [CardBox('K', (.7, .075, .73, .105))], []):
            region.observe(cards)
            self.assertEqual(region.bounds, bounds)
        region.interrupt()
        self.assertEqual(region.bounds, bounds)

    def test_unstable_or_interrupted_proposal_requires_consecutive_observations(self) -> None:
        """高度跳变和未知中断不能将两段不连续观测拼成学习确认。"""
        region = BottomRegion()
        low = CardBox('A', (.7, .07, .73, .11))
        high = CardBox('A', (.7, .17, .73, .21))
        region.observe([low])
        region.interrupt()
        region.observe([low])
        self.assertFalse(region.learned)
        region.observe([high])
        self.assertFalse(region.learned)
        region.observe([high])
        self.assertTrue(region.learned)
        self.assertAlmostEqual(region.bounds[3], .23)
        region.reset()
        self.assertFalse(region.learned)
        self.assertEqual(region.bounds, (.15, 0., .85, .25))

    def test_same_frame_calibration_does_not_count_as_two_observations(self) -> None:
        """FRAME_LENGTH=1时校准转跟踪仍只推进一次区域学习。"""
        analyzer = AutoLayoutAnalyzer()
        analyzer.observe(opening(), (1000, 800), 1)
        self.assertTrue(analyzer.ready)
        self.assertEqual(analyzer.bottom_region[3], .25)
        analyzer.observe(opening(), (1000, 800), 1)
        self.assertLess(analyzer.bottom_region[3], .25)

    def test_resize_and_round_reset_relearn_only_bottom_geometry_as_needed(self) -> None:
        """等比尺寸变化保留牌桌几何但重学底牌高度，新局/显式重置也重学。"""
        analyzer = AutoLayoutAnalyzer()
        for _ in range(3):
            analyzer.observe(opening(), (1000, 800), 3)
        self.assertLess(analyzer.bottom_region[3], .25)
        analyzer.observe(opening(), (2000, 1600), 3)
        self.assertTrue(analyzer.ready)
        self.assertEqual(analyzer.bottom_region[3], .25)
        analyzer.observe(opening(), (2000, 1600), 3)
        self.assertLess(analyzer.bottom_region[3], .25)
        analyzer.wait_for_next_round('test')
        self.assertEqual(analyzer.bottom_region[3], .25)

    def test_actual_right_bottom_survives_joker_hand_center_change(self) -> None:
        """真实2879/2881/2885/2887坐标中，补检大王不能让已识别的3变成空底牌。"""
        data = json.loads(Path('tests/fixtures/auto_bottom_right.json').read_text(encoding='utf-8'))
        analyzer = AutoLayoutAnalyzer()
        learned = None
        for entry in data['observations']:
            cards = [CardBox(c['name'], tuple(c['bbox'])) for c in entry['cards']]
            observation = analyzer.observe(cards, tuple(data['size']), 3)
            self.assertIn('three', [c.name for c in observation.regions['landlord_cards']], entry['frame_id'])
            if learned is not None:
                self.assertEqual(analyzer.bottom_region, learned)
            if entry['frame_id'] == 2881:
                learned = analyzer.bottom_region
            metadata = preview_regions({}, observed=observation.regions,
                                       search=analyzer.preview_search_regions,
                                       fixed={'landlord_cards': analyzer.bottom_region})
            self.assertEqual(metadata['landlord_cards'], analyzer.bottom_region)
        self.assertTrue(analyzer.ready)


if __name__ == '__main__':
    unittest.main()
