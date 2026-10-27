"""验证底牌可选、发牌增补及自动换局，贯穿真实 Controller/布局/Tracker。"""

from collections import Counter
from collections.abc import Sequence
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from config import settings
from core.auto_layout import AutoLayoutAnalyzer, CardBox
from core.game_controller import GameController
from core.games.doudizhu import DoudizhuTracker
from core.frame_packing import Detection
from core.layout_analyzer import LayoutAnalyzer
from core.square_packing import make_square_plan
from test_auto_layout import opening, row
from test_packed_pipeline import raw_result


class RoundLifecycleTests(unittest.TestCase):
    """用模拟模型输出检验实际流水线，模型和窗口捕获不参与测试。"""

    def setUp(self) -> None:
        """创建真实自动布局控制器，隔离配置和所有图片写入。"""
        self.enterContext(patch.multiple(settings, FRAME_LENGTH=3, SAVE_DEBUG_IMAGES=False,
                                        SHOW_CURRENT_SCREENSHOT=False, DEBUG_MODE=False, PACKED_INFERENCE=False,
                                        RESET_TIME=3.))
        config = settings.validate_layout({"window_title": "Game", "mode": "auto", "layout": {}})
        self.enterContext(patch.object(settings, "WINDOW_LAYOUTS", {"Auto": config}))
        with patch.object(GameController, "_create_inferencer", return_value=Mock()), patch(
            "core.game_controller.ImageSaver"
        ), patch("core.game_controller.ScreenCapture"):
            self.controller = GameController("doudizhu", "Auto")
        self.hand = opening()[:-3]
        self.image = Image.new("RGB", (1000, 800))
        self.manual_plan = None
        self.now = 0.
        self.enterContext(patch("core.games.doudizhu.monotonic", side_effect=lambda: self.now))
        self.feed(self.hand)

    @property
    def tracker(self) -> DoudizhuTracker:
        """返回控制器使用的真实斗地主跟踪器。"""
        assert isinstance(self.controller.tracker, DoudizhuTracker)
        return self.controller.tracker

    def feed(self, cards: Sequence[CardBox], count: int = 3, captured: bool = True,
             result: bool = True, step: float = 1.5) -> None:
        """把原图坐标的模拟 YOLO 输出交给完整流水线。

        Args:
            cards: 本轮检测框，空序列代表成功识别后无牌。
            count: 连续帧数。
            captured: False 模拟截图失败。
            result: False 模拟模型没有返回结果，不等同有效空检测。
            step: 相邻截图的单调时钟间隔，无需实际等待。
        """
        width, height = self.image.size
        detections = [Detection(c.name, (c.bbox[0]*width, c.bbox[1]*height,
                                        c.bbox[2]*width, c.bbox[3]*height)) for c in cards]
        raw = raw_result(detections)
        if self.manual_plan is not None:
            projected = []
            for tile in self.manual_plan.tiles:
                a, b, c, d = tile.source
                for card in detections:
                    x1, y1, x2, y2 = card.bbox
                    cropped = (max(a, x1), max(b, y1), min(c, x2), min(d, y2))
                    if cropped[0] < cropped[2] and cropped[1] < cropped[3]:
                        projected.append(Detection(card.name, tile.project(cropped)))
            self.controller.yolo_inferencer.detect_packed.return_value = (raw_result(projected) if result else None, 1.)
            self.controller.yolo_inferencer.restore_result.side_effect = lambda raw, image, cards: raw_result(list(cards))
        self.controller.yolo_inferencer.detect.return_value = (raw if result else None, 1.)
        self.controller.screen_capture.capture_window.return_value = self.image if captured else None
        for _ in range(count):
            self.now += step
            self.controller.detect()

    def use_manual_layout(self) -> None:
        """切换为真实五区手动分析，模拟模型同时支持拼图与整图复查。"""
        config = settings.validate_layout({"window_title": "Game", "mode": "manual", "layout": {
            "player_hand": [.04, .7, .96, .86], "player_played": [.04, .46, .96, .66],
            "opponent_left": [.08, .25, .48, .45], "opponent_right": [.5, .25, .94, .45],
            "landlord_cards": [.35, 0., .6, .2],
        }})
        self.controller.layout_config = config
        self.controller.layout_analyzer = LayoutAnalyzer(config)
        self.controller.reset()
        self.manual_plan = make_square_plan(self.image.size, list(config["layout"].values()))
        self.feed(self.hand)

    def available(self, count: int, excluded: Sequence[str] = ()) -> list[str]:
        """从不包含本家手牌和已指定牌的合法牌组中取测试对手牌。

        Args:
            count: 需要的张数。
            excluded: 其他玩家已持有或打出的牌，使用 YOLO 标签。

        Returns:
            不超出牌组数量的 YOLO 标签列表。
        """
        deck = Counter({label: settings.TOTAL_CARDS[card] for label, card in settings.YOLO_TO_CARD_MAPPING.items()})
        return list((deck - Counter(c.name for c in self.hand) - Counter(excluded)).elements())[:count]

    def use_twenty(self) -> None:
        """重建本家为地主的二十张开局。"""
        self.controller.reset()
        self.hand = row(["three"]*4 + ["four"]*4 + ["five"]*4 + ["six"]*4 + ["seven"]*4, .1, .76)
        self.feed(self.hand)

    def add_left_play(self) -> None:
        """记下一张有效对手出牌，为显示保留断言提供非空历史。"""
        self.feed(self.hand + row(["A"], .22, .37))
        self.assertEqual(self.tracker.show_cards["left"], [["A"]])

    def test_missing_bottom_opens_and_tracks_without_templates(self) -> None:
        """完全没有底牌时，完整手牌校准后仍正确记录三家出牌。"""
        self.assertEqual(sum(self.tracker.remain_cards.values()), 37)
        self.assertTrue(self.controller.layout_analyzer.ready)
        self.feed(self.hand + row(["A"], .22, .37) + row(["K"], .47, .57) + row(["Q"], .76, .37))
        self.assertEqual(self.tracker.show_cards, {"left": [["A"]], "self": [["K"]], "right": [["Q"]]})
        self.controller.yolo_inferencer.detect_packed.assert_not_called()

    def test_partial_and_flickering_bottom_do_not_block_opening(self) -> None:
        """底牌零、一、二、三张及牌面变化不影响完整手牌的连续确认。"""
        self.controller.reset()
        for names in ([], ["A"], ["K", "Q"]):
            self.feed(self.hand + row(names, .43, .1, .035, .035), 1)
        self.assertTrue(self.controller.layout_analyzer.ready)
        self.add_left_play()
        before = deepcopy((self.tracker.remain_cards, self.tracker.show_cards))
        for names in (["A", "K", "Q"], ["two"], [], ["J", "ten", "nine"]):
            self.feed(self.hand + row(names, .43, .1, .035, .035))
        self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), before)

    def test_visible_play_prevents_initializing_from_midgame_hand(self) -> None:
        """即使手牌恰好十七张，已出现出牌也不当作完整开局。"""
        self.controller.reset()
        self.feed(self.hand + row(["A"], .22, .37), 8)
        self.assertFalse(self.controller.layout_analyzer.ready)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 54)

    def test_empty_end_preserves_display_until_stable_new_hand(self) -> None:
        """三帧无牌结束后保留历史，不完整或未稳定的新手牌不刷新显示。"""
        self.add_left_play()
        previous = deepcopy((self.tracker.remain_cards, self.tracker.show_cards))
        self.feed([], 2)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.feed([], 1)
        self.assertTrue(self.tracker.awaiting_next_round)
        self.assertFalse(self.controller.layout_analyzer.ready)
        self.feed(self.hand[:16], 5)
        self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), previous)
        self.feed(self.hand, 2)
        self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), previous)
        self.feed(self.hand, 1)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 37)
        self.assertTrue(all(not history for history in self.tracker.show_cards.values()))

    def test_capture_and_result_failures_interrupt_empty_streak(self) -> None:
        """截图失败和结果缺失不算无牌，并中断连续无牌计数。"""
        for kwargs in ({"captured": False}, {"result": False}):
            with self.subTest(kwargs=kwargs):
                self.feed([], 2)
                self.feed([], 6, **kwargs)
                self.assertFalse(self.tracker.awaiting_next_round)
                self.feed([], 2)
                self.assertFalse(self.tracker.awaiting_next_round)
                self.feed(self.hand, 1)

    def test_visible_cards_break_empty_streak_and_threshold_can_change(self) -> None:
        """可见牌打断无牌累计，六帧阈值不会在三帧提前结束。"""
        settings.FRAME_LENGTH = 6
        self.feed([], 5)
        self.feed(self.hand, 1)
        self.feed([], 5)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.feed([], 1)
        self.assertTrue(self.tracker.awaiting_next_round)

    def test_unknown_timeout_never_erases_automatic_display(self) -> None:
        """长时间截图失败仍保留当前显示，不用旧超时逻辑清成整副牌。"""
        self.add_left_play()
        previous = deepcopy((self.tracker.remain_cards, self.tracker.show_cards))
        self.tracker.no_target_time = 0.
        self.feed([], 10, captured=False)
        self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), previous)
        self.assertFalse(self.tracker.awaiting_next_round)

    def test_self_zero_does_not_end_while_cards_are_visible(self) -> None:
        """本家累计出完也不判结束，只有全图连续无牌才进入等待。"""
        names = [c.name for c in self.hand]
        self.feed(self.hand[16:] + row(names[:16], .35, .57, .018))
        self.feed(row(names[16:], .47, .57))
        self.assertEqual(sum(map(len, self.tracker.show_cards["self"])), 17)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.feed(self.hand, 8)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.feed([])
        self.assertTrue(self.tracker.awaiting_next_round)

    def test_opponent_zero_does_not_end_while_cards_are_visible(self) -> None:
        """对手明确出完十七张时仍等全图无牌，不再推算地主分配。"""
        self.use_twenty()
        self.feed(self.hand + row(self.available(17), .22, .37, .016))
        self.assertEqual(len(self.tracker.show_cards["left"][0]), 17)
        self.assertFalse(self.tracker.awaiting_next_round)

    def test_cards_anywhere_prevent_global_empty_end(self) -> None:
        """即使手牌与出牌区都空，画面任意位置仍有牌就不能判全图无牌。"""
        self.feed(row(["A"], .02, .02), 12)
        self.assertFalse(self.tracker.awaiting_next_round)

    def test_many_fast_empty_frames_still_require_duration(self) -> None:
        """帧数足够但空白不到三秒时，不能将动画短暂消牌当作结束。"""
        self.feed([], 20, step=.1)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.feed([], 1, step=1.2)
        self.assertTrue(self.tracker.awaiting_next_round)

    def test_pause_duration_is_not_counted_as_empty_observation(self) -> None:
        """暂停后恢复要重新累计，不能用暂停的墙钟时间凑够结束阈值。"""
        self.feed([], 2)
        self.now += 100
        self.controller.touch_time()
        self.feed([], 1)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.feed([], 2)
        self.assertTrue(self.tracker.awaiting_next_round)

    def test_seventeen_to_twenty_deducts_only_the_added_cards(self) -> None:
        """发牌阶段十七增至二十只扣新增三张，持续可见时不重复扣牌。"""
        upgraded = row([c.name for c in self.hand] + ["seven"]*3, .1, .76)
        self.feed(upgraded, 2)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 37)
        self.feed(upgraded, 1)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 34)
        self.feed(upgraded, 8)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 34)
        self.assertEqual(len(self.tracker._initial_hand), 20)

    def test_upgrade_is_rejected_after_any_play(self) -> None:
        """已经有出牌后不再改写初始手牌，防止中途误检影响统计。"""
        self.add_left_play()
        previous = self.tracker.remain_cards.copy()
        upgraded = row([c.name for c in self.hand] + ["seven"]*3, .1, .76)
        self.feed(upgraded, 8)
        self.assertEqual(self.tracker.remain_cards, previous)
        business = [settings.YOLO_TO_CARD_MAPPING[c.name] for c in upgraded]
        self.assertFalse(self.tracker.update_initial_hand(business))

    def test_upgrade_requires_consecutive_valid_frames(self) -> None:
        """结果缺失打断二十张增补确认，恢复后重新累计三帧。"""
        upgraded = row([c.name for c in self.hand] + ["seven"]*3, .1, .76)
        self.feed(upgraded, 2)
        self.feed([], 1, result=False)
        self.feed(upgraded, 2)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 37)
        self.feed(upgraded, 1)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 34)

    def test_explicit_reset_still_clears_display_immediately(self) -> None:
        """用户手动重置与自动等待不同，明确重置后立即清空。"""
        self.add_left_play()
        self.feed([])
        self.controller.reset()
        self.assertFalse(self.tracker.awaiting_next_round)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 54)
        self.assertTrue(all(not history for history in self.tracker.show_cards.values()))

    def test_empty_packed_fallback_counts_one_frame_not_two(self) -> None:
        """拼图和整图复查属于同一帧，不能两次累计无牌提前结束。"""
        self.use_manual_layout()
        self.feed([], 2)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.assertEqual(self.tracker._empty_frames, 2)
        self.assertEqual(self.controller.yolo_inferencer.detect_packed.call_args.args[0].size, (640, 640))
        self.feed([], 1)
        self.assertTrue(self.tracker.awaiting_next_round)

    def test_manual_route_works_with_legacy_preference_false_and_bottom_missing(self) -> None:
        """旧配置关闭拼图也不能覆盖新路线；底牌缺失不阻碍手动完整手牌开局。"""
        self.use_manual_layout()
        self.assertFalse(settings.PACKED_INFERENCE)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 37)
        self.assertIn("手动五区拼图", self.controller.packed_pipeline.route)
        self.add_left_play()

    def test_manual_empty_end_preserves_display_until_stable_new_hand(self) -> None:
        """手动模式同样保留旧牌数及历史，下一局十七张连续稳定后才清屏。"""
        self.use_manual_layout()
        self.add_left_play()
        previous = deepcopy((self.tracker.remain_cards, self.tracker.show_cards))
        self.feed([])
        self.assertTrue(self.tracker.awaiting_next_round)
        self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), previous)
        self.feed(self.hand[:16], 5)
        self.feed(self.hand, 2)
        self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), previous)
        self.feed(self.hand, 1)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 37)
        self.assertTrue(all(not cards for cards in self.tracker.show_cards.values()))

    def test_manual_empty_crops_with_a_card_outside_do_not_end(self) -> None:
        """五区都无牌但框外有牌时，整图复查阻止错误结束。"""
        self.use_manual_layout()
        self.feed(row(["A"], .97, .01), 8)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.assertEqual(self.controller.packed_pipeline.timings["model_calls"], 2.)

    def test_manual_unknown_capture_does_not_clear_display_or_finish(self) -> None:
        """手动模式截图失败也不是无牌；旧超时不得清除上一份有效显示。"""
        self.use_manual_layout()
        self.add_left_play()
        previous = deepcopy((self.tracker.remain_cards, self.tracker.show_cards))
        self.feed([], 2)
        self.feed([], 20, captured=False)
        self.feed([], 2)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), previous)

    def test_auto_ignores_legacy_packing_preference_even_after_calibration(self) -> None:
        """自动布局无论旧开关为何值，校准后仍整图推理。"""
        settings.PACKED_INFERENCE = True
        self.feed(self.hand, 8)
        self.controller.yolo_inferencer.detect_packed.assert_not_called()
        self.assertIs(self.controller.yolo_inferencer.detect.call_args.args[0], self.image)

    def test_pause_breaks_pending_new_hand_confirmation(self) -> None:
        """等待新局时，暂停前两帧与恢复后一帧不能合并成稳定开局。"""
        for manual in (False, True):
            with self.subTest(manual=manual):
                if manual:
                    self.use_manual_layout()
                self.feed([])
                self.assertTrue(self.tracker.awaiting_next_round)
                self.feed(self.hand, 2)
                self.controller.touch_time()
                self.feed(self.hand, 1)
                self.assertTrue(self.tracker.awaiting_next_round)
                self.feed(self.hand, 2)
                self.assertFalse(self.tracker.awaiting_next_round)

    def test_separated_partial_bottom_candidates_are_optional(self) -> None:
        """上方两张底牌因间隔拆为两组也不阻塞开局。"""
        self.controller.reset()
        self.feed(self.hand + row(["A", "K"], .4, .1, .15, .035))
        self.assertTrue(self.controller.layout_analyzer.ready)

    def test_geometry_changes_preserve_display_while_recalibrating(self) -> None:
        """窗口比例变化中断旧跟踪，重新定位期间仍保留上一份显示。"""
        self.add_left_play()
        previous = deepcopy((self.tracker.remain_cards, self.tracker.show_cards))
        self.image = Image.new("RGB", (1600, 900))
        self.feed([], 1)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.assertFalse(self.controller.layout_analyzer.ready)
        self.feed(self.hand)
        self.assertTrue(self.controller.layout_analyzer.ready)
        self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), previous)


if __name__ == "__main__":
    unittest.main()
