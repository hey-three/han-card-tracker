"""斗地主统计口径与运行时帧长度的回归测试。"""

import unittest
from unittest.mock import patch

from config import settings
from core.games.doudizhu import DoudizhuTracker


class TrackerTests(unittest.TestCase):
    """用确定的检测帧验证扣牌和连续帧确认。"""

    def setUp(self) -> None:
        """隔离运行配置，禁止测试保存截图。"""
        changes = patch.multiple(settings, FRAME_LENGTH=1, DEBUG_MODE=False)
        changes.start()
        self.addCleanup(changes.stop)
        self.tracker = DoudizhuTracker()
        self.frame = {region["key"]: [] for region in settings.LAYOUT_REGIONS}
        self.frame["landlord_cards"] = ["3", "4", "5"]
        self.frame["player_hand"] = ["A"] + ["3"]*4 + ["4"]*4 + ["5"]*4 + ["6"]*4

    def feed(self, count: int = 3) -> None:
        """输入指定数量的相同帧。

        Args:
            count: 输入次数。
        """
        for _ in range(count):
            self.tracker.get_cards_number(self.frame, 0.0)

    def test_reading_order_changes_do_not_recount_a_visible_play(self) -> None:
        """换行只改变显示顺序，不重复扣牌；空区过渡后允许再出相同牌组。"""
        self.feed(1)
        cards = ['8', '9', '10', 'J', 'Q']
        self.frame['opponent_left'] = cards
        self.feed(1)
        self.frame['opponent_left'] = list(reversed(cards))
        self.feed(1)
        self.assertEqual(self.tracker.show_cards['left'], [cards])
        self.assertTrue(all(self.tracker.remain_cards[card] == 3 for card in cards))
        self.frame['opponent_left'] = []
        self.feed(1)
        self.frame['opponent_left'] = cards
        self.feed(1)
        self.assertEqual(self.tracker.show_cards['left'], [cards, cards])
        self.assertTrue(all(self.tracker.remain_cards[card] == 2 for card in cards))

    def test_frame_confirmation_ignores_order_but_keeps_card_multiplicity(self) -> None:
        """连续确认不比较排列，但必须区分AAK与AKK的张数。"""
        settings.FRAME_LENGTH = 2
        self.assertTrue(self.tracker._check_card([['A', 'A', 'K'], ['K', 'A', 'A']]))
        self.assertFalse(self.tracker._check_card([['A', 'A', 'K'], ['K', 'K', 'A']]))

    def test_diagnostics_follow_order_independent_confirmation(self) -> None:
        """显示顺序变化后实际完成确认，诊断不能仍然报告该区域待确认。"""
        from core.diagnostic_state import decision_reasons, tracker_snapshot
        self.feed(1)
        settings.FRAME_LENGTH = 2
        self.frame['opponent_left'] = ['8', '9', '10', 'J', 'Q']
        self.feed(1)
        before = tracker_snapshot(self.tracker)
        self.frame['opponent_left'] = ['Q', 'J', '10', '9', '8']
        self.feed(1)
        after = tracker_snapshot(self.tracker)
        reasons = decision_reasons(before, after, self.frame, list(self.frame), True, 2)
        self.assertEqual(self.tracker.show_cards['left'], [self.frame['opponent_left']])
        self.assertFalse(any(reason['code'] == 'confirmation_pending'
                             and reason.get('region') == 'opponent_left' for reason in reasons))

    def test_initial_hand_and_self_play_are_counted_once(self) -> None:
        """持续可见的手牌和本家出牌不重复扣减。"""
        self.feed(5)
        self.assertEqual(self.tracker.remain_cards["A"], 3)
        self.frame["player_hand"] = []
        self.frame["player_played"] = ["A"]
        self.feed(5)
        self.assertEqual(self.tracker.remain_cards["A"], 3)
        self.assertEqual(self.tracker.show_cards["self"], [["A"]])

    def test_same_opponent_play_requires_empty_transition(self) -> None:
        """对手持续显示的牌只扣一次，空帧之后可再次出同牌。"""
        self.feed()
        self.frame["opponent_left"] = ["A"]
        self.feed(5)
        self.assertEqual(self.tracker.remain_cards["A"], 2)
        self.frame["opponent_left"] = []
        self.feed()
        self.frame["opponent_left"] = ["A"]
        self.feed()
        self.assertEqual(self.tracker.remain_cards["A"], 1)

    def test_lower_frame_length_takes_effect_on_next_frame(self) -> None:
        """从六帧改为一帧后，下一帧立即确认新出牌。"""
        settings.FRAME_LENGTH = 6
        self.feed(6)
        settings.FRAME_LENGTH = 1
        self.frame["opponent_left"] = ["2"]
        self.feed()
        self.assertEqual(self.tracker.show_cards["left"], [["2"]])
        self.assertEqual(len(self.tracker.frame_caches["opponent_left"]), 1)

    def test_raise_frame_length_waits_for_enough_frames(self) -> None:
        """提高确认阈值后必须收集足够的新帧。"""
        self.feed()
        settings.FRAME_LENGTH = 3
        self.frame["opponent_left"] = ["2"]
        self.feed(2)
        self.assertEqual(self.tracker.show_cards["left"], [])
        self.feed()
        self.assertEqual(self.tracker.show_cards["left"], [["2"]])


if __name__ == "__main__":
    unittest.main()
