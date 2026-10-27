"""斗地主 V3 三阶段流程：底牌即开局，非空手牌稳定后仅扣一次。"""

from __future__ import annotations

from collections import Counter
from time import monotonic
import time

import config.settings as settings
from config.settings import HAS_STARTED, STARTED_RECORD_CARD, WAIT_BEGIN
from core.base_tracker import BaseCardTracker
from core.image_saver import ImageSaver


class DoudizhuTracker(BaseCardTracker):
    """以底牌控制开局与结束，沿用 V3 的稳定手牌一次扣减。

    Attributes:
        awaiting_next_round: 底牌消失后保留旧显示，等待下一次底牌出现。
        round_end_reason: 最近一次底牌消失结束的原因。
        _initial_hand: 第一次稳定确认并扣除的非空手牌，不要求 17/20 张。
        _bottom_visible: 当前有效观测中底牌区是否至少有一张牌。
        _empty_frames: 连续已知底牌区无牌的截图数量。
        _empty_since: 当前无牌序列的单调时钟起点。
    """

    def __init__(self, layout_name: str | None = None, debug_manager: ImageSaver | None = None) -> None:
        """创建尚未开局的跟踪器。

        Args:
            layout_name: 当前布局名。
            debug_manager: 统一的可选调试图片管理器。
        """
        super().__init__(layout_name, debug_manager)
        self.awaiting_next_round = False
        self.round_end_reason = ""
        self._initial_hand: list[str] = []
        self._bottom_visible = False
        self._empty_frames = 0
        self._empty_since: float | None = None

    def reset(self) -> None:
        """显式重置时立即清空显示和所有确认状态。"""
        super().reset()
        self.awaiting_next_round = False
        self.round_end_reason = ""
        self._initial_hand = []
        self.interrupt_observation()
        self.no_target_time = time.time()

    def begin_waiting(self, reason: str) -> None:
        """内部结束并保留旧牌数、历史，等待新的底牌出现。

        Args:
            reason: 底牌区连续无牌的结束原因。
        """
        self.state = WAIT_BEGIN
        self.awaiting_next_round = True
        self.round_end_reason = reason
        self.interrupt_observation()

    def interrupt_observation(self) -> None:
        """暂停或未知帧打断确认序列；保留当前牌局状态和显示。"""
        self._bottom_visible = False
        self._empty_frames = 0
        self._empty_since = None
        for cache in self.frame_caches.values():
            cache.clear()

    def observe_bottom_presence(self, present: bool | None) -> None:
        """每张截图仅一次，以底牌区的存在性驱动门禁及结束计时。

        Args:
            present: 有任意牌为 True，确定无牌为 False，未知为 None。
        Raises:
            TypeError: present 不是 bool 或 None。
        """
        if present is not None and type(present) is not bool:
            raise TypeError("底牌存在性必须是 bool 或 None")
        if present is None:
            self.interrupt_observation()
            return
        self._bottom_visible = present
        if present or self.state == WAIT_BEGIN:
            self._empty_frames, self._empty_since = 0, None
            if not present:
                for cache in self.frame_caches.values():
                    cache.clear()
            return
        for cache in self.frame_caches.values():
            cache.clear()
        now = monotonic()
        if self._empty_since is None:
            self._empty_since = now
        self._empty_frames += 1
        if self._empty_frames >= max(3, settings.FRAME_LENGTH) and now-self._empty_since >= settings.RESET_TIME:
            self.begin_waiting(f"底牌区连续至少 {settings.RESET_TIME:g} 秒且多帧无牌，等待下一局")

    def get_cards_number(self, frame_data: dict[str, list[str]], yolo_ms: float,
                         known_regions: set[str] | None = None) -> tuple[dict[str, int], dict[str, list[list[str]]], float]:
        """底牌可见时推进 V3 状态机，不再等待特定手牌张数。

        Args:
            frame_data: 当前区域的业务牌点。
            yolo_ms: 本轮模型调用毫秒。
            known_regions: 明确已知区域；None 使用 frame_data 的键。
        Returns:
            显示牌数、历史和模型耗时。结束时保留，下一次底牌出现开局时刷新。
        """
        self._last_yolo_ms = yolo_ms
        known = set(frame_data) if known_regions is None else known_regions
        self.observe_bottom_presence(bool(frame_data.get("landlord_cards")) if "landlord_cards" in known else None)
        if self._bottom_visible:
            self.run_game(frame_data, yolo_ms, known)
        return self.remain_cards, self.show_cards, self._last_yolo_ms

    def _get_validity_region(self) -> str:
        """底牌区至少一张可见才可更新，不要求三张或同一牌面。"""
        return "landlord_cards"

    def should_start_game(self) -> bool:
        """当前帧底牌存在立即进入 HAS_STARTED，不等待完整手牌。"""
        return self._bottom_visible

    def should_start_recording(self) -> bool:
        """非空合法手牌按 FRAME_LENGTH 连续稳定后开始扣牌，无张数门槛。"""
        cache = self.frame_caches["player_hand"]
        return (bool(cache) and bool(cache[-1]) and self._check_card(cache)
                and all(n <= settings.TOTAL_CARDS.get(k, 0) for k, n in Counter(cache[-1]).items()))

    def on_game_started(self) -> None:
        """新底牌出现时刷新旧显示，保留本帧缓存供后续稳定手牌确认。"""
        self.remain_cards = settings.TOTAL_CARDS.copy()
        self.show_cards = {z["key"]: [] for z in settings.PLAYED_ZONES}
        self.has_found_empty = {z["key"]: False for z in settings.PLAYED_ZONES}
        self._initial_hand = []
        self.awaiting_next_round = False
        self.round_end_reason = ""
        if settings.SAVE_DEBUG_IMAGES and self.debug_manager is not None:
            self.debug_manager.start_new_game()

    def on_start_recording(self) -> None:
        """仅扣除首次稳定的非空手牌，之后手牌变化不再重扣或自动补扣。"""
        self._initial_hand = list(self.frame_caches["player_hand"][-1])
        self._delete_played_cards(self._initial_hand)

    def initialize_from_layout(self, frame_data: dict[str, list[str]]) -> None:
        """兼容已稳定的布局快照入口；正式流水线按三阶段逐帧确认。

        Args:
            frame_data: 至少一张底牌及非空合法手牌的已稳定快照。
        Raises:
            ValueError: 已经记牌、底牌/手牌为空，或手牌牌面数量非法。
        """
        hand = frame_data.get("player_hand", [])
        if (self.state == STARTED_RECORD_CARD or not frame_data.get("landlord_cards") or not hand
                or any(n > settings.TOTAL_CARDS.get(k, 0) for k, n in Counter(hand).items())):
            raise ValueError("初始化需要底牌存在及非空合法手牌")
        self.reset()
        for key in self.frame_caches:
            self.frame_caches[key] = [list(frame_data.get(key, [])) for _ in range(settings.FRAME_LENGTH)]
        self._bottom_visible = True
        self.on_game_started()
        self.state = HAS_STARTED
        self.on_start_recording()
        self.state = STARTED_RECORD_CARD

    def process_played_cards(self, zone_key: str, cards: list[str]) -> None:
        """对手出牌扣剩余，本家出牌只记录。

        Args:
            zone_key: left、self 或 right。
            cards: 稳定确认的牌点列表。
        """
        if zone_key != "self":
            self._delete_played_cards(cards)
