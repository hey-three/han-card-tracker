"""手动区域的固定方形拼图：完整覆盖、重叠切口、统一缩放和二维装箱。

规划只依赖窗口尺寸和用户矩形，固定布局可直接缓存；不调用视觉模型找区域。
不旋转图块，不按牌的数量删减裁片，也不把相交区域扩大成包围矩形。
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import product
from math import ceil, floor, isfinite, sqrt

from core.frame_packing import Box, PACKED_CANVAS_SIZE, PackingPlan, Rect, Tile


def _area(rect: Rect) -> int:
    """返回内部非空整数矩形的像素面积。

    Args:
        rect: 已校验的左、上、右、下坐标。
    Returns:
        像素面积。
    """
    return (rect[2] - rect[0]) * (rect[3] - rect[1])


def union_area(rects: Sequence[Rect]) -> int:
    """通过横向扫描计算覆盖并集，不将保护重叠计为新增信息。

    Args:
        rects: 已校验的非空整数矩形，允许相交；序列可为空。
    Returns:
        所有矩形并集的像素面积。
    """
    xs = sorted({x for r in rects for x in (r[0], r[2])})
    area = 0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((r[1], r[3]) for r in rects if r[0] < right and r[2] > left)
        bottom, covered = 0, 0
        for top, end in intervals:
            covered += max(0, end - max(bottom, top))
            bottom = max(bottom, end)
        area += (right - left) * covered
    return area


def _source_rects(size: tuple[int, int], regions: Sequence[Box]) -> list[Rect]:
    """向外取整并加少量源图保护边，避免切掉用户矩形边缘的牌面。

    Args:
        size: 原图正整数宽高。
        regions: 归一化区域。
    Returns:
        完整覆盖每个输入区域、限制在原图以内的源矩形。
    Raises:
        TypeError: 尺寸、区域或坐标类型错误。
        ValueError: 尺寸非正、区域为空或坐标不合法。
    """
    if not isinstance(size, tuple) or len(size) != 2 or any(type(v) is not int for v in size):
        raise TypeError("原图尺寸必须是整数二元组")
    if not isinstance(regions, Sequence) or isinstance(regions, (str, bytes)):
        raise TypeError("区域必须是矩形序列")
    if min(size) <= 0 or not regions:
        raise ValueError("原图尺寸必须为正，手动区域不能为空")
    if len(regions) > 5:
        raise ValueError("手动拼图最多接受五个业务区域")
    width, height = size
    pad = max(2, ceil(min(size) * .01))
    rects: list[Rect] = []
    for region in regions:
        if (not isinstance(region, (tuple, list)) or len(region) != 4
                or any(type(v) not in (int, float) for v in region)):
            raise TypeError("每个区域必须包含四个数字")
        a, b, c, d = region
        if not all(isfinite(v) for v in region) or not (0 <= a < c <= 1 and 0 <= b < d <= 1):
            raise ValueError("区域必须是 0~1 以内的非空矩形")
        # 旧手动框用于筛选中心点，目标下半部分可能伸出框外；向下多留上下文。
        # 仍完整保留原矩形，四周基础保护外，下沿最多补原图高的 3.5% / 区域高的 25%。
        below = max(pad, min(ceil(height*.035), ceil((d-b)*height*.25)))
        rects.append((max(0, floor(a*width)-pad), max(0, floor(b*height)-pad),
                      min(width, ceil(c*width)+pad), min(height, ceil(d*height)+below)))
    return rects


def _split_options(rect: Rect, margin: int) -> list[tuple[Rect, ...]]:
    """为长条生成一至六块的重叠切分候选，保留完整高度。

    Args:
        rect: 带外围保护的原图矩形。
        margin: 每条切口单侧的源像素保护宽度上限。
    Returns:
        不切分及不同块数的候选；相邻核心段无缝覆盖原区域。
    """
    a, b, c, d = rect
    width, height = c-a, d-b
    maximum = min(6, max(1, ceil(width / (2*height))))
    options: list[tuple[Rect, ...]] = []
    for count in range(1, maximum+1):
        guard = min(margin, max(1, width // (2*count)))
        options.append(tuple((max(a, a + width*i//count - (guard if i else 0)), b,
                              min(c, a + width*(i+1)//count + (guard if i+1 < count else 0)), d)
                             for i in range(count)))
    return options


def _split_free(free: Sequence[Rect], used: Rect) -> list[Rect]:
    """更新 MaxRects 空闲矩形，并去除被完全包含的冗余空间。

    Args:
        free: 尚未占用的候选空间，彼此可重叠。
        used: 本次占用的含间隔矩形。
    Returns:
        不与 used 相交的最大空闲矩形列表。
    """
    result: list[Rect] = []
    x1, y1, x2, y2 = used
    for a, b, c, d in free:
        if x2 <= a or c <= x1 or y2 <= b or d <= y1:
            result.append((a, b, c, d))
            continue
        if a < x1:
            result.append((a, b, x1, d))
        if x2 < c:
            result.append((x2, b, c, d))
        if b < y1:
            result.append((a, b, c, y1))
        if y2 < d:
            result.append((a, y2, c, d))
    unique = list(dict.fromkeys(result))
    return [r for i, r in enumerate(unique) if not any(
        j != i and s[0] <= r[0] and s[1] <= r[1] and s[2] >= r[2] and s[3] >= r[3]
        for j, s in enumerate(unique))]


def _place(pieces: Sequence[Rect], scale: float, side: int) -> list[Tile] | None:
    """用短边优先装箱，保持朝向及统一缩放，图块之间留四像素间隔。

    Args:
        pieces: 已按某种策略排序的源图块。
        scale: 所有图块共同使用的正比例；像素尺寸向上取整。
        side: 固定方形画布边长。
    Returns:
        完整放下的图块，或 None；不会返回放不下时的部分结果。
    """
    free: list[Rect] = [(4, 4, side, side)]
    tiles: list[Tile] = []
    for piece in pieces:
        width = max(1, ceil((piece[2]-piece[0]) * scale))
        height = max(1, ceil((piece[3]-piece[1]) * scale))
        padded_w, padded_h = width+4, height+4
        candidates = [(min(r[2]-r[0]-padded_w, r[3]-r[1]-padded_h),
                       max(r[2]-r[0]-padded_w, r[3]-r[1]-padded_h), r[1], r[0], r)
                      for r in free if r[2]-r[0] >= padded_w and r[3]-r[1] >= padded_h]
        if not candidates:
            return None
        rect = min(candidates)[-1]
        x, y = rect[:2]
        tiles.append(Tile(piece, x, y, (width, height)))
        free = _split_free(free, (x, y, x+padded_w, y+padded_h))
    return tiles


def make_square_plan(size: tuple[int, int], regions: Sequence[Box], guard: float = .04,
                     canvas_size: tuple[int, int] = PACKED_CANVAS_SIZE) -> PackingPlan:
    """搜索保护切分和二维排列，把所有手动区域完整放入固定方形画布。

    先以面积下界筛选最多 64 组切分，再尝试四种顺序和二分统一缩放。
    优先最大化同一源像素的显示比例；不通过重复复制图块伪造利用率。
    搜索是有界启发式，不宣称得到数学最优解；规划应按布局和尺寸缓存。

    Args:
        size: 原图正整数宽高。
        regions: 一至五个归一化矩形；运行时传入全部五个手动区域。
        guard: 切口单侧保护宽度占原图宽度的比例，必须在 (0, .25)。
        canvas_size: 正方形整数画布，默认 640×640，边长至少 32。
    Returns:
        所有区域均被覆盖、没有拉伸或旋转的固定画布规划。
    Raises:
        TypeError: 尺寸、保护比例或区域类型错误。
        ValueError: 尺寸、区域或保护比例不合法，或无法放入完整图块。
    Examples:
        >>> make_square_plan((1000, 800), [(0., .7, 1., .9)]).canvas_size
        (640, 640)
    """
    rects = _source_rects(size, regions)
    if type(guard) not in (int, float):
        raise TypeError("切口保护比例必须是数字")
    if not isfinite(guard) or not 0 < guard < .25:
        raise ValueError("切口保护比例必须在 0 与 .25 之间")
    if (not isinstance(canvas_size, tuple) or len(canvas_size) != 2
            or any(type(v) is not int for v in canvas_size)):
        raise TypeError("画布尺寸必须是整数二元组")
    if canvas_size[0] != canvas_size[1] or canvas_size[0] < 32:
        raise ValueError("画布必须是边长至少 32 的正方形")
    side = canvas_size[0]
    options = [_split_options(r, max(2, ceil(size[0]*guard))) for r in rects]
    total = sum(map(_area, rects))
    candidates: list[tuple[float, int, tuple[Rect, ...]]] = []
    for combination in product(*options):
        pieces = tuple(r for group in combination for r in group)
        area = sum(map(_area, pieces))
        # 限制新增保护副本的开销，避免用过多重叠挤占有效信息。
        if area <= total * 1.3:
            bound = max(sqrt(area), max(max(r[2]-r[0], r[3]-r[1]) for r in pieces))
            candidates.append((bound, area, pieces))
    selected = [entry[2] for entry in sorted(candidates)[:64]]
    if tuple(rects) not in selected:
        selected.append(tuple(rects))
    best: list[Tile] | None = None
    best_score = (0., 0, 0)
    for pieces in selected:
        orders = {
            tuple(sorted(pieces, key=lambda r: (-_area(r), r))),
            tuple(sorted(pieces, key=lambda r: (-max(r[2]-r[0], r[3]-r[1]), -_area(r), r))),
            tuple(sorted(pieces, key=lambda r: (-(r[2]-r[0]), -(r[3]-r[1]), r))),
            tuple(sorted(pieces, key=lambda r: (-(r[3]-r[1]), -(r[2]-r[0]), r))),
        }
        for order in sorted(orders):
            low = 0.
            high = min((side-8)/max(r[2]-r[0], r[3]-r[1]) for r in order)
            fitted: list[Tile] | None = None
            for _ in range(12):
                scale = (low+high)/2
                placed = _place(order, scale, side)
                if placed is None:
                    high = scale
                else:
                    low, fitted = scale, placed
            if fitted is not None:
                score = (low, -sum(map(_area, order)), -len(order))
                if score > best_score:
                    best, best_score = fitted, score
    if best is None:
        raise ValueError("固定画布无法容纳全部区域及保护间隔")
    return PackingPlan(size, best, canvas_size)


def plan_metrics(plan: PackingPlan) -> dict[str, float]:
    """计算真实覆盖、重复和画布填充指标，便于区分裁剪收益与输入降采样。

    Args:
        plan: 已完成的固定画布规划。
    Returns:
        原图覆盖比例、含保护的源面积比例、灰边比例及图块数等数值。
    """
    sources = [t.source for t in plan.tiles]
    full = plan.size[0]*plan.size[1]
    canvas = plan.canvas_size[0]*plan.canvas_size[1]
    rendered = sum(t.render_size[0]*t.render_size[1] for t in plan.tiles)
    return {"source_coverage": union_area(sources)/full,
            "source_with_overlap": sum(map(_area, sources))/full,
            "padding_fraction": 1-rendered/canvas, "tile_count": float(len(plan.tiles)),
            "canvas_width": float(plan.canvas_size[0]), "canvas_height": float(plan.canvas_size[1]),
            "source_scale": min(min(t.render_size[0]/(t.source[2]-t.source[0]),
                                    t.render_size[1]/(t.source[3]-t.source[1])) for t in plan.tiles)}
