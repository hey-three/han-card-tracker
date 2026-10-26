"""使用用户截图的真实 YOLO 牌框验证首次跨行出牌，不依赖模型加载。"""

from collections import Counter
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from config import settings
from core.auto_layout import AutoLayoutAnalyzer, CardBox, LayoutObservation, group_rows
from core.games.doudizhu import DoudizhuTracker


class WrappedOwnershipTests(unittest.TestCase):
    """复现初始位置先验把下家末行判为本家，以及相邻玩家误合并的边界。"""

    def setUp(self) -> None:
        """读取真实检测框，以补全的模拟开局建立同一牌桌参照。"""
        self.enterContext(patch.multiple(settings, FRAME_LENGTH=3, SAVE_DEBUG_IMAGES=False, DEBUG_MODE=False))
        data = json.loads((Path(__file__).parent / "fixtures/right_wrapped_play.json").read_text(encoding="utf-8"))
        self.size = tuple(data["size"])
        cards = [CardBox(item["name"], tuple(item["bbox"])) for item in data["cards"]]
        self.base = [c for c in cards if c.bbox[1] > .6 or c.bbox[1] < .2]
        self.plays = [c for c in cards if .2 < c.bbox[1] < .6]
        # 原图已出过牌，只有十五张手牌；额外两张仅模拟开局，不冒充真实历史。
        self.opening = self.base + [CardBox("K", (.3, .732, .33, .8)), CardBox("ten", (.34, .732, .37, .8))]
        self.analyzer = AutoLayoutAnalyzer()
        self.tracker = DoudizhuTracker()
        for _ in range(3):
            initial = self.analyzer.observe(self.opening, self.size, 3)
        self.assertIsNotNone(initial.initial)
        self.tracker.initialize_from_layout(self.tracker.translate_boxes_to_cards(initial.as_boxes()))

    def feed(self, cards: list[CardBox], count: int = 3) -> LayoutObservation:
        """将观测连续交给分析器和真实记牌器。

        Args:
            cards: 含手牌、底牌和出牌的检测框。
            count: 连续输入帧数。

        Returns:
            最后一帧区域观测。
        """
        observation = LayoutObservation()
        for _ in range(count):
            observation = self.analyzer.observe(cards, self.size, 3)
            self.tracker.get_cards_number(self.tracker.translate_boxes_to_cards(observation.as_boxes()), 1., set(observation.regions))
        return observation

    def test_first_right_play_keeps_both_rows_and_deducts_once(self) -> None:
        """没有任何出牌锚点时，真实截图的八加二张必须全部归下家。"""
        self.assertEqual(self.analyzer._anchors, {})
        observation = self.feed(self.base + self.plays, 6)
        self.assertEqual(Counter(c.name for c in observation.regions["opponent_right"]), Counter(c.name for c in self.plays))
        self.assertEqual(observation.regions["player_played"], [])
        self.assertEqual(self.tracker.show_cards["self"], [])
        self.assertEqual(len(self.tracker.show_cards["right"]), 1)
        self.assertEqual(len(self.tracker.show_cards["right"][0]), 10)
        self.assertEqual(sum(self.tracker.remain_cards.values()), 27)
        self.assertEqual(self.tracker.remain_cards["4"], 1)
        self.assertNotIn("player_played", self.analyzer._anchors)

    def test_first_right_play_with_single_card_second_row(self) -> None:
        """第一手出牌末行只有一张时，也不能被拆给本家。"""
        rows = group_rows(self.plays)
        plays = rows[0].cards + rows[1].cards[:1]
        observation = self.feed(self.base + plays)
        self.assertEqual(len(observation.regions["opponent_right"]), 9)
        self.assertEqual(observation.regions["player_played"], [])

    def test_mirrored_first_left_play_keeps_both_rows(self) -> None:
        """将同一实测几何镜像后，上家首次两行出牌也保持完整。"""
        plays = [CardBox(c.name, (1 - c.bbox[2], c.bbox[1], 1 - c.bbox[0], c.bbox[3])) for c in self.plays]
        observation = self.feed(self.base + plays)
        self.assertEqual(len(observation.regions["opponent_left"]), 10)
        self.assertEqual(observation.regions["player_played"], [])

    def test_learned_opponent_anchor_keeps_same_ownership(self) -> None:
        """学过下家的位置后，结果仍应与第一手出牌一致。"""
        last = max(group_rows(self.plays)[0].cards, key=lambda c: c.bbox[2])
        self.feed(self.base + [last])
        self.feed(self.base)
        observation = self.feed(self.base + self.plays)
        self.assertEqual(len(observation.regions["opponent_right"]), 10)
        self.assertEqual(observation.regions["player_played"], [])

    def test_existing_self_play_is_not_swallowed_by_new_opponent_row(self) -> None:
        """原本独立且稳定的本家对子，不能因下家新牌紧贴其上方而被合并。"""
        top, lower = group_rows(self.plays)
        self.feed(self.base + lower.cards)
        observation = self.feed(self.base + self.plays)
        self.assertEqual(len(observation.regions["opponent_right"]), len(top.cards))
        self.assertEqual(len(observation.regions["player_played"]), len(lower.cards))

    def test_own_hand_loss_preserves_independent_self_row(self) -> None:
        """本家手牌确实减少相同牌面时，不以换行形状覆盖本家出牌证据。"""
        top, lower = group_rows(self.plays)
        own_four = next(c for c in self.opening if c.name == "four")
        cards = [c for c in self.opening if c is not own_four] + top.cards + lower.cards[:1]
        observation = self.feed(cards)
        self.assertEqual(len(observation.regions["opponent_right"]), len(top.cards))
        self.assertEqual([c.name for c in observation.regions["player_played"]], ["four"])

    def test_full_group_does_not_change_owner_when_detection_order_changes(self) -> None:
        """打乱检测顺序和轻微坐标抖动不改变归属或重复扣牌。"""
        self.feed(self.base + self.plays)
        before = self.tracker.remain_cards.copy()
        shifted = [CardBox(c.name, (c.bbox[0] + .001, c.bbox[1] + .001, c.bbox[2] + .001, c.bbox[3] + .001)) for c in self.plays]
        self.feed(list(reversed(self.base + shifted)), 6)
        self.assertEqual(self.tracker.remain_cards, before)
        self.assertEqual(len(self.tracker.show_cards["right"]), 1)

    def test_offset_self_row_without_wrap_alignment_stays_separate(self) -> None:
        """位置接近但没有同侧和列对齐的两家出牌不能强行合并。"""
        top, lower = group_rows(self.plays)
        shifted = [CardBox(c.name, (c.bbox[0] - .02, c.bbox[1], c.bbox[2] - .02, c.bbox[3])) for c in lower.cards]
        observation = self.feed(self.base + top.cards + shifted)
        self.assertEqual(len(observation.regions["opponent_right"]), 8)
        self.assertEqual(len(observation.regions["player_played"]), 2)

    def test_first_wrapped_play_with_longer_confirmation(self) -> None:
        """调大稳定帧阈值时仍整组归属，确认前不记录、确认后只记录一次。"""
        with patch.object(settings, "FRAME_LENGTH", 6):
            for frame in range(8):
                observation = self.analyzer.observe(self.base + self.plays, self.size, 6)
                self.tracker.get_cards_number(self.tracker.translate_boxes_to_cards(observation.as_boxes()), 1., set(observation.regions))
                if frame < 5:
                    self.assertEqual(self.tracker.show_cards["right"], [])
            self.assertEqual(len(self.tracker.show_cards["right"]), 1)
            self.assertEqual(len(self.tracker.show_cards["right"][0]), 10)
            self.assertEqual(self.tracker.show_cards["self"], [])


if __name__ == "__main__":
    unittest.main()
