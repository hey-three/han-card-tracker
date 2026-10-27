"""以不同布局字体的真实框形状验证尺度自适应，而非样例常量。"""

import unittest

from core.frame_packing import Detection
from core.packed_pipeline import PackedPipeline
from core.packing_scales import AdaptivePackingScales
from config.packing_profile import INITIAL_REGION_SCALES


class PackingScaleTests(unittest.TestCase):
    """验证区域大小独立、字体比例变化、异常过滤与上下文隔离。"""

    def setUp(self) -> None:
        """建立五个不相交区域，测量框尺寸独立于区域尺寸。"""
        self.layout = {'landlord_cards': (.4, .02, .6, .2), 'player_hand': (.05, .65, .95, .95),
                       'opponent_left': (.05, .25, .35, .45), 'opponent_right': (.65, .25, .95, .45),
                       'player_played': (.35, .45, .65, .65)}
        self.names = list(self.layout)
        self.regions = list(self.layout.values())

    def cards(self, hand: float, bottom: float, played: float) -> list[Detection]:
        """在固定区域内生成可控单牌框，模拟不同皮肤的实际检测。

        Args:
            hand: 本家单牌高度。
            bottom: 底牌单牌高度。
            played: 三家出牌单牌高度。
        Returns:
            原图1000×1000坐标中的五张普通牌。
        """
        points = [(480., 70., bottom), (400., 730., hand), (100., 300., played),
                  (750., 300., played), (450., 500., played)]
        return [Detection('A', (x, y, x+20., y+h)) for x, y, h in points]

    def learn(self, cards: list[Detection]) -> AdaptivePackingScales:
        """连续提供三帧真实框，返回已确认配置。

        Args:
            cards: 同一牌桌的原坐标检测框。
        Returns:
            完成三帧尺度确认的学习器。
        """
        profile = AdaptivePackingScales()
        for _ in range(3):
            profile.observe(cards, (1000, 1000), self.names, self.regions)
        return profile

    def test_different_card_fonts_produce_opposite_hand_scaling(self) -> None:
        """同样区域大小，大手牌缩小、小手牌放大，底牌都比普通出牌高12%。"""
        for hand, bottom, played in ((60., 20., 40.), (30., 40., 50.)):
            profile = self.learn(self.cards(hand, bottom, played))
            weights = dict(zip(self.names, profile.weights(self.names)))
            self.assertAlmostEqual(weights['player_hand']*hand, played)
            self.assertAlmostEqual(weights['landlord_cards']*bottom, played*1.12)

    def test_large_joker_box_does_not_change_ordinary_reference(self) -> None:
        """大小王纵长字样不能压小其余普通牌。"""
        cards = self.cards(60., 20., 40.)+[Detection('JOKER', (500., 700., 520., 850.))]
        self.assertAlmostEqual(self.learn(cards).heights['player_hand'], .06)

    def test_no_bottom_keeps_fixed_initial_ratios(self) -> None:
        """无底牌保持固定比例等待，不试探放大；空区中断未确认测量。"""
        profile = AdaptivePackingScales()
        profile.observe(self.cards(60., 20., 40.), (1000, 1000), self.names, self.regions)
        profile.observe([], (1000, 1000), self.names, self.regions)
        profile.observe(self.cards(60., 20., 40.), (1000, 1000), self.names, self.regions)
        self.assertEqual(profile.heights, {})
        for _ in range(30):
            profile.observe([], (1000, 1000), self.names, self.regions)
        self.assertEqual(profile.weights(self.names), [INITIAL_REGION_SCALES[k] for k in self.names])

    def test_hysteresis_ignores_minor_regression_jitter(self) -> None:
        """单牌框不足10%的抖动不会导致重建排列。"""
        profile = self.learn(self.cards(60., 20., 40.))
        before = profile.weights(self.names)
        for _ in range(6):
            profile.observe(self.cards(63., 21., 42.), (1000, 1000), self.names, self.regions)
        self.assertEqual(before, profile.weights(self.names))

    def test_unknown_result_interrupts_measurement_without_losing_confirmed_scale(self) -> None:
        """不能跨未知帧凑够三帧，已有可靠尺度不因推理失败丢失。"""
        profile = self.learn(self.cards(60., 20., 40.))
        before = profile.weights(self.names)
        profile.interrupt()
        self.assertEqual(profile.weights(self.names), before)
        profile = AdaptivePackingScales()
        for _ in range(2):
            profile.observe(self.cards(60., 20., 40.), (1000, 1000), self.names, self.regions)
        profile.interrupt()
        profile.observe(self.cards(60., 20., 40.), (1000, 1000), self.names, self.regions)
        self.assertEqual(profile.heights, {})

    def test_layout_resize_and_reset_do_not_reuse_other_players_measurements(self) -> None:
        """阶段切换保留参照；布局、截图尺寸和显式重置隔离测量。"""
        pipeline = PackedPipeline()
        pipeline.scale_weights((1000, 1000), self.layout, self.names)
        pipeline.scale_profile = self.learn(self.cards(60., 20., 40.))
        self.assertAlmostEqual(pipeline.scale_weights((1000, 1000), self.layout, ['player_hand'])[0], 2/3)
        pipeline.scale_weights((1200, 1000), self.layout, self.names)
        self.assertEqual(pipeline.scale_profile.heights, {})
        pipeline.scale_profile = self.learn(self.cards(60., 20., 40.))
        changed = dict(self.layout, player_hand=(.02, .65, .98, .95))
        self.assertEqual(pipeline.scale_weights((1200, 1000), changed, ['player_hand']), [INITIAL_REGION_SCALES['player_hand']])
        pipeline.reset()
        self.assertEqual(pipeline.scale_profile.heights, {})
