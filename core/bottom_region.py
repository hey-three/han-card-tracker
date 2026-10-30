"""自动底牌区域：居中70%宽度，独立学习顶部高度，不依赖手牌几何。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.auto_layout import Box, CardBox


class BottomRegion:
    """以两帧顶部检测学习高度，确认后本局固定，不因漏检或少牌收缩。

    Attributes:
        SEARCH_HEIGHT: 初搜中心点上限为截图高度的25%，避免中央翻牌参与学习。
    """

    SEARCH_HEIGHT = .25

    def __init__(self) -> None:
        """建立尚未学习高度的底牌区域。"""
        self.reset()

    def reset(self) -> None:
        """清除高度与待确认提议，供新局、布局或截图尺寸改变时重学。"""
        self._height: float | None = None
        self._proposal: float | None = None
        self._frames = 0

    def interrupt(self) -> None:
        """未知、空帧或暂停中断未确认提议，已学习的高度保持不变。"""
        self._proposal = None
        self._frames = 0

    @property
    def bounds(self) -> Box:
        """返回预览和业务判断共用的归一化矩形。"""
        return (.15, 0., .85, self.SEARCH_HEIGHT if self._height is None else self._height)

    @property
    def learned(self) -> bool:
        """返回高度是否已由连续两帧检测确认。"""
        return self._height is not None

    def observe(self, cards: Sequence[CardBox]) -> list[CardBox]:
        """筛选区域内的检测；未学习时用下沿加半个牌框高度提出区域高度。

        Args:
            cards: 本帧已有的归一化检测，不另做推理，不要求三张底牌齐全。
        Returns:
            中心落在当前区域内的牌；业务仍独立按既有两帧门禁确认开局。
            两次高度提议差不超过屏高3%才确认，取二者较大值并封顶25%。
            学习不阻塞当前底牌存在性；确认后不再按每帧框大小修改区域。
        """
        left, top, right, lower = self.bounds
        accepted = [card for card in cards
                    if left <= (card.bbox[0]+card.bbox[2])/2 <= right
                    and top <= (card.bbox[1]+card.bbox[3])/2 <= lower]
        if self.learned:
            return accepted
        if not accepted:
            self.interrupt()
            return []
        height = min(self.SEARCH_HEIGHT, max(card.bbox[3]+(card.bbox[3]-card.bbox[1])*.5
                                            for card in accepted))
        if self._proposal is not None and abs(height-self._proposal) <= .03:
            self._proposal = max(self._proposal, height)
            self._frames += 1
        else:
            self._proposal, self._frames = height, 1
        if self._frames >= 2:
            self._height = self._proposal
        return accepted
