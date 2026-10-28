"""自动布局的单张、长牌组、跨行和不确定观测回归。"""

from collections.abc import Sequence
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from config import settings
from core.auto_layout import AutoLayoutAnalyzer, CardBox, LayoutObservation, REGIONS, read_yolo_boxes
from core.game_controller import GameController
from core.games.doudizhu import DoudizhuTracker


def row(names: Sequence[str], x: float, y: float, step: float = .04, height: float = .045) -> list[CardBox]:
    """生成相对于截图尺寸的确定检测框。

    Args:
        names: 每张牌的类别。
        x: 第一张牌左边。
        y: 行顶边。
        step: 横向间隔。
        height: 单框高度。

    Returns:
        从左到右的检测框。
    """
    return [CardBox(name, (x + i * step, y, x + i * step + .025, y + height))
            for i, name in enumerate(names)]


def opening() -> list[CardBox]:
    """生成合法的 17 张开局手牌和三张底牌。"""
    hand = ["three"] * 4 + ["four"] * 4 + ["five"] * 4 + ["six"] * 4 + ["seven"]
    return row(hand, .10, .76, .047) + row(["A", "K", "Q"], .43, .10, .035, .035)


class AutoLayoutTests(unittest.TestCase):
    """只使用模拟 YOLO 框，验证自动区域解析及真实 Tracker 扣牌。"""

    def setUp(self) -> None:
        """校准默认牌桌，禁用所有磁盘写入。"""
        changes = patch.multiple(settings, FRAME_LENGTH=3, DEBUG_MODE=False, PACKED_INFERENCE=False)
        changes.start()
        self.addCleanup(changes.stop)
        self.analyzer = AutoLayoutAnalyzer()
        self.cards = opening()
        self.tracker = DoudizhuTracker()
        for _ in range(3):
            observation = self.analyzer.observe(self.cards, (1000, 800), 3)
        self.assertIsNotNone(observation.initial)
        self.tracker.initialize_from_layout(self.tracker.translate_boxes_to_cards(observation.as_boxes()))

    def feed(self, cards: Sequence[CardBox], count: int = 3) -> LayoutObservation:
        """把连续观测交给真实 Tracker，未知键保持未知。

        Args:
            cards: 模拟检测框。
            count: 连续帧数。

        Returns:
            最后一次观测。
        """
        observation = LayoutObservation()
        for _ in range(count):
            observation = self.analyzer.observe(cards, (1000, 800), 3)
            data = self.tracker.translate_boxes_to_cards(observation.as_boxes())
            self.tracker.get_cards_number(data, 1., set(observation.regions))
        return observation

    def test_observation_uses_screen_reading_order_instead_of_label_names(self) -> None:
        """两行牌先读取上行，行内从左到右，不按标签字母或牌点重排。"""
        top = row(['eight', 'nine', 'ten'], .2, .3)
        bottom = row(['J', 'Q'], .2, .37)
        observation = LayoutObservation(regions={'opponent_left': list(reversed(top + bottom))})
        boxes = observation.as_boxes()['opponent_left']
        self.assertEqual([box['name'] for box in boxes], ['eight', 'nine', 'ten', 'J', 'Q'])

    def test_manual_reading_order_uses_left_edges_and_preserves_wrapped_rows(self) -> None:
        """手动分区中宽框右沿越过邻牌时仍依照左沿顺序读取。"""
        from core.layout_analyzer import LayoutAnalyzer
        analyzer = LayoutAnalyzer({'window_title': 'test', 'layout': {}})
        detections = [
            {'name': '10', 'bbox': (5, 42, 18, 62)},
            {'name': '9', 'bbox': (30, 10, 42, 30)},
            {'name': '8', 'bbox': (10, 10, 48, 30)},
        ]
        result = analyzer.sort_cards_by_topright_rowwise(detections)
        self.assertEqual([box['name'] for box in result], ['8', '9', '10'])

    def test_single_card_for_each_player(self) -> None:
        """本家、左家、右家单张出牌均归入独立区域。"""
        plays = row(["A"], .22, .37) + row(["K"], .47, .57) + row(["Q"], .76, .37)
        observation = self.feed(self.cards + plays)
        self.assertEqual([c.name for c in observation.regions["opponent_left"]], ["A"])
        self.assertEqual(self.tracker.show_cards, {"left": [["A"]], "self": [["K"]], "right": [["Q"]]})
        self.assertEqual(self.tracker.remain_cards["K"], 4)
        self.assertEqual(self.tracker.remain_cards["A"], 3)

    def test_long_left_play_crosses_center_without_splitting(self) -> None:
        """左家长牌组跨过中线后仍整组归左家。"""
        names = ["A", "A", "K", "K", "Q", "Q", "J", "J", "ten", "ten", "nine", "nine"]
        observation = self.feed(self.cards + row(names, .22, .37, .032))
        self.assertEqual(len(observation.regions["opponent_left"]), 12)
        self.assertEqual(observation.regions["opponent_right"], [])
        self.assertEqual(len(self.tracker.show_cards["left"][0]), 12)

    def test_long_right_and_self_plays_are_not_split(self) -> None:
        """右家跨中线的长牌组和本家长牌组也保持完整。"""
        names = ["A", "A", "K", "K", "Q", "Q", "J", "J", "ten", "ten", "nine", "nine"]
        for role, x, y in (("opponent_right", .38, .37), ("player_played", .30, .57)):
            with self.subTest(role=role):
                observation = self.analyzer.observe(self.cards + row(names, x, y, .032), (1000, 800), 3)
                self.assertEqual(len(observation.regions[role]), 12)

    def test_two_row_plays_for_all_players(self) -> None:
        """三家各自的两行出牌均合为一条完整出牌记录。"""
        cases = {
            "opponent_left": row(["A", "K", "Q", "J"], .22, .37) + row(["ten"], .22, .425),
            "opponent_right": row(["A", "K", "Q", "J"], .65, .37) + row(["ten"], .77, .425),
            "player_played": row(["A", "K", "Q", "J"], .425, .57) + row(["ten"], .485, .625),
        }
        for region, plays in cases.items():
            with self.subTest(region=region):
                observation = self.feed(self.cards + plays)
                self.assertEqual(len(observation.regions[region]), 5)
                self.assertEqual(sum(len(observation.regions[key]) for key in REGIONS[2:]), 5)
                key = {"opponent_left": "left", "opponent_right": "right", "player_played": "self"}[region]
                self.assertEqual(len(self.tracker.show_cards[key][0]), 5)

    def test_reflow_of_same_cards_does_not_deduct_again(self) -> None:
        """一行改成两行或检测顺序改变，不会被当作新出牌重复扣减。"""
        names = ["A", "K", "Q", "J", "ten", "nine"]
        self.feed(self.cards + row(names, .22, .37))
        before = self.tracker.remain_cards.copy()
        wrapped = row(names[:4], .22, .37) + row(names[4:], .22, .425)
        self.feed(list(reversed(self.cards + wrapped)), 5)
        self.assertEqual(self.tracker.remain_cards, before)
        self.assertEqual(len(self.tracker.show_cards["left"]), 1)

    def test_adjacent_opponent_second_row_does_not_swallow_self_play(self) -> None:
        """两行对手出牌的下沿靠近本家出牌时，仍保持玩家归属隔离。"""
        plays = (row(["A", "K", "Q", "J"], .22, .37)
                 + row(["ten"], .22, .425) + row(["nine"], .48, .49))
        observation = self.feed(self.cards + plays)
        self.assertEqual(len(observation.regions["opponent_left"]), 5)
        self.assertEqual(len(observation.regions["player_played"]), 1)

    def test_moving_play_does_not_pass_stability_gate(self) -> None:
        """同牌在动画中持续移动时不确认，停止后再记录一次。"""
        for y in (.37, .40, .43):
            self.feed(self.cards + row(["A"], .22, y), 1)
        self.assertEqual(self.tracker.show_cards["left"], [])
        self.feed(self.cards + row(["A"], .22, .43))
        self.assertEqual(self.tracker.show_cards["left"], [["A"]])

    def test_empty_keeps_anchor_and_allows_same_card_again(self) -> None:
        """牌组消失不删除锚点，确认空帧后可再次记录同牌。"""
        self.feed(self.cards + row(["A"], .22, .37))
        self.feed(self.cards)
        self.feed(self.cards + row(["A"], .22, .37))
        self.assertEqual(self.tracker.show_cards["left"], [["A"], ["A"]])
        self.assertEqual(self.tracker.remain_cards["A"], 2)

    def test_ambiguous_observation_is_not_an_empty_transition(self) -> None:
        """归属不明时清确认缓存，不制造重复出同牌所需的空帧。"""
        self.feed(self.cards + row(["A"], .22, .37))
        observation = self.feed(self.cards + row(["K"], .29, .515), 5)
        self.assertNotIn("opponent_left", observation.regions)
        self.assertIn("不确定", observation.message)
        self.feed(self.cards + row(["A"], .22, .37))
        self.assertEqual(self.tracker.show_cards["left"], [["A"]])

    def test_hand_loss_resolves_self_play_near_region_boundary(self) -> None:
        """手牌减少的牌面与新牌组一致时，可辅助确定本家出牌归属。"""
        observation = self.feed(self.cards[1:] + row(["three"], .29, .515))
        self.assertEqual([c.name for c in observation.regions["player_played"]], ["three"])
        self.assertEqual(self.tracker.show_cards["self"], [["3"]])
        self.assertEqual(self.tracker.remain_cards["3"], 0)

    def test_hidden_bottom_pauses_recording(self) -> None:
        """底牌区无牌时不再把中央出现的牌记作对手出牌。"""
        observation = self.feed(self.cards[:-3] + row(["A"], .22, .37))
        self.assertEqual(observation.regions["landlord_cards"], [])
        self.assertNotIn("opponent_left", observation.regions)
        self.assertEqual(self.tracker.show_cards["left"], [])

    def test_arbitrary_nonempty_hand_can_locate(self) -> None:
        """按 V3 要求，八张非空手牌也能定位，不再要求十七或二十张。"""
        analyzer = AutoLayoutAnalyzer()
        for _ in range(6):
            observation = analyzer.observe(self.cards[:8] + self.cards[-3:], (1000, 800), 3)
        self.assertTrue(analyzer.ready)
        self.assertEqual(len(observation.regions["player_hand"]), 8)

    def test_twenty_cards_and_two_row_hand_can_calibrate(self) -> None:
        """地主二十张手牌，以及初始手牌本身换行的情况可校准。"""
        names = ["three"] * 4 + ["four"] * 4 + ["five"] * 4 + ["six"] * 4 + ["seven"] * 4
        candidates = [row(names, .10, .76, .04),
                      row(names[:10], .26, .76, .047) + row(names[10:17], .33, .815, .047)]
        for hand in candidates:
            with self.subTest(count=len(hand)):
                analyzer = AutoLayoutAnalyzer()
                for _ in range(3):
                    observation = analyzer.observe(hand + self.cards[-3:], (1000, 800), 3)
                self.assertIsNotNone(observation.initial)
                self.assertEqual(len(observation.regions["player_hand"]), len(hand))

    def test_invalid_initial_card_counts_do_not_initialize(self) -> None:
        """重复识别出超出牌组上限的手牌时保持等待。"""
        analyzer = AutoLayoutAnalyzer({"A": 4, "K": 4, "Q": 4})
        for _ in range(5):
            observation = analyzer.observe(row(["A"] * 17, .1, .76, .047) + self.cards[-3:], (1000, 800), 3)
        self.assertFalse(analyzer.ready)
        self.assertIn("不合法", observation.message)

    def test_transient_bottom_misread_does_not_reset_game(self) -> None:
        """底牌单帧误识别不会清除已经记下的牌局。"""
        changed = self.cards[:-3] + row(["A", "K", "J"], .43, .10, .035, .035)
        observation = self.analyzer.observe(changed, (1000, 800), 3)
        self.assertFalse(observation.reset_required)
        observation = self.analyzer.observe(self.cards, (1000, 800), 3)
        self.assertFalse(observation.reset_required)
        self.assertTrue(self.analyzer.ready)

    def test_calibration_needs_consecutive_stable_positions(self) -> None:
        """同牌面仍在发牌移动时不能完成校准。"""
        analyzer = AutoLayoutAnalyzer()
        for _ in range(2):
            analyzer.observe(self.cards, (1000, 800), 3)
        analyzer.observe([], (1000, 800), 3)
        observation = analyzer.observe(self.cards, (1000, 800), 3)
        self.assertIsNone(observation.initial)
        moved = [CardBox(c.name, (c.bbox[0], c.bbox[1] + .03, c.bbox[2], c.bbox[3] + .03))
                 for c in self.cards]
        observation = analyzer.observe(moved, (1000, 800), 3)
        self.assertIsNone(observation.initial)

    def test_scale_preserves_calibration_but_aspect_change_resets(self) -> None:
        """归一化坐标允许等比例缩放，明显长宽比改变要求重新校准。"""
        observation = self.analyzer.observe(self.cards, (2000, 1600), 3)
        self.assertFalse(observation.reset_required)
        self.assertTrue(self.analyzer.ready)
        observation = self.analyzer.observe(self.cards, (1600, 900), 3)
        self.assertTrue(observation.reset_required)
        self.assertFalse(self.analyzer.ready)

    def test_no_detections_are_unknown_not_all_empty(self) -> None:
        """截图失败或整帧无牌时，不把所有出牌区域标记为空。"""
        self.assertEqual(self.analyzer.observe([], (0, 0), 3).regions, {})

    def test_invalid_boxes_are_rejected(self) -> None:
        """非有限值、越界和空框不能参与校准。"""
        for box in ((0., 0., 0., .1), (-.1, 0., .1, .1), (0., 0., float("nan"), .1)):
            with self.subTest(box=box), self.assertRaises(ValueError):
                CardBox("A", box)

    def test_yolo_reading_normalizes_coordinates(self) -> None:
        """真实接口形状的原始结果可以转换，零尺寸图片安全忽略。"""
        raw = Mock()
        raw.names = {0: "A"}
        raw.boxes.xyxy.cpu.return_value.tolist.return_value = [[100., 200., 150., 250.]]
        raw.boxes.cls.cpu.return_value.tolist.return_value = [0.]
        self.assertEqual(read_yolo_boxes(raw, (1000, 500)), [CardBox("A", (.1, .4, .15, .5))])
        self.assertEqual(read_yolo_boxes(raw, (0, 0)), [])

    def test_controller_pipeline_initializes_once_and_resets_layout(self) -> None:
        """自动模式经过真实 Controller/Tracker，启动和重置均不依赖手工区域。"""
        config = settings.validate_layout({"window_title": "Game", "mode": "auto", "layout": {}})
        with patch.object(settings, "WINDOW_LAYOUTS", {"Auto": config}), patch.object(
            GameController, "_create_inferencer", return_value=Mock(),
        ), patch("core.game_controller.ScreenCapture") as capture:
            capture.return_value.capture_window.return_value = Image.new("RGB", (1000, 800))
            controller = GameController("Auto")
        controller.yolo_inferencer.detect.return_value = (Mock(), 1.)
        with patch("core.game_controller.read_yolo_boxes", return_value=self.cards):
            for _ in range(8):
                remain, _, _ = controller.detect()
        self.assertEqual(sum(remain.values()), 37)
        self.assertIn("已定位", controller.layout_status)
        controller.reset()
        self.assertFalse(controller.layout_analyzer.ready)
        self.assertEqual(sum(controller.tracker.remain_cards.values()), 54)


if __name__ == "__main__":
    unittest.main()
