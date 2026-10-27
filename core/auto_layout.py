"""仅使用 YOLO 牌框进行斗地主布局校准、跨行分组和区域跟踪。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from statistics import median
from typing import Protocol, cast


REGIONS = ("player_hand", "landlord_cards", "opponent_left", "player_played", "opponent_right")
PLAY_REGIONS = REGIONS[2:]
Box = tuple[float, float, float, float]


class BoxTensor(Protocol):
    """检测坐标张量的最小读取接口。"""

    def cpu(self) -> BoxTensor:
        """返回 CPU 张量。"""
        ...

    def tolist(self) -> list[list[float]]:
        """返回坐标列表。"""
        ...


class ClassTensor(Protocol):
    """类别及置信度张量的最小读取接口。"""

    def cpu(self) -> ClassTensor:
        """返回 CPU 张量。"""
        ...

    def tolist(self) -> list[float]:
        """返回数值列表。"""
        ...


class YoloBoxes(Protocol):
    """YOLO 检测框读取接口。

    Attributes:
        xyxy: 原始像素坐标张量。
        cls: 类别编号张量。
    """

    xyxy: BoxTensor
    cls: ClassTensor


class YoloResult(Protocol):
    """YOLO 单帧结果读取接口。

    Attributes:
        boxes: 检测框，未检测到目标时可以为 None。
        names: 类别编号到 YOLO 标签的映射。
    """

    boxes: YoloBoxes | None
    names: Mapping[int, str]


@dataclass(frozen=True)
class CardBox:
    """归一化的单张牌检测。

    Attributes:
        name: YOLO 类别名。
        bbox: 相对于截图宽高的四个边界坐标。
    """

    name: str
    bbox: Box

    def __post_init__(self) -> None:
        """验证检测框。

        Raises:
            TypeError: 名称或坐标不是预期类型。
            ValueError: 坐标非有限值、越界或矩形为空。
        """
        if not isinstance(self.name, str) or len(self.bbox) != 4:
            raise TypeError("牌框必须包含类别和四个坐标")
        if any(type(v) not in (int, float) for v in self.bbox):
            raise TypeError("牌框坐标必须是数字")
        a, b, c, d = self.bbox
        if not all(isfinite(v) for v in self.bbox) or not (0 <= a < c <= 1 and 0 <= b < d <= 1):
            raise ValueError("牌框必须是 0~1 内的非空矩形")


@dataclass
class CardGroup:
    """可包含多行的完整牌组。

    Attributes:
        cards: 组内检测框；归属判断以整体范围而非单牌中心进行。
    """

    cards: list[CardBox]

    @property
    def bounds(self) -> Box:
        """返回整组牌的边界。"""
        return (min(c.bbox[0] for c in self.cards), min(c.bbox[1] for c in self.cards),
                max(c.bbox[2] for c in self.cards), max(c.bbox[3] for c in self.cards))

    @property
    def height(self) -> float:
        """返回单牌高度中位数，避免把双行整体高度当作单牌高度。"""
        return median(c.bbox[3] - c.bbox[1] for c in self.cards)

    @property
    def signature(self) -> tuple[str, ...]:
        """返回与换行、左右顺序无关的多重集签名。"""
        return tuple(sorted(c.name for c in self.cards))


@dataclass
class LayoutObservation:
    """一次布局观测，明确区分空区域与未知区域。

    Attributes:
        regions: 已知区域的检测；缺失键表示未知，空列表表示确定为空。
        message: 给用户的校准或归属状态。
        initial: 连续真实观测确认的开局快照，仅校准成功时发布一次。
        reset_required: 比例变化导致几何参照失效，不代表旧牌局已经结束。
        initial_hand_update: 发牌阶段稳定的 17→20 张增补，不代表新一局。
    """

    regions: dict[str, list[CardBox]] = field(default_factory=dict)
    message: str = "自动布局：等待完整的 17/20 张开局手牌"
    initial: dict[str, list[CardBox]] | None = None
    reset_required: bool = False
    initial_hand_update: list[CardBox] | None = None

    def as_boxes(self) -> dict[str, list[dict[str, object]]]:
        """转换成 Tracker 已使用的检测对象格式，保持稳定的牌面顺序。"""
        return {key: [{"name": c.name, "bbox": c.bbox} for c in sorted(cards, key=lambda c: c.name)]
                for key, cards in self.regions.items()}


def read_yolo_boxes(raw: object | None, size: tuple[int, int]) -> list[CardBox]:
    """读取原始 YOLO 结果，丢弃无效框，不依赖 torch 或 numpy。

    Args:
        raw: 单帧 YOLO 结果；None 表示未取得有效画面。
        size: 原始截图宽高。

    Returns:
        合法的归一化检测框。

    Examples:
        >>> read_yolo_boxes(None, (1000, 800))
        []
    """
    width, height = size
    if raw is None or width <= 0 or height <= 0:
        return []
    result = cast(YoloResult, raw)
    if result.boxes is None:
        return []
    coords = result.boxes.xyxy.cpu().tolist()
    classes = result.boxes.cls.cpu().tolist()
    cards: list[CardBox] = []
    for box, class_id in zip(coords, classes):
        if len(box) != 4 or not all(isfinite(v) for v in box):
            continue
        x1, y1, x2, y2 = box
        normalized = (max(0., x1 / width), max(0., y1 / height),
                      min(1., x2 / width), min(1., y2 / height))
        if normalized[0] < normalized[2] and normalized[1] < normalized[3]:
            cards.append(CardBox(result.names[int(class_id)], normalized))
    return cards


def group_rows(cards: Sequence[CardBox]) -> list[CardGroup]:
    """先按顶边组成行，再依据相邻空隙拆组；单张牌也形成有效组。

    Args:
        cards: 当前帧的检测框。

    Returns:
        从上到下、从左到右的行片段。

    Examples:
        >>> len(group_rows([CardBox("A", (.1, .2, .13, .25))]))
        1
    """
    rows: list[list[CardBox]] = []
    for card in sorted(cards, key=lambda c: (c.bbox[1], c.bbox[0])):
        h = card.bbox[3] - card.bbox[1]
        for row in rows:
            anchor = median(c.bbox[1] for c in row)
            row_h = median(c.bbox[3] - c.bbox[1] for c in row)
            if abs(card.bbox[1] - anchor) <= .45 * min(h, row_h):
                row.append(card)
                break
        else:
            rows.append([card])
    groups: list[CardGroup] = []
    for row in rows:
        ordered = sorted(row, key=lambda c: c.bbox[0])
        current = [ordered[0]]
        for card in ordered[1:]:
            previous = current[-1]
            scale = max(card.bbox[2] - card.bbox[0], previous.bbox[2] - previous.bbox[0])
            if card.bbox[0] - previous.bbox[2] > max(.015, 2.2 * scale):
                groups.append(CardGroup(current))
                current = []
            current.append(card)
        groups.append(CardGroup(current))
    return groups


def merge_rows(
    groups: Sequence[CardGroup], compatible: Callable[[CardGroup, CardGroup], bool] | None = None,
) -> list[CardGroup]:
    """合并相邻且横向对齐的行片段，允许长牌组的第二行只有一张。

    Args:
        groups: 相互独立的行片段；手牌、底牌应先从出牌候选中移除。
        compatible: 可选的区域冲突检查，阻止两个玩家的相邻牌组被合并。

    Returns:
        合并后的牌组。以相邻原始行建图，避免双行高度扩大后吞并远处牌区。

    Examples:
        >>> merge_rows([])
        []
    """
    parent = list(range(len(groups)))

    def root(index: int) -> int:
        """查找并压缩连通分量索引。

        Args:
            index: 分组索引。

        Returns:
            根索引。
        """
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i, first in enumerate(groups):
        a, top, b, _ = first.bounds
        for j in range(i + 1, len(groups)):
            second = groups[j]
            c, y, d, _ = second.bounds
            h = max(first.height, second.height)
            overlap = max(0., min(b, d) - max(a, c))
            aligned = overlap >= .45 * min(b - a, d - c)
            if .55 * min(first.height, second.height) <= abs(y - top) <= 1.65 * h and aligned:
                if compatible is None or compatible(first, second):
                    parent[root(j)] = root(i)
    merged: dict[int, list[CardBox]] = {}
    for i, group in enumerate(groups):
        merged.setdefault(root(i), []).extend(group.cards)
    return [CardGroup(cards) for cards in merged.values()]


class AutoLayoutAnalyzer:
    """针对本家在下、两家在左右的三人斗地主自动布局。

    Attributes:
        ready: 是否已从完整开局确认手牌参照，底牌可缺失。
        last_observation: 最近一次观测，供进程回传状态提示。
        _anchors: 已稳定确认的出牌锚点；左家用左边、右家用右边、本家用中心。
        _last_visible: 上一有效观测中的完整出牌组，防止动画跨区域换身份。
    """

    def __init__(self, card_limits: Mapping[str, int] | None = None) -> None:
        """创建尚未校准的布局，不读取任何手工牌区坐标。

        Args:
            card_limits: 可选的 YOLO 类别张数上限，用于排除非法开局检测。
        """
        self._limits = dict(card_limits) if card_limits is not None else None
        self.reset()

    def reset(self) -> None:
        """清除牌桌参照、候选和锚点，保留牌面合法性约束。"""
        self.ready = False
        self.last_observation = LayoutObservation()
        self._hand: CardGroup | None = None
        self._bottom: CardGroup | None = None
        self._anchors: dict[str, tuple[float, float]] = {}
        self._candidate: tuple[str, ...] | None = None
        self._candidate_bounds: Box | None = None
        self._stable = 0
        self._aspect: float | None = None
        self._play_candidates: dict[str, tuple[tuple[str, ...], int, Box]] = {}
        self._waiting_reason = ""
        self._plays_seen = False
        self._upgrade_candidate: tuple[str, ...] = ()
        self._upgrade_bounds: Box | None = None
        self._upgrade_frames = 0
        self._last_visible: dict[str, CardGroup] = {}
        self._previous_hand: tuple[str, ...] = ()
        self._self_evidence: tuple[str, ...] = ()
        self._evidence_ttl = 0

    def wait_for_next_round(self, reason: str) -> None:
        """在已确认全图连续无牌的局间边界清除几何，允许下局恰好同手牌。

        Args:
            reason: 本局结束原因，用于等待提示；旧牌数和历史由 Tracker 保留。
        """
        self.reset()
        self._waiting_reason = reason
        self.last_observation = LayoutObservation(message=f"自动布局：{reason}，保留上一局，等待新手牌")

    @property
    def _table_top(self) -> float:
        """由已学习的手牌顶边推导桌面上界先验，不依赖底牌可见性。"""
        assert self._hand is not None
        return self._hand.bounds[1] * .18

    def interrupt_observation(self) -> None:
        """打断未确认序列但保留已学习几何，暂停时间不能连接两段连续确认。"""
        self._stable = 0
        self._upgrade_frames = 0
        self._play_candidates.clear()

    def observe(self, cards: Sequence[CardBox], size: tuple[int, int], confirm_frames: int) -> LayoutObservation:
        """从一帧检测框更新布局，布局变更和不确定结果均显式返回。

        Args:
            cards: 已归一化的单张牌检测。
            size: 截图宽高；等比例缩放不触发重置。
            confirm_frames: 校准和出牌锚点的连续帧阈值，至少取三帧。

        Returns:
            已知区域、状态提示及必要的初始化/重置通知。

        Raises:
            TypeError: 帧阈值不是整数。
            ValueError: 帧阈值非正。
        """
        if type(confirm_frames) is not int:
            raise TypeError("确认帧数必须是整数")
        if confirm_frames <= 0:
            raise ValueError("确认帧数必须为正数")
        required = max(3, confirm_frames)
        w, h = size
        changed = False
        if w > 0 and h > 0:
            aspect = w / h
            if self._aspect is not None and abs(aspect / self._aspect - 1) > .05:
                changed = self.ready
                self.reset()
            if self._aspect is None:
                self._aspect = aspect
        if w <= 0 or h <= 0:
            self._stable = 0
            self._upgrade_frames = 0
            self._play_candidates.clear()
            observation = LayoutObservation(message="自动布局：未取得有效画面，保留当前记录")
        elif not cards:
            self._stable = 0
            self._upgrade_frames = 0
            self._play_candidates.clear()
            observation = LayoutObservation(message="自动布局：未检测到牌，等待有效画面", reset_required=changed)
        elif not self.ready:
            observation = self._calibrate(cards, required)
            observation.reset_required = changed
        else:
            observation = self._assign(cards, required)
        self.last_observation = observation
        return observation

    def _calibrate(self, cards: Sequence[CardBox], required: int) -> LayoutObservation:
        """只在无出牌的完整开局校准，避免把中途剩余手牌当成初始手牌。

        Args:
            cards: 当前检测。
            required: 连续真实观测阈值。

        Returns:
            等待状态或一次性开局快照。
        """
        groups = merge_rows(group_rows(cards))
        hands = [g for g in groups if len(g.cards) in (17, 20) and g.bounds[1] > .5
                 and g.bounds[2] - g.bounds[0] > .2]
        if len(hands) != 1:
            self._stable = 0
            return LayoutObservation(message="自动布局：请在开局前启动，等待完整的 17/20 张手牌")
        hand = hands[0]
        others = [g for g in groups if g is not hand]
        bottoms = [g for g in others if self._is_bottom_group(g, hand)]
        if len(bottoms) != len(others) or sum(len(g.cards) for g in bottoms) > 3:
            self._stable = 0
            return LayoutObservation(message="自动布局：发现其他牌组，等待尚未出牌的开局画面")
        bottom = CardGroup([c for g in bottoms for c in g.cards]) if bottoms else None
        if self._limits is not None and any(
            count > self._limits.get(name, 0)
            for name, count in Counter(hand.signature).items()
        ):
            self._stable = 0
            return LayoutObservation(message="自动布局：开局牌数不合法，等待识别稳定")
        signature, bounds = hand.signature, hand.bounds
        position_stable = self._candidate_bounds is not None and all(
            abs(a - b) < .025 for a, b in zip(self._candidate_bounds, bounds)
        )
        self._stable = self._stable + 1 if signature == self._candidate and position_stable else 1
        self._candidate, self._candidate_bounds = signature, bounds
        if self._stable < required:
            prefix = "保留上一局，" if self._waiting_reason else ""
            return LayoutObservation(message=f"自动布局：{prefix}正在确认开局 {self._stable}/{required}")
        self._hand, self._bottom, self.ready = hand, bottom, True
        self._previous_hand = hand.signature
        regions: dict[str, list[CardBox]] = {key: [] for key in REGIONS}
        regions["player_hand"] = hand.cards
        if bottom is not None:
            regions["landlord_cards"] = bottom.cards
        return LayoutObservation(regions, "自动布局：已定位，正在跟踪出牌", initial=regions)

    @staticmethod
    def _is_bottom_group(group: CardGroup, hand: CardGroup) -> bool:
        """筛选上方中央的可选底牌组，允许只识别一至三张。

        Args:
            group: 手牌以外的完整牌组。
            hand: 已找到的本家手牌，提供位置及牌框尺度。

        Returns:
            是否属于保守的底牌候选；这只是相对几何先验，不是游戏模板。
        """
        a, top, b, lower = group.bounds
        left, hand_top, right, _ = hand.bounds
        return (1 <= len(group.cards) <= 3 and top < min(.35, hand_top * .45)
                and lower < hand_top - 2 * hand.height and group.height <= 1.15 * hand.height
                and abs((a + b - left - right) / 2) <= max(.15, (right-left)*.3))

    def _reference(self, role: str) -> tuple[float, float]:
        """返回已学习锚点，或由本家手牌和窗口边界推导的初始位置。

        Args:
            role: 出牌区域名。

        Returns:
            横向锚点和顶边纵坐标。
        """
        if role in self._anchors:
            return self._anchors[role]
        assert self._hand is not None
        left, top, right, _ = self._hand.bounds
        bottom = self._table_top
        gap = top - bottom
        if role == "opponent_left":
            return left + .16 * (right - left), bottom + .38 * gap
        if role == "opponent_right":
            return right - .16 * (right - left), bottom + .38 * gap
        return (left + right) / 2, bottom + .70 * gap

    @staticmethod
    def _anchor(group: CardGroup, role: str) -> tuple[float, float]:
        """按玩家方向选择锚点，使牌组变长或换行不改变归属。

        Args:
            group: 完整牌组。
            role: 候选区域。

        Returns:
            左边/右边/中心及第一行顶边。
        """
        a, y, b, _ = group.bounds
        x = a if role == "opponent_left" else b if role == "opponent_right" else (a + b) / 2
        return x, y

    def _cost(self, group: CardGroup, role: str) -> float:
        """计算整组与区域参照的尺度化距离，不按单张牌跨越中线拆分。

        Args:
            group: 包含一行或多行的牌组。
            role: 候选区域。

        Returns:
            越小越可信的距离。
        """
        assert self._hand is not None
        x, y = self._anchor(group, role)
        rx, ry = self._reference(role)
        hand = self._hand.bounds
        sx = max(.09, (hand[2] - hand[0]) * .20)
        sy = max(.055, (hand[1] - self._table_top) * .20)
        return abs(x - rx) / sx + abs(y - ry) / sy

    def _assign(self, cards: Sequence[CardBox], required: int) -> LayoutObservation:
        """保留手牌参照，合并跨行出牌，歧义时不输出伪造空区域。

        Args:
            cards: 当前检测。
            required: 锚点学习所需连续帧。

        Returns:
            稳定归属或明确的未知状态。
        """
        assert self._hand is not None
        hand_top = self._hand.bounds[1]
        hand_cards = [c for c in cards if c.bbox[1] >= hand_top - .55 * self._hand.height]
        self._update_hand_evidence(hand_cards, required)
        top_groups = group_rows([c for c in cards if c not in hand_cards])
        bottom_cards = [c for g in top_groups if self._is_bottom_group(g, self._hand) for c in g.cards]
        if not hand_cards and len(cards) - len(bottom_cards) >= 8:
            self._play_candidates.clear()
            self._upgrade_frames = 0
            return LayoutObservation(message="自动布局：手牌参照丢失，暂停记录；请检查窗口或重新定位")
        regions: dict[str, list[CardBox]] = {"player_hand": hand_cards}
        if bottom_cards:
            regions["landlord_cards"] = bottom_cards
        remaining = [c for c in cards if c not in hand_cards and c not in bottom_cards]
        groups = merge_rows(group_rows(remaining), lambda first, second: self._compatible_rows(first, second, required))
        self._plays_seen = self._plays_seen or bool(groups)
        assignments: dict[str, CardGroup] = {}
        ambiguous = False
        for group in groups:
            costs = sorted((self._cost(group, role), role) for role in PLAY_REGIONS)
            best, role = costs[0]
            uncertain = costs[1][0] - best < .45
            self_cost = self._cost(group, "player_played")
            if uncertain and group.signature == self._self_evidence and self_cost <= best + .5:
                best, role, uncertain = self_cost, "player_played", False
            if best > 2.4 or uncertain or role in assignments or len(group.cards) > 20:
                ambiguous = True
                break
            assignments[role] = group
        for role, group in assignments.items():
            for previous_role, previous_group in self._last_visible.items():
                distance = max(abs(a - b) for a, b in zip(group.bounds, previous_group.bounds))
                if (role != previous_role and previous_role not in assignments
                        and group.signature == previous_group.signature
                        and distance < max(.12, 2 * group.height)):
                    ambiguous = True
        if ambiguous:
            self._play_candidates.clear()
            self._upgrade_frames = 0
            return LayoutObservation(regions, "自动布局：牌组归属不确定，暂停更新出牌记录")
        # 单张、长牌组、双行整组都经过同一个归属入口。
        for role in PLAY_REGIONS:
            assigned_group = assignments.get(role)
            regions[role] = [] if assigned_group is None else assigned_group.cards
            if assigned_group is None:
                self._play_candidates.pop(role, None)
                continue
            signature, count, previous_box = self._play_candidates.get(role, ((), 0, assigned_group.bounds))
            moved = max(abs(a - b) for a, b in zip(previous_box, assigned_group.bounds)) > .025
            count = count + 1 if signature == assigned_group.signature and not moved else 1
            self._play_candidates[role] = assigned_group.signature, count, assigned_group.bounds
            if signature == assigned_group.signature and moved:
                regions.pop(role)
            if count >= required:
                self._anchors[role] = self._anchor(assigned_group, role)
        if not hand_cards and not bottom_cards and not groups:
            return LayoutObservation(message="自动布局：等待有效画面")
        moving = any(role not in regions for role in PLAY_REGIONS)
        if not moving:
            self._last_visible = assignments
        upgrade = self._confirm_hand_upgrade(hand_cards, required)
        message = "自动布局：出牌移动中，等待位置稳定" if moving else "自动布局：已定位，正在跟踪出牌"
        return LayoutObservation(regions, message, initial_hand_update=upgrade)

    def _confirm_hand_upgrade(self, cards: Sequence[CardBox], required: int) -> list[CardBox] | None:
        """仅在尚未见出牌时确认 17 张完整手牌增至 20 张。

        Args:
            cards: 当前手牌区域的真实检测。
            required: 连续稳定帧数。

        Returns:
            已确认的二十张手牌；其他情况不发布增补。
        """
        assert self._hand is not None
        current = tuple(sorted(c.name for c in cards))
        legal = self._limits is None or all(n <= self._limits.get(k, 0) for k, n in Counter(current).items())
        if (self._plays_seen or len(self._hand.cards) != 17 or len(cards) != 20
                or Counter(self._hand.signature) - Counter(current) or not legal):
            self._upgrade_frames = 0
            return None
        group = CardGroup(list(cards))
        stable = self._upgrade_bounds is not None and max(
            abs(a-b) for a, b in zip(group.bounds, self._upgrade_bounds)) < .025
        self._upgrade_frames = self._upgrade_frames + 1 if current == self._upgrade_candidate and stable else 1
        self._upgrade_candidate, self._upgrade_bounds = current, group.bounds
        if self._upgrade_frames < required:
            return None
        self._hand = group
        return list(cards)

    def _update_hand_evidence(self, hand: Sequence[CardBox], required: int) -> None:
        """短暂保留手牌减少的牌面，辅助判断紧接着出现的本家出牌。

        Args:
            hand: 当前手牌观测。
            required: 证据保留所依据的连续帧阈值。
        """
        self._evidence_ttl = max(0, self._evidence_ttl - 1)
        if self._evidence_ttl == 0:
            self._self_evidence = ()
        current = tuple(sorted(c.name for c in hand))
        old, new = Counter(self._previous_hand), Counter(current)
        removed = old - new
        if current and removed and not new - old:
            self._self_evidence = tuple(sorted(removed.elements()))
            self._evidence_ttl = required + 2
        self._previous_hand = current

    def _compatible_rows(self, first: CardGroup, second: CardGroup, required: int = 3) -> bool:
        """用整手换行证据修正位置先验，同时保护独立本家出牌。

        Args:
            first: 第一行片段。
            second: 第二行片段。
            required: 独立出牌历史的稳定帧阈值。

        Returns:
            可以作为同一手牌的两行时为 True。
        """
        first_scores = sorted((self._cost(first, role), role) for role in PLAY_REGIONS)
        second_scores = sorted((self._cost(second, role), role) for role in PLAY_REGIONS)
        # 本家手牌减少或此前单独稳定出现的整手牌，比单行位置评分更可靠。
        for group, other_scores in ((first, second_scores), (second, first_scores)):
            if (self._independent_self_play(group, required)
                    and other_scores[0][1] != "player_played" and other_scores[0][0] <= 2.4
                    and other_scores[1][0] - other_scores[0][0] >= .45):
                return False
        different = first_scores[0][1] != second_scores[0][1]
        confident = (first_scores[1][0] - first_scores[0][0] >= .45
                     and second_scores[1][0] - second_scores[0][0] >= .45)
        if not (different and confident):
            return True
        # 短末行的右边缘和中心与整手牌不同，不能以其独立评分否定换行。
        return self._aligned_continuation(first, second)

    def _independent_self_play(self, group: CardGroup, required: int) -> bool:
        """判断某一行是否有独立本家出牌证据，避免吞并相邻玩家。

        Args:
            group: 待合并的单行。
            required: 独立历史所需连续帧数。

        Returns:
            牌面匹配近期手牌减少，或与此前稳定的本家整手出牌相同时为 True。
        """
        if group.signature == self._self_evidence:
            return True
        previous = self._last_visible.get("player_played")
        candidate = self._play_candidates.get("player_played")
        return (previous is not None and candidate is not None and candidate[1] >= required
                and previous.signature == group.signature
                and max(abs(a - b) for a, b in zip(previous.bounds, group.bounds)) <= .025)

    @staticmethod
    def _aligned_continuation(first: CardGroup, second: CardGroup) -> bool:
        """检查短末行的对齐、牌框尺度和列间距，不使用玩家位置先验。

        Args:
            first: 已通过相邻行距离和横向重叠检查的行片段。
            second: 另一行片段。

        Returns:
            下行未超出上行、同侧对齐且牌列位置对应时为 True。
        """
        upper, lower = sorted((first, second), key=lambda group: group.bounds[1])
        if len(upper.cards) < 2 or len(lower.cards) > len(upper.cards):
            return False
        widths = [median(c.bbox[2] - c.bbox[0] for c in group.cards) for group in (upper, lower)]
        if min(widths) / max(widths) < .65 or min(upper.height, lower.height) / max(upper.height, lower.height) < .65:
            return False
        tolerance = .55 * min(widths)
        left, _, right, _ = upper.bounds
        lower_left, _, lower_right, _ = lower.bounds
        contained = lower_left >= left - tolerance and lower_right <= right + tolerance
        edge_aligned = min(abs(lower_left - left), abs(lower_right - right)) <= tolerance
        column_aligned = all(
            min(abs(card.bbox[0] - other.bbox[0]) for other in upper.cards) <= tolerance
            for card in lower.cards
        )
        return contained and edge_aligned and column_aligned
