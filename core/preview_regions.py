"""生成只供UI绘制的预览区域元数据，不改变模型输入像素。"""

from collections.abc import Mapping, Sequence
from typing import cast

from core.auto_layout import Box, CardBox
from core.frame_packing import PackingPlan, Rect
from core.square_packing import _source_rects


PACKED_PREVIEW_PREFIX = 'packed:'
SEARCH_PREVIEW_PREFIX = 'search:'


def preview_regions(
    layout: Mapping[str, Sequence[float]], *, plan: PackingPlan | None = None,
    observed: Mapping[str, Sequence[CardBox]] | None = None,
    search: Mapping[str, Box] | None = None,
    fixed: Mapping[str, Box] | None = None,
) -> dict[str, Box]:
    """返回本次实际输入坐标中的区域，区分整图边框和拼图文字标签。

    Args:
        layout: 已验证的手动布局；拼图传本阶段选区，原图传全部五区，自动模式为空。
        plan: 最后一次模型调用实际使用的拼图规划；整图或同帧回退必须为None。
        observed: 自动模式本帧已确认归属的牌组；空或未知区域不猜测边界。
        search: 自动校准前的宽泛搜索带，仅用于预览，非空时优先显示。
        fixed: 实际用于业务判断的区域，覆盖同名观测包围框，空区也持续显示。
    Returns:
        归一化矩形。普通键表示原图整体牌区，packed:序号:区域键表示只标名称的裁片。
        相同人工框具有多个归属时以加号组合区域键，避免任意选一个错误名称。
    Raises:
        TypeError: 拼图使用的人工选区坐标类型错误。
        ValueError: 拼图人工选区非法或不足一个完整像素。
    """
    if plan is None:
        if search:
            result = {SEARCH_PREVIEW_PREFIX+key: bounds for key, bounds in search.items()}
        elif observed is None:
            result = {key: cast(Box, tuple(float(value) for value in bounds)) for key, bounds in layout.items()}
        else:
            result = {
                key: (min(card.bbox[0] for card in cards), min(card.bbox[1] for card in cards),
                      max(card.bbox[2] for card in cards), max(card.bbox[3] for card in cards))
                for key, cards in observed.items() if cards
            }
        result.update(fixed or {})
        return result
    if not layout:
        return {}
    names = list(layout)
    rects = _source_rects(plan.size, [cast(Box, tuple(layout[key])) for key in names])
    by_rect: dict[Rect, list[str]] = {}
    for name, rect in zip(names, rects):
        by_rect.setdefault(rect, []).append(name)
    width, height = plan.canvas_size
    overlays: dict[str, Box] = {}
    for index, tile in enumerate(plan.tiles):
        if tile.source_region is not None:
            matches = by_rect.get(tile.source_region, [])
        else:
            a, b, c, d = tile.source
            matches = [name for rect, labels in by_rect.items()
                       if rect[0] <= a < c <= rect[2] and rect[1] <= b < d <= rect[3] for name in labels]
        if not matches:
            continue
        left, top, right, bottom = tile.bounds
        key = f'{PACKED_PREVIEW_PREFIX}{index}:{"+".join(matches)}'
        overlays[key] = (left/width, top/height, right/width, bottom/height)
    return overlays
