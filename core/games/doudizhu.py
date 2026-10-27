# -*- coding: utf-8 -*-
"""斗地主牌局跟踪器。

实现斗地主特有的状态转换逻辑：
- 两种布局开局：完整 17/20 张手牌稳定，底牌可缺失
- 开始记牌条件：玩家手牌稳定
- 出牌处理：上家/下家出牌扣减剩余牌数，本家出牌仅记录不扣减
"""

from __future__ import annotations

from collections import Counter
from time import monotonic
import time

import config.settings as settings
from config.settings import STARTED_RECORD_CARD, WAIT_BEGIN
from core.base_tracker import BaseCardTracker
from core.image_saver import ImageSaver


class DoudizhuTracker(BaseCardTracker):
    """斗地主牌局状态跟踪器。

    两种布局都以完整、合法、尚无出牌的 17/20 张稳定手牌开局。
    结束只依据连续有效全图无牌，同时满足帧数和持续时间；保留旧显示等待新局。

    出牌处理：
        - 上家/下家出牌：扣减剩余牌数
        - 本家出牌：仅记录显示，不扣减（已在开始记牌时扣减）

    Attributes:
        awaiting_next_round: 已结束并保留上一局显示，直到新手牌初始化。
        round_end_reason: 最近的自动结束原因。
        _initial_hand: 已确认的本家完整初始手牌，可在发牌阶段增补至二十张。
        _empty_frames: 连续成功识别且全图无牌的帧数，未知帧中断累计。
        _empty_since: 无牌序列首帧的单调时钟，None 表示未在累计。
        _hand_candidate: 手动开局/增补的规范化候选多重集。
        _hand_frames: 当前候选已连续出现的帧数。
        _play_observed: 本局是否曾观测出牌，防止中局改写初始手牌。
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
        self._empty_since: float | None = None
        self._hand_candidate: tuple[str, ...] = ()
        self._hand_frames = 0
        self._play_observed = False

    def reset(self) -> None:
        """显式重置时清空牌局、等待标记及显示；自动换局使用 begin_waiting。"""
        super().reset()
        self.awaiting_next_round = False
        self.round_end_reason = ""
        self._initial_hand = []
        self._empty_frames = 0
        self._empty_since = None
        self._hand_candidate = ()
        self._hand_frames = 0
        self._play_observed = False
        self.no_target_time = time.time()

    def begin_waiting(self, reason: str) -> None:
        """结束内部跟踪但保留上一局牌数和历史，等待新开局快照。

        Args:
            reason: 连续有效全图无牌的结束原因。
        """
        self.awaiting_next_round = True
        self.round_end_reason = reason
        self.state = WAIT_BEGIN
        for cache in self.frame_caches.values():
            cache.clear()
        self._empty_frames = 0
        self._empty_since = None
        self._hand_candidate = ()
        self._hand_frames = 0

    def interrupt_observation(self) -> None:
        """中断无牌及手牌候选连续性，供暂停恢复、设置对话框和未知帧使用。"""
        self._empty_frames = 0
        self._empty_since = None
        self._hand_candidate = ()
        self._hand_frames = 0
        for cache in self.frame_caches.values():
            cache.clear()

    def observe_frame_presence(self, present: bool | None) -> None:
        """仅凭成功检测的整轮结果累计无牌帧，截图/推理缺失会中断连续性。

        Args:
            present: True 表示检测到牌，False 表示有效结果无牌，None 表示画面未知。
        Raises:
            TypeError: present 不是 bool 或 None。
        """
        if present is not None and type(present) is not bool:
            raise TypeError("画面存在性必须是 bool 或 None")
        if present is None:
            self.interrupt_observation()
            return
        if self.state != STARTED_RECORD_CARD or present is not False:
            self._empty_frames = 0
            self._empty_since = None
            return
        now = monotonic()
        if self._empty_since is None:
            self._empty_since = now
        self._empty_frames += 1
        if (self._empty_frames >= max(3, settings.FRAME_LENGTH)
                and now-self._empty_since >= settings.RESET_TIME):
            self.begin_waiting(f"全图连续至少 {settings.RESET_TIME:g} 秒且多帧未检测到扑克牌，等待下一局")

    def _get_validity_region(self) -> str:
        """返回手牌兼容门禁；正式链路显式传入已知区域，不依赖底牌。"""
        return "player_hand"

    def initialize_from_layout(self, frame_data: dict[str, list[str]]) -> None:
        """使用任一布局已连续确认的开局快照初始化，只扣一次本家手牌。

        Args:
            frame_data: 无出牌、含 17/20 张手牌的真实稳定快照，底牌可缺失或不完整。

        Raises:
            ValueError: 牌局已初始化、手牌张数错误、存在出牌或牌面数量不合法。
        """
        hand = frame_data.get("player_hand", [])
        if (self.state == STARTED_RECORD_CARD or len(hand) not in (17, 20)
                or any(frame_data.get(zone["region"]) for zone in settings.PLAYED_ZONES)):
            raise ValueError("需要完整且尚未记牌的开局快照")
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
                or self._play_observed or any(self.show_cards.values()) or old - new
                or any(n > settings.TOTAL_CARDS.get(k, 0) for k, n in new.items())):
            return False
        self._delete_played_cards(list((new - old).elements()))
        self._initial_hand = list(hand)
        return True

    def get_cards_number(
        self, frame_data: dict[str, list[str]], yolo_ms: float,
        known_regions: set[str] | None = None,
    ) -> tuple[dict[str, int], dict[str, list[list[str]]], float]:
        """统一两种布局的稳定开局及出牌处理，普通超时和零张均不触发清屏。

        Args:
            frame_data: 当前已确认区域的牌点列表。
            yolo_ms: 本轮推理毫秒。
            known_regions: 已知区域；None 使用 frame_data 的键，不把缺失键当空区。

        Returns:
            当前显示牌数、历史及推理毫秒。结束时保留显示，直到新手牌确认。
        """
        self._last_yolo_ms = yolo_ms
        known_regions = set(frame_data) if known_regions is None else known_regions
        if self.state == STARTED_RECORD_CARD:
            self._play_observed |= any(frame_data.get(z["region"]) for z in settings.PLAYED_ZONES
                                       if z["region"] in known_regions)
        self._observe_opening_hand(frame_data, known_regions)
        if self.state == STARTED_RECORD_CARD:
            self.run_game(frame_data, yolo_ms, known_regions)
        return self.remain_cards, self.show_cards, self._last_yolo_ms

    def _observe_opening_hand(self, data: dict[str, list[str]], known: set[str]) -> None:
        """在完整已知区域中确认手动开局，或发牌阶段的十七增至二十。

        Args:
            data: 当前帧业务牌面。
            known: 真实已知的区域键；未知或漏牌会打断候选连续性。
        """
        hand = data.get("player_hand", [])
        required = {"player_hand", *(z["region"] for z in settings.PLAYED_ZONES)}
        opening = self.state != STARTED_RECORD_CARD
        eligible = (required <= known and not any(data.get(z["region"]) for z in settings.PLAYED_ZONES)
                    and len(hand) in (17, 20)
                    and all(n <= settings.TOTAL_CARDS.get(k, 0) for k, n in Counter(hand).items()))
        if not opening:
            eligible &= (not self._play_observed and len(self._initial_hand) == 17 and len(hand) == 20
                         and not (Counter(self._initial_hand) - Counter(hand)))
        if not eligible:
            self._hand_candidate, self._hand_frames = (), 0
            return
        signature = tuple(sorted(hand))
        self._hand_frames = self._hand_frames+1 if signature == self._hand_candidate else 1
        self._hand_candidate = signature
        if self._hand_frames >= max(3, settings.FRAME_LENGTH):
            if opening:
                self.initialize_from_layout(data)
            else:
                self.update_initial_hand(hand)
                self._hand_candidate, self._hand_frames = (), 0

    def should_start_game(self) -> bool:
        """为基类直接调用保留完整手牌门禁；正常开局由连续候选初始化。"""
        cache = self.frame_caches["player_hand"]
        return (len(cache) >= max(3, settings.FRAME_LENGTH) and len(cache[-1]) in (17, 20)
                and self._check_card(cache)
                and all(n <= settings.TOTAL_CARDS.get(k, 0) for k, n in Counter(cache[-1]).items())
                and not any(self.frame_caches[z["region"]] and self.frame_caches[z["region"]][-1]
                            for z in settings.PLAYED_ZONES))

    def should_start_recording(self) -> bool:
        """完整合法手牌稳定时开始记牌，不要求底牌可见。"""
        return self.should_start_game()

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
