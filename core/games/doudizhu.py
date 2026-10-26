# -*- coding: utf-8 -*-
"""斗地主牌局跟踪器。

实现斗地主特有的状态转换逻辑：
- 自动布局开局：完整 17/20 张手牌稳定，底牌可缺失
- 开始记牌条件：玩家手牌稳定
- 出牌处理：上家/下家出牌扣减剩余牌数，本家出牌仅记录不扣减
"""

from __future__ import annotations

from collections import Counter
import time

import config.settings as settings
from config.settings import STARTED_RECORD_CARD, WAIT_BEGIN
from core.base_tracker import BaseCardTracker
from core.image_saver import ImageSaver


class DoudizhuTracker(BaseCardTracker):
    """斗地主牌局状态跟踪器。

    手动模式状态转换条件（自动模式以布局快照直接初始化）：
        - WAIT_BEGIN → HAS_STARTED：地主底牌连续帧稳定
        - HAS_STARTED → STARTED_RECORD_CARD：玩家手牌连续帧稳定，同时扣减手牌

    出牌处理：
        - 上家/下家出牌：扣减剩余牌数
        - 本家出牌：仅记录显示，不扣减（已在开始记牌时扣减）

    Attributes:
        awaiting_next_round: 自动模式已结束并保留上一局显示，直到新手牌初始化。
        round_end_reason: 最近的自动结束原因。
        _initial_hand: 已确认的本家完整初始手牌，可在发牌阶段增补至二十张。
        _empty_frames: 连续成功识别且全图无牌的帧数，未知帧中断累计。
    """

    def __init__(self, layout_name: str | None = None, debug_manager: ImageSaver | None = None) -> None:
        """初始化斗地主跟踪器。

        Args:
            layout_name: 布局名称。
            debug_manager: 调试图片管理器实例（由 GameController 传入）。
        """
        super().__init__(layout_name, debug_manager)
        self.awaiting_next_round: bool = False
        self.round_end_reason: str = ""
        self._initial_hand: list[str] = []
        self._empty_frames: int = 0

    def reset(self) -> None:
        """显式重置时清空牌局、等待标记及显示；自动换局使用 begin_waiting。"""
        super().reset()
        self.awaiting_next_round = False
        self.round_end_reason = ""
        self._initial_hand = []
        self._empty_frames = 0
        self.no_target_time = time.time()

    def begin_waiting(self, reason: str) -> None:
        """结束内部跟踪但保留上一局牌数和历史，等待新开局快照。

        Args:
            reason: 连续无牌或确认出完的原因。
        """
        self.awaiting_next_round = True
        self.round_end_reason = reason
        self.state = WAIT_BEGIN
        for cache in self.frame_caches.values():
            cache.clear()
        self._empty_frames = 0

    def observe_frame_presence(self, present: bool | None) -> None:
        """仅凭成功检测的整轮结果累计无牌帧，截图/推理缺失会中断连续性。

        Args:
            present: True 表示检测到牌，False 表示有效结果无牌，None 表示画面未知。
        """
        if self.state != STARTED_RECORD_CARD or present is not False:
            self._empty_frames = 0
            return
        self._empty_frames += 1
        if self._empty_frames >= max(3, settings.FRAME_LENGTH):
            self.begin_waiting("连续多帧未检测到扑克牌，本局结束")

    @property
    def remaining_by_player(self) -> dict[str, int | None]:
        """返回有确切依据的各家剩余张数；未知地主身份或记录矛盾时返回 None。

        本家二十张时两家均为十七张；本家十七张时保留两种地主分配，
        某家累计出牌超过十七张可排除其中一种。不能将不确定的 [0, 3] 当作零。
        """
        own = len(self._initial_hand)
        played = {key: sum(map(len, self.show_cards[key])) for key in ("left", "self", "right")}
        self_played = Counter(card for play in self.show_cards["self"] for card in play)
        if self_played - Counter(self._initial_hand) or any(count < 0 for count in self.remain_cards.values()):
            return dict.fromkeys(("left", "self", "right"))
        allocations = [(17, 17)] if own == 20 else ([(17, 20), (20, 17)] if own == 17 else [])
        valid = [(left, right) for left, right in allocations
                 if played["left"] <= left and played["right"] <= right and played["self"] <= own]
        if not valid:
            return dict.fromkeys(("left", "self", "right"))
        result: dict[str, int | None] = {"self": own - played["self"]}
        for key, index in (("left", 0), ("right", 1)):
            values = {allocation[index] - played[key] for allocation in valid}
            result[key] = next(iter(values)) if len(values) == 1 else None
        return result

    def _get_validity_region(self) -> str:
        """斗地主以地主底牌作为帧有效性判断依据。"""
        return "landlord_cards"

    def initialize_from_layout(self, frame_data: dict[str, list[str]]) -> None:
        """使用自动布局已连续确认的开局快照初始化，只扣一次本家手牌。

        Args:
            frame_data: 无出牌、含 17/20 张手牌的真实稳定快照，底牌可缺失或不完整。

        Raises:
            ValueError: 牌局已初始化、手牌张数错误、存在出牌或牌面数量不合法。
        """
        hand = frame_data.get("player_hand", [])
        if (self.state == STARTED_RECORD_CARD or len(hand) not in (17, 20)
                or any(frame_data.get(zone["region"]) for zone in settings.PLAYED_ZONES)):
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

    def update_initial_hand(self, hand: list[str]) -> bool:
        """发牌阶段从十七张增补至二十张，只扣新增三张，不清除显示历史。

        Args:
            hand: 自动布局已连续确认、包含原十七张的二十张手牌。

        Returns:
            增补成功为 True；已有出牌、手牌不完整或牌面不合法时拒绝。
        """
        old, new = Counter(self._initial_hand), Counter(hand)
        if (self.state != STARTED_RECORD_CARD or len(self._initial_hand) != 17 or len(hand) != 20
                or any(self.show_cards.values()) or old - new
                or any(n > settings.TOTAL_CARDS.get(k, 0) for k, n in new.items())):
            return False
        self._delete_played_cards(list((new - old).elements()))
        self._initial_hand = list(hand)
        return True

    def get_cards_number(
        self, frame_data: dict[str, list[str]], yolo_ms: float,
        known_regions: set[str] | None = None,
    ) -> tuple[dict[str, int], dict[str, list[list[str]]], float]:
        """自动模式保留结束画面直到新开局；手动模式沿用原状态机。

        Args:
            frame_data: 当前已确认区域的牌点列表。
            yolo_ms: 本轮推理毫秒。
            known_regions: 自动模式的已知区域；None 表示旧手动模式。

        Returns:
            当前显示牌数、历史及推理毫秒。未知画面和普通超时不清空自动模式显示。
        """
        if known_regions is None:
            return super().get_cards_number(frame_data, yolo_ms)
        self._last_yolo_ms = yolo_ms
        if not self.awaiting_next_round:
            self.run_game(frame_data, yolo_ms, known_regions)
            if self.state == STARTED_RECORD_CARD:
                remaining = self.remaining_by_player
                for key, label in (("left", "上家"), ("self", "本家"), ("right", "下家")):
                    if remaining[key] == 0:
                        self.begin_waiting(f"{label}已确认出完，本局结束")
                        break
        return self.remain_cards, self.show_cards, self._last_yolo_ms

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
        self._initial_hand = list(self.frame_caches["player_hand"][-1])
        self._delete_played_cards(self._initial_hand)

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
