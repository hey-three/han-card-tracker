# -*- coding: utf-8 -*-
"""斗地主牌局跟踪器。

实现斗地主特有的状态转换逻辑：
- 开始游戏条件：地主底牌稳定
- 开始记牌条件：玩家手牌稳定
- 出牌处理：上家/下家出牌扣减剩余牌数，本家出牌仅记录不扣减
"""

from __future__ import annotations

from collections import Counter
import time

import config.settings as settings
from config.settings import STARTED_RECORD_CARD
from core.base_tracker import BaseCardTracker


class DoudizhuTracker(BaseCardTracker):
    """斗地主牌局状态跟踪器。

    状态转换条件：
        - WAIT_BEGIN → HAS_STARTED：地主底牌连续帧稳定
        - HAS_STARTED → STARTED_RECORD_CARD：玩家手牌连续帧稳定，同时扣减手牌

    出牌处理：
        - 上家/下家出牌：扣减剩余牌数
        - 本家出牌：仅记录显示，不扣减（已在开始记牌时扣减）
    """

    def __init__(self, layout_name: str | None = None, debug_manager=None) -> None:
        """初始化斗地主跟踪器。

        Args:
            layout_name: 布局名称。
            debug_manager: 调试图片管理器实例（由 GameController 传入）。
        """
        super().__init__(layout_name, debug_manager)

    def _get_validity_region(self) -> str:
        """斗地主以地主底牌作为帧有效性判断依据。"""
        return "landlord_cards"

    def initialize_from_layout(self, frame_data: dict[str, list[str]]) -> None:
        """使用自动布局已连续确认的开局快照初始化，只扣一次本家手牌。

        Args:
            frame_data: 无出牌、含 17/20 张手牌及三张底牌的真实稳定快照。

        Raises:
            ValueError: 牌局已初始化、手牌/底牌张数错误或牌面数量不合法。
        """
        hand = frame_data.get("player_hand", [])
        bottom = frame_data.get("landlord_cards", [])
        if self.state == STARTED_RECORD_CARD or len(hand) not in (17, 20) or len(bottom) != 3:
            raise ValueError("自动布局需要完整且尚未记牌的开局快照")
        if any(count > settings.TOTAL_CARDS.get(card, 0) for card, count in Counter(hand).items()):
            raise ValueError("初始手牌包含非法牌面或超出牌组数量")
        self.reset()
        for key in self.frame_caches:
            self.frame_caches[key] = [list(frame_data.get(key, []))]
        self.on_game_started()
        self.on_start_recording()
        self.state = STARTED_RECORD_CARD
        self.no_target_time = time.time()

    def should_start_game(self) -> bool:
        """地主底牌连续帧稳定时开始游戏。"""
        return self._check_card(self.frame_caches["landlord_cards"])

    def should_start_recording(self) -> bool:
        """玩家手牌连续帧稳定时开始记牌。"""
        return self._check_card(self.frame_caches["player_hand"])

    def on_game_started(self) -> None:
        """游戏开始时启动调试图片记录。"""
        if settings.SAVE_DEBUG_IMAGES and self.debug_manager is not None:
            self.debug_manager.start_new_game()

    def on_start_recording(self) -> None:
        """开始记牌时扣减玩家手牌。"""
        self._delete_played_cards(self.frame_caches["player_hand"][-1])

    def process_played_cards(self, zone_key: str, cards: list[str]) -> None:
        """处理确认的出牌。

        斗地主规则：
        - 上家/下家出牌扣减剩余牌数
        - 本家出牌仅记录不扣减（已在 on_start_recording 中扣减）

        Args:
            zone_key: 出牌区域键名（``"left"`` / ``"self"`` / ``"right"``）。
            cards: 本帧确认出的牌点列表。
        """
        if zone_key != "self":
            self._delete_played_cards(cards)
