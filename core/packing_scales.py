"""从当前布局的真实单牌框学习拼图相对尺度，不使用样例牌桌固定比例。"""

from collections import deque
from collections.abc import Sequence
from statistics import median

from config.packing_profile import INITIAL_REGION_SCALES
from core.auto_layout import Box
from core.frame_packing import Detection


class AdaptivePackingScales:
    """以每帧单牌中位高度、三帧确认及迟滞避免拼图频繁抖动。

    Attributes:
        heights: 已确认的各区域普通单牌归一化高度，不是区域高度。
        _samples: 各区最近三次有普通牌观测的帧级高度。
    """

    def __init__(self) -> None:
        """创建空测量；冷启动使用用户确认的固定初始比例。"""
        self.heights: dict[str, float] = {}
        self._samples: dict[str, deque[float]] = {}

    def interrupt(self) -> None:
        """失败或未知帧中断未确认样本，保留已经测准的单牌尺度。"""
        self._samples.clear()

    def weights(self, names: Sequence[str]) -> list[float]:
        """未测准底牌前保留固定比例，测准后优先对齐出牌、其次手牌。

        Args:
            names: 当前阶段参与的区域名。
        Returns:
            同顺序的正权重；无底牌或参照时保持固定值，不循环试探放大。
        """
        played = [v for k, v in self.heights.items() if k not in ('landlord_cards', 'player_hand')]
        reference = median(played) if played else self.heights.get('player_hand')
        if reference is None or 'landlord_cards' not in self.heights:
            return [INITIAL_REGION_SCALES.get(name, 1.) for name in names]
        weights = []
        for name in names:
            height = self.heights.get(name)
            factor = reference/height if height is not None else INITIAL_REGION_SCALES.get(name, 1.)
            if name == 'landlord_cards':
                factor *= 1.12
            weights.append(max(.25, min(8., factor)))
        return weights

    def observe(self, cards: Sequence[Detection], size: tuple[int, int],
                names: Sequence[str], regions: Sequence[Box]) -> None:
        """按原坐标中心分区，学习每张普通牌框高度并排除大小王纵长字样。

        Args:
            cards: 已完整还原、去重的当前帧真实检测框。
            size: 当前原图宽高。
            names: 当前参与的区域。
            regions: 与名称一一对应的归一化矩形。
        Raises:
            ValueError: 区域与名称数量不一致或图像尺寸非正。
        """
        if len(names) != len(regions) or min(size) <= 0:
            raise ValueError('名称与区域须一一对应，图像尺寸必须为正')
        values: dict[str, list[float]] = {name: [] for name in names}
        for card in cards:
            a, b, c, d = card.bbox
            cx, cy = (a+c)/(2*size[0]), (b+d)/(2*size[1])
            owner = next((name for name, (x1, y1, x2, y2) in zip(names, regions)
                          if x1 <= cx <= x2 and y1 <= cy <= y2), None)
            if owner is not None and 'joker' not in card.name.lower():
                values[owner].append((d-b)/size[1])
        for name, heights in values.items():
            if not heights:
                self._samples.pop(name, None)
                continue
            history = self._samples.setdefault(name, deque(maxlen=3))
            history.append(median(heights))
            if len(history) < 3 or max(history) > min(history)*1.2:
                continue
            measured = median(history)
            previous = self.heights.get(name)
            if previous is None or abs(measured/previous-1.) > .1:
                self.heights[name] = measured
