import cv2
import numpy as np
from collections.abc import Sequence
from typing import Dict, Union
import sys
import os

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
sys.path.insert(0, project_root)

from config import settings

def get_layout_by_name(layout_name: str | None = None) -> tuple[dict[str, Sequence[float]], str]:
    """
    根据layout名称获取配置，如果没有指定则使用第一个配置
    
    Args:
        layout_name: layout名称，如果为None则使用第一个配置
    
    Returns:
        区域配置字典及实际使用的布局名称。

    Raises:
        ValueError: 没有可用布局。
    """
    available_layouts = list(settings.WINDOW_LAYOUTS.keys())
    
    if not available_layouts:
        raise ValueError("WINDOW_LAYOUTS 字典为空，没有可用的配置")
    
    if layout_name is None:
        print(f"未指定layout，使用第一个配置: {available_layouts[0]}")
        layout_name = available_layouts[0]
    elif layout_name not in settings.WINDOW_LAYOUTS:
        print(f"警告: 找不到layout '{layout_name}'，可用选项: {available_layouts}")
        print(f"使用第一个配置: {available_layouts[0]}")
        layout_name = available_layouts[0]
    
    return settings.WINDOW_LAYOUTS[layout_name]["layout"], layout_name

def draw_layout_regions(
    layout: Dict,
    img: Union[str, np.ndarray],
    save_path: str = "layout_debug.png",
    show: bool = True,
    thickness: int = 2
) -> np.ndarray:
    """
    在图片上画出 layout 的五个区域（归一化坐标 -> 像素坐标），并保存/显示。

    layout: 例如 settings.LAYOUT_PRESETS[self.window_title]
            需包含 keys: player_hand, opponent_left, opponent_right, landlord_cards, player_played
            每个值为 (x1, y1, x2, y2) 且范围 0~1
    img: 图片路径 或 cv2读取后的 ndarray(BGR)
    save_path: 保存路径
    show: 是否弹窗显示
    thickness: 矩形线宽

    return: 画完框的 BGR 图片 ndarray
    """

    # 读图
    if isinstance(img, str):
        im = cv2.imread(img)
        if im is None:
            raise FileNotFoundError(f"读取图片失败：{img}")
    else:
        im = img.copy()

    h, w = im.shape[:2]

    # 归一化 -> 像素
    def norm_to_pixel(box):
        x1, y1, x2, y2 = box
        px1 = int(round(x1 * w))
        py1 = int(round(y1 * h))
        px2 = int(round(x2 * w))
        py2 = int(round(y2 * h))
        # 防止越界
        px1 = max(0, min(px1, w - 1))
        px2 = max(0, min(px2, w - 1))
        py1 = max(0, min(py1, h - 1))
        py2 = max(0, min(py2, h - 1))
        # 保证左上/右下
        if px1 > px2: px1, px2 = px2, px1
        if py1 > py2: py1, py2 = py2, py1
        return px1, py1, px2, py2

    # 颜色（BGR）
    colors = {
        "player_hand": (0, 255, 0),        # 绿
        "player_played": (255, 0, 255),    # 紫
        "opponent_left": (255, 0, 0),      # 蓝
        "opponent_right": (0, 0, 255),     # 红
        "landlord_cards": (0, 255, 255),   # 黄,
    }

    keys = ["player_hand", "player_played", "opponent_left", "opponent_right", "landlord_cards"]

    for k in keys:
        if k not in layout:
            raise KeyError(f"layout 缺少 ключ: {k}")

        x1, y1, x2, y2 = norm_to_pixel(layout[k])
        color = colors.get(k, (255, 255, 255))

        # 画矩形
        cv2.rectangle(im, (x1, y1), (x2, y2), color, thickness)

        # 标签背景 + 文字
        label = f"{k} ({x1},{y1})-({x2},{y2})"
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        t_thickness = 2

        (tw, th), baseline = cv2.getTextSize(label, font, font_scale, t_thickness)
        tx1, ty1 = x1, max(0, y1 - th - baseline - 4)
        tx2, ty2 = x1 + tw + 6, y1

        cv2.rectangle(im, (tx1, ty1), (tx2, ty2), color, -1)
        cv2.putText(im, label, (x1 + 3, y1 - baseline - 2), font, font_scale, (0, 0, 0), t_thickness, cv2.LINE_AA)

    # 保存
    cv2.imwrite(save_path, im)

    # 显示
    if show:
        cv2.imshow("layout_debug", im)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    return im

if __name__ == "__main__":
    
    # ==================== 在这里修改配置 ====================
    
    # 1. 指定layout名称（None表示使用第一个配置）
    LAYOUT_NAME = "JJ斗地主(含控件)"  # 例如: "JJ斗地主(含控件)" 或 "JJ斗地主(全屏)"
    
    # 2. 指定输入图片路径（相对于脚本所在目录）
    INPUT_IMAGE = "screenshot.png"
    
    # 3. 指定输出图片路径（相对于脚本所在目录）
    OUTPUT_IMAGE = "layout_debug.png"
    
    # ==================================================
    
    # 获取脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 拼接完整路径
    input_path = os.path.join(script_dir, INPUT_IMAGE)
    output_path = os.path.join(script_dir, OUTPUT_IMAGE)
    
    print(f"脚本所在目录: {script_dir}")
    print(f"当前工作目录: {os.getcwd()}")
    print(f"使用的layout: {LAYOUT_NAME if LAYOUT_NAME else '默认（第一个配置）'}")
    print(f"输入图片: {input_path}")
    print(f"输出图片: {output_path}")
    
    # 检查输入图片是否存在
    if not os.path.exists(input_path):
        print(f"\n错误: 找不到图片文件: {input_path}")
        print(f"请确保图片文件存在于: {script_dir}")
        exit(1)
    
    Layout, layout_name = get_layout_by_name(LAYOUT_NAME)
    draw_layout_regions(Layout, input_path, output_path)
