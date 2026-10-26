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
from test_auto_layout import opening, row
from test_packed_pipeline import raw_result


class RoundLifecycleTests(unittest.TestCase):
    """用模拟模型输出检验实际流水线，模型和窗口捕获不参与测试。"""

    def setUp(self) -> None:
        """创建真实自动布局控制器，隔离配置和所有图片写入。"""
        self.enterContext(patch.multiple(settings, FRAME_LENGTH=3, SAVE_DEBUG_IMAGES=False,
                                        SHOW_CURRENT_SCREENSHOT=False, DEBUG_MODE=False, PACKED_INFERENCE=False))
        config = settings.validate_layout({"window_title": "Game", "mode": "auto", "layout": {}})
        self.enterContext(patch.object(settings, "WINDOW_LAYOUTS", {"Auto": config}))
        with patch.object(GameController, "_create_inferencer", return_value=Mock()), patch(
            "core.game_controller.ImageSaver"
        ), patch("core.game_controller.ScreenCapture"):
            self.controller = GameController("doudizhu", "Auto")
        self.hand = opening()[:-3]
        self.image = Image.new("RGB", (1000, 800))
        self.feed(self.hand)

    @property
    def tracker(self) -> DoudizhuTracker:
        """返回控制器使用的真实斗地主跟踪器。"""
        assert isinstance(self.controller.tracker, DoudizhuTracker)
        return self.controller.tracker

    def feed(self, cards: Sequence[CardBox], count: int = 3, captured: bool = True, result: bool = True) -> None:
        """把原图坐标的模拟 YOLO 输出交给完整流水线。

        Args:
            cards: 本轮检测框，空序列代表成功识别后无牌。
            count: 连续帧数。
            captured: False 模拟截图失败。
            result: False 模拟模型没有返回结果，不等同有效空检测。
        """
        names = dict(enumerate(dict.fromkeys(card.name for card in cards)))
        ids = {name: index for index, name in names.items()}
        raw = Mock()
        raw.names = names
        width, height = self.image.size
        raw.boxes.xyxy.cpu.return_value.tolist.return_value = [
            [c.bbox[0]*width, c.bbox[1]*height, c.bbox[2]*width, c.bbox[3]*height] for c in cards
        ]
        raw.boxes.cls.cpu.return_value.tolist.return_value = [ids[c.name] for c in cards]
        self.controller.yolo_inferencer.detect.return_value = (raw if result else None, 1.)
        self.controller.screen_capture.capture_window.return_value = self.image if captured else None
        for _ in range(count):
            self.controller.detect()

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
        regions, _ = self.controller.layout_analyzer.inference_regions()
        self.assertIsNotNone(regions)

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

    def test_self_zero_ends_and_old_opening_does_not_restart(self) -> None:
        """本家确认出完后冻结显示，即便旧完整手牌重新出现也不重开旧局。"""
        names = [c.name for c in self.hand]
        self.feed(self.hand[16:] + row(names[:16], .35, .57, .018))
        self.feed(row(names[16:], .47, .57))
        self.assertTrue(self.tracker.awaiting_next_round)
        self.assertEqual(self.tracker.remaining_by_player["self"], 0)
        previous = deepcopy((self.tracker.remain_cards, self.tracker.show_cards))
        self.feed(self.hand, 8)
        self.assertTrue(self.tracker.awaiting_next_round)
        self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), previous)
        new_hand = [CardBox("eight", c.bbox) if i == 16 else c for i, c in enumerate(self.hand)]
        self.feed(new_hand, 2)
        self.assertEqual((self.tracker.remain_cards, self.tracker.show_cards), previous)
        self.feed(new_hand, 1)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 37)
        self.assertEqual(self.tracker.show_cards["self"], [])

    def test_opponent_zero_with_known_initial_count(self) -> None:
        """本家地主时对手明确十七张，累计确认出完即可换局。"""
        self.use_twenty()
        names = self.available(17)
        self.feed(self.hand + row(names, .22, .37, .016))
        self.assertTrue(self.tracker.awaiting_next_round)
        self.assertEqual(self.tracker.remaining_by_player["left"], 0)
        self.assertEqual(len(self.tracker.show_cards["left"][0]), 17)

    def test_end_frame_hand_dropout_still_blocks_old_seventeen(self) -> None:
        """结束帧恰好漏检本家手牌，仍记住上一组十七张以阻止旧画面重开。"""
        self.use_twenty()
        old_hand = self.hand
        current = row([c.name for c in old_hand][3:], .1, .76, .047)
        self.feed(current + row([c.name for c in old_hand][:3], .45, .57))
        opponent = self.available(17)
        self.feed(current + row(opponent[:16], .22, .37, .016))
        self.feed(row(opponent[16:], .22, .37), 3)
        self.assertTrue(self.tracker.awaiting_next_round)
        self.feed(current, 6)
        self.assertTrue(self.tracker.awaiting_next_round)

    def test_unknown_landlord_seventeen_is_not_assumed_zero(self) -> None:
        """本家十七张且对手身份未知时，累计十七张仍可能剩三张。"""
        left = self.available(17)
        self.feed(self.hand + row(left, .22, .37, .016))
        self.assertFalse(self.tracker.awaiting_next_round)
        self.assertIsNone(self.tracker.remaining_by_player["left"])
        right = self.available(18, left)
        self.feed(self.hand + row(left, .22, .37, .016) + row(right, .60, .37, .016))
        self.assertTrue(self.tracker.awaiting_next_round)
        self.assertEqual(self.tracker.remaining_by_player["left"], 0)

    def test_twenty_opponent_cards_resolve_landlord_and_end(self) -> None:
        """身份起初未知的对手累计二十张，仍可确定为地主并确认出完。"""
        self.feed(self.hand + row(self.available(20), .22, .37, .016))
        self.assertTrue(self.tracker.awaiting_next_round)
        self.assertEqual(self.tracker.remaining_by_player["left"], 0)

    def test_invalid_overdraw_does_not_invent_a_zero(self) -> None:
        """出牌张数矛盾时返回未知，不能由错误负数或错误分配触发换局。"""
        self.use_twenty()
        self.tracker.show_cards["left"] = [["A"]*18]
        self.tracker.show_cards["right"] = [["K"]*17]
        self.assertTrue(all(value is None for value in self.tracker.remaining_by_player.values()))
        self.tracker.get_cards_number({}, 1., set())
        self.assertFalse(self.tracker.awaiting_next_round)

    def test_seventeen_to_twenty_deducts_only_the_added_cards(self) -> None:
        """发牌阶段十七增至二十只扣新增三张，持续可见时不重复扣牌。"""
        upgraded = row([c.name for c in self.hand] + ["seven"]*3, .1, .76)
        self.feed(upgraded, 2)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 37)
        self.feed(upgraded, 1)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 34)
        self.feed(upgraded, 8)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 34)
        self.assertEqual(self.tracker.remaining_by_player, {"self": 20, "left": 17, "right": 17})

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
        settings.PACKED_INFERENCE = True
        self.feed(self.hand, 1)
        self.controller.yolo_inferencer.detect_packed.return_value = (raw_result([]), 1.)
        self.feed([], 2)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.assertEqual(self.controller.yolo_inferencer.detect_packed.call_args.args[0].size, (640, 640))
        self.feed([], 1)
        self.assertTrue(self.tracker.awaiting_next_round)

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
