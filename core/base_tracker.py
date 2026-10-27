# -*- coding: utf-8 -*-
"""牌局状态跟踪基类模块。

通过连续帧确认机制驱动三阶段状态机（等待开始 → 已开始 → 记牌中），
提供通用的记牌器骨架，子类实现游戏特定的状态转换条件和出牌处理逻辑。

截图和 YOLO 检测由 GameController 编排，Tracker 只负责状态机逻辑。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import cast
from abc import ABC, abstractmethod

import config.settings as settings
from config.settings import HAS_STARTED, STARTED_RECORD_CARD, WAIT_BEGIN
from core.image_saver import ImageSaver


class BaseCardTracker(ABC):
    """牌局状态跟踪器基类，驱动状态机并维护剩余牌数。

    截图和检测由外部（GameController）编排，Tracker 通过
    ``process_frame(frame_data, yolo_ms)`` 接收每帧检测结果。

    状态机::

        WAIT_BEGIN → (should_start_game) → HAS_STARTED → (should_start_recording) → STARTED_RECORD_CARD
                                                                                         ↓
                                                                              (超时无目标) → WAIT_BEGIN

    子类必须实现以下抽象方法：
        - ``should_start_game()``: 判断是否满足开始游戏的条件
        - ``should_start_recording()``: 判断是否满足开始记牌的条件
        - ``on_game_started()``: 游戏开始时的回调
        - ``on_start_recording()``: 开始记牌时的回调
        - ``process_played_cards()``: 处理确认的出牌

    Attributes:
        layout_name: 当前布局名称。
        state: 当前状态（WAIT_BEGIN / HAS_STARTED / STARTED_RECORD_CARD）。
        frame_caches: 各区域帧缓存字典，key 为区域名，value 为帧列表。
        show_cards: 各出牌区域已确认的出牌记录字典。
        has_found_empty: 各出牌区域是否曾检测到空帧。
        remain_cards: 各牌点剩余数量字典。
        no_target_time: 上次检测到有效目标的时间戳。
        debug_manager: 调试图片保存器（由 GameController 传入，可选）。
        _last_yolo_ms: 最近一次 YOLO 推理耗时（毫秒）。
    """

    def __init__(self, layout_name: str | None = None, debug_manager: ImageSaver | None = None) -> None:
        """初始化牌局跟踪器。

        Args:
            layout_name: 布局名称。为 None 时自动使用第一个可用配置。
            debug_manager: 调试图片保存器实例（由 GameController 统一管理并传入，可选）。
        """
        self.layout_name: str | None = layout_name
        self.state: int = WAIT_BEGIN
        self.remain_cards: dict[str, int] = settings.TOTAL_CARDS.copy()
        self.no_target_time: float = time.time()
        self.debug_manager: ImageSaver | None = debug_manager
        self._last_yolo_ms: float = 0.0

        # 从游戏配置初始化帧缓存和出牌记录
        self.frame_caches: dict[str, list[list[str]]] = {
            r["key"]: [] for r in settings.LAYOUT_REGIONS
        }
        self.show_cards: dict[str, list[list[str]]] = {
            z["key"]: [] for z in settings.PLAYED_ZONES
        }
        self.has_found_empty: dict[str, bool] = {
            z["key"]: False for z in settings.PLAYED_ZONES
        }

    def translate_boxes_to_cards(
        self, region_boxes: Mapping[str, Sequence[Mapping[str, object]]],
    ) -> dict[str, list[str]]:
        """将物理坐标框里的 YOLO 标签转换为游戏业务里的牌点名称。

        Args:
            region_boxes: 经过空间排序和分区后的对象字典，key 为区域名。

        Returns:
            dict[str, list[str]]: 转换后的各个区域扑克点数列表。
        """
        frame_data: dict[str, list[str]] = {}
        for region_key, dets in region_boxes.items():
            frame_data[region_key] = [
                settings.YOLO_TO_CARD_MAPPING[cast(str, d["name"])] for d in dets
            ]
        return frame_data

    def reset(self) -> None:
        """重置记牌器到初始状态，清空所有帧缓存和出牌记录。"""
        self.state = WAIT_BEGIN
        self.frame_caches = {r["key"]: [] for r in settings.LAYOUT_REGIONS}
        self.show_cards = {z["key"]: [] for z in settings.PLAYED_ZONES}
        self.has_found_empty = {z["key"]: False for z in settings.PLAYED_ZONES}
        self.remain_cards = settings.TOTAL_CARDS.copy()

    def _presses_one_frame(
        self, frame_data: dict[str, list[str]], yolo_ms: float,
        known_regions: set[str] | None = None,
    ) -> bool:
        """处理一帧检测结果，更新帧缓存。

        旧兼容调用以 ``_get_validity_region()`` 的区域非空作为门禁。
        当前斗地主两种布局均仅收集 known_regions 中的观测，未知区域清确认缓存；
        至少有一个已知区域非空时刷新 no_target_time 并推进状态。
        缓存始终裁剪到最新 FRAME_LENGTH 帧。

        Args:
            frame_data: 各区域的牌点列表，key 为区域名。
            yolo_ms: YOLO 推理耗时（毫秒）。
            known_regions: 明确已知区域；None 仅保留旧基类门禁，当前正式链路显式提供。

        Returns:
            本帧是否可用于状态更新。未知区域清缓存，不伪造空帧。
        """
        self._last_yolo_ms = yolo_ms

        valid_key = self._get_validity_region()
        if known_regions is None:
            if len(frame_data.get(valid_key, [])) == 0:
                return False
        else:
            for key in self.frame_caches:
                if key not in known_regions:
                    self.frame_caches[key].clear()
            if not any(frame_data.get(key) for key in known_regions):
                return False

        self.no_target_time = time.time()

        if settings.DEBUG_MODE:
            print("------------------------------------------")
            for key, val in frame_data.items():
                print(f"{key}: {val}")

        for key in self.frame_caches:
            if known_regions is not None and key not in known_regions:
                continue
            cache = self.frame_caches[key]
            cache.append(frame_data.get(key, []))
            # 阈值调小时一次移除全部超额旧帧，而非只移除一帧。
            del cache[:-settings.FRAME_LENGTH]
        return True

    @abstractmethod
    def _get_validity_region(self) -> str:
        """返回用于判断本帧是否有效的区域键名。

        该区域为空时本帧直接丢弃。例如斗地主使用 ``"landlord_cards"``。

        Returns:
            str: 区域键名。
        """
        ...

    def _check_card(self, lst: list[list[str]]) -> bool:
        """检查连续帧缓存是否稳定（长度足够且所有帧内容一致）。

        Args:
            lst: 帧缓存列表，每个元素为一帧的检测结果。

        Returns:
            bool: 缓存长度达到 FRAME_LENGTH 且所有帧完全一致时返回 True。
        """
        if len(lst) < settings.FRAME_LENGTH:
            return False

        for i in range(1, len(lst)):
            if lst[i - 1] != lst[i]:
                return False

        return True

    def _delete_played_cards(self, lst: list[str]) -> None:
        """从剩余牌数中扣减已出的牌。

        Args:
            lst: 本帧确认出的牌点列表。
        """
        for s in lst:
            self.remain_cards[s] -= 1

    @abstractmethod
    def should_start_game(self) -> bool:
        """判断是否满足开始游戏的条件。

        Returns:
            bool: 满足条件返回 True。
        """
        ...

    @abstractmethod
    def should_start_recording(self) -> bool:
        """判断是否满足开始记牌的条件。

        Returns:
            bool: 满足条件返回 True。
        """
        ...

    @abstractmethod
    def on_game_started(self) -> None:
        """游戏开始时的回调（如启动调试图片记录）。"""
        ...

    @abstractmethod
    def on_start_recording(self) -> None:
        """开始记牌时的回调（如扣减初始手牌）。"""
        ...

    @abstractmethod
    def process_played_cards(self, zone_key: str, cards: list[str]) -> None:
        """处理确认的出牌（扣减/记录）。

        Args:
            zone_key: 出牌区域键名（如 ``"left"``、``"self"``、``"right"``）。
            cards: 本帧确认出的牌点列表。
        """
        ...

    def _process_all_played_zones(self) -> None:
        """处理所有出牌区域的确认出牌。"""
        for zone in settings.PLAYED_ZONES:
            key = zone["key"]
            region_key = zone["region"]
            cache = self.frame_caches.get(region_key, [])
            show = self.show_cards[key]

            if not self._check_card(cache):
                continue

            current = cache[-1]
            if (len(show) == 0
                    or current != show[-1]
                    or self.has_found_empty[key]):
                if len(current) > 0:
                    show.append(current)
                    self.has_found_empty[key] = False
                else:
                    self.has_found_empty[key] = True
                self.process_played_cards(key, current)

    def run_game(
        self, frame_data: dict[str, list[str]], yolo_ms: float,
        known_regions: set[str] | None = None,
    ) -> None:
        """驱动一帧的状态机转换和出牌记录。

        依次执行：接收一帧数据 → 按当前状态检查条件 → 状态转换与扣牌。
        三个状态的处理互不排斥（使用 if 而非 elif），允许同一帧内连续推进状态。

        Args:
            frame_data: 各区域的牌点列表，key 为区域名。
            yolo_ms: YOLO 推理耗时（毫秒）。
            known_regions: 自动布局已知区域，未知区域不更新也不确认出牌。
        """
        if not self._presses_one_frame(frame_data, yolo_ms, known_regions):
            return

        if self.state == WAIT_BEGIN:
            if self.should_start_game():
                self.on_game_started()
                self.state = HAS_STARTED

        if self.state == HAS_STARTED:
            if self.should_start_recording():
                self.on_start_recording()
                self.state = STARTED_RECORD_CARD

        if self.state == STARTED_RECORD_CARD:
            self._process_all_played_zones()

    def get_cards_number(
        self, frame_data: dict[str, list[str]], yolo_ms: float,
        known_regions: set[str] | None = None,
    ) -> tuple[dict[str, int], dict[str, list[list[str]]], float]:
        """接收一帧检测结果并返回当前牌局数据。

        超过 RESET_TIME 未检测到有效目标时自动重置。

        Args:
            frame_data: 各区域的牌点列表，key 为区域名。
            yolo_ms: YOLO 推理耗时（毫秒）。
            known_regions: 自动布局已确认的区域；None 使用原手动布局规则。

        Returns:
            tuple: 包含三个元素：
                - remain_cards (dict[str, int]): 各牌点剩余数量。
                - show_cards (dict[str, list[list[str]]]): 各出牌区域的出牌记录。
                - _last_yolo_ms (float): 最近一次 YOLO 推理耗时（毫秒）。
        """
        self.run_game(frame_data, yolo_ms, known_regions)
        tme = time.time()
        if tme - self.no_target_time > settings.RESET_TIME:
            self.reset()
            self.no_target_time = tme
        return self.remain_cards, self.show_cards, self._last_yolo_ms
