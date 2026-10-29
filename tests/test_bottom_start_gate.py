"""底牌固定两帧开局，第二帧才开始手牌稳定累计。"""

import unittest
from unittest.mock import patch

from config import settings
from core.games.doudizhu import DoudizhuTracker


class BottomStartGateTests(unittest.TestCase):
    """用确定帧序列验证底牌存在性与手牌设置的不同门槛。"""

    def setUp(self) -> None:
        """隔离确认设置，不读写用户配置。"""
        self.enterContext(patch.multiple(settings, FRAME_LENGTH=3, DEBUG_MODE=False))
        self.tracker = DoudizhuTracker()

    def feed(self, bottom: list[str] | None, hand: list[str]) -> None:
        """提交一张截图的识别结果，None底牌表示区域未知。

        Args:
            bottom: 当前底牌，None为未知、空列表为已知为空。
            hand: 当前本家手牌。
        """
        frame = {'player_hand': hand}
        if bottom is not None:
            frame['landlord_cards'] = bottom
        self.tracker.get_cards_number(frame, 1.)

    def test_second_bottom_frame_is_first_hand_frame(self) -> None:
        """第一帧不缓存，第二帧开始，第四帧才完成设置的三帧手牌确认。"""
        self.feed(['3'], ['A','K'])
        self.assertEqual(self.tracker.state, settings.WAIT_BEGIN)
        self.assertFalse(self.tracker.frame_caches['player_hand'])
        self.feed(['9','9','10'], ['A','K'])
        self.assertEqual(self.tracker.state, settings.HAS_STARTED)
        self.assertEqual(len(self.tracker.frame_caches['player_hand']), 1)
        self.feed(['2'], ['K','A'])
        self.assertEqual(self.tracker.state, settings.HAS_STARTED)
        self.feed(['J'], ['A','K'])
        self.assertEqual(self.tracker.state, settings.STARTED_RECORD_CARD)
        self.assertEqual(self.tracker.remain_cards['A'], 3)
        self.assertEqual(self.tracker.remain_cards['K'], 3)

    def test_bottom_gate_stays_two_when_hand_setting_changes(self) -> None:
        """手牌阈值为1或5，底牌仍只需且必须两帧。"""
        for required in (1,5):
            with self.subTest(required=required), patch.object(settings,'FRAME_LENGTH',required):
                self.tracker = DoudizhuTracker()
                self.feed(['3'], ['A'])
                self.assertEqual(self.tracker.state, settings.WAIT_BEGIN)
                self.feed(['K','Q'], ['A'])
                for _ in range(required-1):
                    self.assertEqual(self.tracker.state, settings.HAS_STARTED)
                    self.feed(['2'], ['A'])
                self.assertEqual(self.tracker.state, settings.STARTED_RECORD_CARD)

    def test_empty_unknown_and_pause_break_opening_sequence(self) -> None:
        """空、未知或暂停中断后必须重新连续两帧，不能跨中断累计。"""
        for missing in ([],None):
            with self.subTest(missing=missing):
                self.tracker.reset()
                self.feed(['A'],['K'])
                self.feed(missing,['K'])
                self.feed(['Q'],['K'])
                self.assertEqual(self.tracker.state,settings.WAIT_BEGIN)
                self.tracker.interrupt_observation()
                self.feed(['3'],['K'])
                self.assertEqual(self.tracker.state,settings.WAIT_BEGIN)
                self.feed(['4'],['K'])
                self.assertEqual(self.tracker.state,settings.HAS_STARTED)
                self.assertEqual(len(self.tracker.frame_caches['player_hand']),1)

    def test_old_round_display_survives_first_bottom_frame(self) -> None:
        """下一局底牌第一帧仍保留旧记录，第二帧才刷新。"""
        for _ in range(4):
            self.feed(['3'],['A'])
        self.tracker.begin_waiting('test')
        self.feed(['K'],['Q'])
        self.assertEqual(self.tracker.remain_cards['A'],3)
        self.assertTrue(self.tracker.awaiting_next_round)
        self.feed(['J'],['Q'])
        self.assertEqual(self.tracker.remain_cards['A'],4)
        self.assertFalse(self.tracker.awaiting_next_round)
        self.assertEqual(len(self.tracker.frame_caches['player_hand']),1)


if __name__ == '__main__':
    unittest.main()
