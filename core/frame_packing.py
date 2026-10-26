"""独立于模型和 Qt 的重叠切分、画布规划及原图坐标恢复。"""

from __future__ import annotations

from collections.abc import Sequence
from math import ceil, floor, isfinite

from core.auto_layout import Box

Rect = tuple[int, int, int, int]
PACKED_CANVAS_SIZE = (640, 640)


class Detection:
    """保存像素坐标下的模型检测。

    Attributes:
        name: str，模型类别名。
        bbox: Box，像素边界 (left, top, right, bottom)。
        confidence: float，0~1 置信度。
    """

    def __init__(self, name: str, bbox: Box, confidence: float = 1.0) -> None:
        """校验检测。

        Args:
            name: 非空模型类别名。
            bbox: 四个有限数字组成的非空像素矩形。
            confidence: 0~1 置信度，默认 1。

        Raises:
            TypeError: 参数类型错误。
            ValueError: 坐标非有限、矩形为空或置信度越界。
        Examples:
            >>> Detection('A', (1., 2., 3., 4.)).name
            'A'
        """
        if not isinstance(name, str) or not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
            raise TypeError("检测必须包含类别和四个坐标")
        if any(type(v) not in (int, float) for v in (*bbox, confidence)):
            raise TypeError("检测坐标及置信度必须是数字")
        if not name or not all(isfinite(v) for v in (*bbox, confidence)):
            raise ValueError("检测必须有限且类别非空")
        if not (bbox[0] < bbox[2] and bbox[1] < bbox[3] and 0 <= confidence <= 1):
            raise ValueError("检测矩形或置信度无效")
        self.name = name
        self.bbox: Box = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        self.confidence = float(confidence)


class Tile:
    """记录原图裁片及其在统一缩放画布上的位置。

    Attributes:
        source: Rect，原图整数矩形。
        x: int，图块在画布的横向偏移。
        y: int，图块在画布的纵向偏移。
        render_size: tuple[int, int]，整数绘制尺寸，用于严格恢复坐标。
    """

    def __init__(self, source: Rect, x: int, y: int, render_size: tuple[int, int] | None = None) -> None:
        """保存规划器生成的坐标。

        Args:
            source: 非空、非负整数矩形。
            x: 画布非负横向偏移。
            y: 画布非负纵向偏移。
            render_size: 实际绘制宽高；None 保留原尺寸，固定画布使用统一比例缩放后的整数尺寸。

        Raises:
            TypeError: 坐标或绘制尺寸不是整数。
            ValueError: 矩形为空、偏移为负或绘制尺寸非正。
        """
        if not isinstance(source, tuple) or len(source) != 4:
            raise TypeError("图块矩形必须是四元组")
        if any(type(v) is not int for v in (*source, x, y)):
            raise TypeError("图块坐标必须是整数")
        if min(*source, x, y) < 0 or source[0] >= source[2] or source[1] >= source[3]:
            raise ValueError("图块坐标无效")
        if render_size is None:
            render_size = source[2]-source[0], source[3]-source[1]
        if (not isinstance(render_size, tuple) or len(render_size) != 2
                or any(type(v) is not int for v in render_size)):
            raise TypeError("绘制尺寸必须是整数二元组")
        if min(render_size) <= 0:
            raise ValueError("绘制尺寸必须为正")
        self.source, self.x, self.y = source, x, y
        self.render_size = render_size

    @property
    def bounds(self) -> Rect:
        """返回画布边界。

        Returns:
            缩放和偏移后的画布整数矩形。
        """
        return self.x, self.y, self.x + self.render_size[0], self.y + self.render_size[1]

    def project(self, box: Box) -> Box:
        """把原图检测框映射到当前裁片在画布上的位置，供训练标注和验证复用。

        Args:
            box: 已校验且位于 source 内的原图像素框。

        Returns:
            画布像素框；使用实际取整后的绘制尺寸，与恢复公式严格互逆。
        """
        a, b, c, d = self.source
        sx, sy = self.render_size[0]/(c-a), self.render_size[1]/(d-b)
        return ((box[0]-a)*sx+self.x, (box[1]-b)*sy+self.y,
                (box[2]-a)*sx+self.x, (box[3]-b)*sy+self.y)

    def unproject(self, box: Box) -> Box:
        """把当前裁片的画布框恢复为原图像素坐标。

        Args:
            box: 已确认落在该裁片内部的画布像素框。

        Returns:
            原图坐标；不会把缩放后的坐标直接当作原图坐标。
        """
        a, b, c, d = self.source
        sx, sy = (c-a)/self.render_size[0], (d-b)/self.render_size[1]
        return ((box[0]-self.x)*sx+a, (box[1]-self.y)*sy+b,
                (box[2]-self.x)*sx+a, (box[3]-self.y)*sy+b)


def overlap(a: Box, b: Box, *, smaller: bool = False) -> float:
    """计算 IoU 或交集/较小面积。

    Args:
        a: 已校验的非空像素矩形。
        b: 已校验的非空像素矩形。
        smaller: 为 True 时使用较小面积作分母，默认计算 IoU。

    Returns:
        0~1 的重叠比例；仅供已校验的内部检测使用。
    Examples:
        >>> overlap((0., 0., 2., 2.), (0., 0., 2., 2.))
        1.0
    """
    intersection = max(0., min(a[2], b[2]) - max(a[0], b[0])) * max(0., min(a[3], b[3]) - max(a[1], b[1]))
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (min(aa, bb) if smaller else aa + bb - intersection)


class PackingPlan:
    """保存单帧的切片和恢复关系。

    Attributes:
        size: tuple[int, int]，原图宽高。
        tiles: tuple[Tile, ...]，不可变图块序列。
        canvas_size: tuple[int, int]，画布宽高。
    """

    def __init__(self, size: tuple[int, int], tiles: Sequence[Tile], canvas_size: tuple[int, int] | None = None) -> None:
        """持有规划结果。

        Args:
            size: 原图正整数宽高。
            tiles: 非空图块序列，源矩形必须位于原图内部。
            canvas_size: 明确的固定画布宽高；None 使用图块边界包围框。

        Raises:
            TypeError: 原图/画布尺寸或图块类型错误。
            ValueError: 尺寸非正、图块为空、源裁片越界或目标图块超出固定画布。
        """
        if not isinstance(size, tuple) or len(size) != 2 or any(type(v) is not int for v in size):
            raise TypeError("原图尺寸必须是整数二元组")
        if min(size) <= 0 or not tiles:
            raise ValueError("拼图至少需要一个图块")
        if any(not isinstance(tile, Tile) for tile in tiles):
            raise TypeError("图块必须是 Tile")
        if any(t.source[2] > size[0] or t.source[3] > size[1] for t in tiles):
            raise ValueError("裁片超出原图")
        self.size, self.tiles = size, tuple(tiles)
        enclosing = (max(t.bounds[2] for t in tiles), max(t.bounds[3] for t in tiles))
        if canvas_size is not None:
            if (not isinstance(canvas_size, tuple) or len(canvas_size) != 2
                    or any(type(v) is not int for v in canvas_size)):
                raise TypeError("画布尺寸必须是整数二元组")
            if min(canvas_size) <= 0 or any(a > b for a, b in zip(enclosing, canvas_size)):
                raise ValueError("图块超出固定画布")
        self.canvas_size = enclosing if canvas_size is None else canvas_size

    def fit(self, canvas_size: tuple[int, int] = PACKED_CANVAS_SIZE) -> PackingPlan:
        """把整套排列等比例放入固定画布，留灰边，不按每块分别放大。

        Args:
            canvas_size: 目标正整数宽高，默认固定 640×640。

        Returns:
            原图 source 不变、目标矩形已缩放且居中的新规划。像素取整误差小于一像素。

        Raises:
            TypeError: 目标尺寸类型错误。
            ValueError: 尺寸非正，或小到无法容纳图块及其间隔。
        """
        if (not isinstance(canvas_size, tuple) or len(canvas_size) != 2
                or any(type(v) is not int for v in canvas_size)):
            raise TypeError("目标画布尺寸必须是整数二元组")
        if min(canvas_size) <= 0:
            raise ValueError("目标画布尺寸必须为正")
        width, height = self.canvas_size
        scale = min(canvas_size[0]/width, canvas_size[1]/height)
        dx, dy = (canvas_size[0]-round(width*scale))//2, (canvas_size[1]-round(height*scale))//2
        tiles = [Tile(t.source, dx+round(t.x*scale), dy+round(t.y*scale),
                      (round(t.render_size[0]*scale), round(t.render_size[1]*scale))) for t in self.tiles]
        # 取整后相邻图块仍需有至少一个空白像素，不能引入重叠所有权。
        if any(not (a.bounds[2] < b.bounds[0] or b.bounds[2] < a.bounds[0]
                    or a.bounds[3] < b.bounds[1] or b.bounds[3] < a.bounds[1])
               for i, a in enumerate(tiles) for b in tiles[i+1:]):
            raise ValueError("固定画布太小，图块重叠或没有空白间隔")
        return PackingPlan(self.size, tiles, canvas_size)

    def restore(self, detections: Sequence[Detection]) -> tuple[list[Detection], str]:
        """还原检测并消除跨块重复，出现未解决的切边或类别冲突时要求回退。

        Args:
            detections: 当前画布的像素检测；同一原始帧，不跨帧合并。
        Returns:
            (原图检测, 回退原因)；原因非空时调用者必须丢弃本结果并复查整图。
        """
        safe: list[tuple[Detection, int]] = []
        clipped: list[Detection] = []
        for detection in detections:
            a, b, c, d = detection.bbox
            owners = [(i, t) for i, t in enumerate(self.tiles)
                      if t.bounds[0] <= (a+c)/2 < t.bounds[2] and t.bounds[1] <= (b+d)/2 < t.bounds[3]]
            if len(owners) != 1:
                return [], "检测落在拼图间隔或跨越图块"
            index, tile = owners[0]
            x1, y1, x2, y2 = tile.bounds
            if a < x1 or b < y1 or c > x2 or d > y2:
                return [], "检测跨越图块边界"
            sx, sy, ex, ey = tile.source
            restored = Detection(detection.name, tile.unproject(detection.bbox), detection.confidence)
            cut = ((sx > 0 and a <= x1+2) or (sy > 0 and b <= y1+2)
                   or (ex < self.size[0] and c >= x2-2) or (ey < self.size[1] and d >= y2-2))
            if cut:
                clipped.append(restored)
            else:
                safe.append((restored, index))
        kept: list[tuple[Detection, int]] = []
        for detection, index in sorted(safe, key=lambda item: -item[0].confidence):
            duplicate = False
            for old, old_index in kept:
                if index == old_index:
                    continue
                iou = overlap(detection.bbox, old.bbox)
                if iou >= .65:
                    if detection.name != old.name:
                        return [], "同一原图位置的类别冲突"
                    duplicate = True
                    break
            if not duplicate:
                kept.append((detection, index))
        for fragment in clipped:
            if not any(overlap(fragment.bbox, whole.bbox, smaller=True) >= .85 for whole, _ in kept):
                return [], "切边目标缺少完整副本"
        return [d for d, _ in kept], ""


def make_plan(size: tuple[int, int], regions: Sequence[Box], guard: float = .04) -> PackingPlan:
    """将归一化区域向外取整、合并相交区、重叠切分长条并选取紧凑排列。

    Args:
        size: 正整数原图宽高。
        regions: 非空区域序列，坐标在 0~1 以内。
        guard: 中心切口左右各保留的原图宽度比例，默认 4%；运行链路还参考实测牌框宽度。
    Returns:
        无逐块缩放的拼图规划；模型执行自己的等比例缩放。
    Raises:
        TypeError: 尺寸或参数类型错误。
        ValueError: 尺寸非正、矩形无效、保护比例不合法。
    Examples:
        >>> len(make_plan((1000, 200), [(0., 0., 1., 1.)]).tiles)
        2
    """
    if not isinstance(size, tuple) or len(size) != 2 or any(type(v) is not int for v in size):
        raise TypeError("图像尺寸必须是整数二元组")
    if type(guard) not in (int, float):
        raise TypeError("切口保护比例必须是数字")
    if min(size) <= 0 or not isfinite(guard) or not 0 < guard < .25 or not regions:
        raise ValueError("图像尺寸、保护比例或区域列表无效")
    width, height = size
    rects: list[Rect] = []
    for region in regions:
        if not isinstance(region, (list, tuple)) or len(region) != 4 or any(type(v) not in (int, float) for v in region):
            raise TypeError("区域必须包含四个数字")
        a, b, c, d = region
        if not all(isfinite(v) for v in region) or not (0 <= a < c <= 1 and 0 <= b < d <= 1):
            raise ValueError("区域必须是归一化非空矩形")
        rects.append((floor(a*width), floor(b*height), ceil(c*width), ceil(d*height)))
    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            for j in range(i+1, len(rects)):
                first, second = rects[i], rects[j]
                # 相邻区域向外取整可能仅重叠一像素；不能因此把手牌长条重新并回整图。
                if (min(first[2], second[2])-max(first[0], second[0]) > 1
                        and min(first[3], second[3])-max(first[1], second[1]) > 1):
                    rects[i] = (min(first[0], second[0]), min(first[1], second[1]),
                                max(first[2], second[2]), max(first[3], second[3]))
                    rects.pop(j)
                    merged = True
                    break
            if merged:
                break
    pieces: list[Rect] = []
    for a, b, c, d in rects:
        if c-a > 2.4*(d-b) and c-a >= 16:
            center, margin = (a+c)//2, min((c-a)//4, max(2, ceil(width*guard)))
            pieces.extend(((a, b, min(c, center+margin), d), (max(a, center-margin), b, c, d)))
        else:
            pieces.append((a, b, c, d))
    gap = max(4, round(min(size)*.01))
    candidates: list[PackingPlan] = []
    for columns in range(1, len(pieces)+1):
        tiles: list[Tile] = []
        y = 0
        for start in range(0, len(pieces), columns):
            row = pieces[start:start+columns]
            x = 0
            for rect in row:
                tiles.append(Tile(rect, x, y))
                x += rect[2]-rect[0]+gap
            y += max(r[3]-r[1] for r in row)+gap
        candidates.append(PackingPlan(size, tiles))
    return min(candidates, key=lambda p: (max(p.canvas_size), p.canvas_size[0]*p.canvas_size[1]))
