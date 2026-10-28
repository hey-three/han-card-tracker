from typing import Dict, Tuple

REGION_KEYS = [
    "player_hand",
    "player_played",
    "opponent_left",
    "opponent_right",
    "landlord_cards",
]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_rect(rect: Tuple[int, int, int, int], width: int, height: int) -> Tuple[float, float, float, float]:
    x1, y1, x2, y2 = sanitize_pixel_rect(rect, width, height)
    return (
        x1 / float(width),
        y1 / float(height),
        x2 / float(width),
        y2 / float(height),
    )


def denormalize_rect(norm_rect, width: int, height: int) -> Tuple[int, int, int, int]:
    x1, y1, x2, y2 = norm_rect
    return sanitize_pixel_rect(
        (
            int(round(clamp(float(x1), 0.0, 1.0) * width)),
            int(round(clamp(float(y1), 0.0, 1.0) * height)),
            int(round(clamp(float(x2), 0.0, 1.0) * width)),
            int(round(clamp(float(y2), 0.0, 1.0) * height)),
        ),
        width,
        height,
    )


def sanitize_pixel_rect(rect: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    """规范化裁剪边界，右下边界允许等于图片尺寸。

    Args:
        rect: 任意拖动方向的两个端点。
        width: 正整数图片宽度。
        height: 正整数图片高度。
    Returns:
        左闭右开的非空像素区域。
    Raises:
        TypeError: 尺寸或端点不是整数。
        ValueError: 尺寸非正或端点数量错误。
    """
    if not isinstance(rect, (tuple, list)) or type(width) is not int or type(height) is not int:
        raise TypeError("图片尺寸和像素区域类型错误")
    if any(type(value) is not int for value in rect):
        raise TypeError('图片尺寸和像素坐标必须是整数')
    if width <= 0 or height <= 0 or len(rect) != 4:
        raise ValueError('图片尺寸必须为正，区域必须包含四个端点值')
    left, right = sorted((max(0, min(width, rect[0])), max(0, min(width, rect[2]))))
    top, bottom = sorted((max(0, min(height, rect[1])), max(0, min(height, rect[3]))))
    left, top = min(left, width-1), min(top, height-1)
    return left, top, max(left+1, right), max(top+1, bottom)


def normalize_layout(pixel_layout: Dict[str, Tuple[int, int, int, int]], width: int, height: int):
    return {k: normalize_rect(pixel_layout[k], width, height) for k in REGION_KEYS}

