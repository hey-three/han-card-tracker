"""保存手动布局前的五区归一化坐标校验。"""

from collections.abc import Mapping

from utils.layout_editor.coord import REGION_KEYS


def validate_normalized_layout(layout: Mapping[str, object]) -> tuple[bool, str]:
    """检查五区是否齐全、坐标格式正确且为0到1内的非空矩形。

    Args:
        layout: 区域键到四个边界坐标的映射，接受列表或元组形式的坐标。
    Returns:
        校验结果及原因；非法区域返回False和说明，全部合法返回True和ok。
    """
    for key in REGION_KEYS:
        if key not in layout:
            return False, f"缺少区域: {key}"
        box = layout[key]
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return False, f"区域 {key} 坐标格式错误"
        try:
            x1, y1, x2, y2 = [float(v) for v in box]
        except Exception:
            return False, f"区域 {key} 坐标必须是数字"

        if not (0.0 <= x1 <= 1.0 and 0.0 <= x2 <= 1.0 and 0.0 <= y1 <= 1.0 and 0.0 <= y2 <= 1.0):
            return False, f"区域 {key} 坐标必须在 0~1"
        if x1 >= x2 or y1 >= y2:
            return False, f"区域 {key} 坐标范围无效（x1<x2, y1<y2）"
    return True, "ok"
