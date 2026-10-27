"""由 tmp 三张参考图的真实 YOLO 单牌框测得的当前牌桌拼图尺度。

原图 1284×804；模型 yolov11s_imgsz_960/best.pt，conf=.6，IoU=.45。
详细样本计数、大小王例外、公式与矩形对照见 docs/V4.3.1设计与验证.md。
这里的权重归一单牌标记高度，不是归一用户框选的区域尺寸。
"""

from collections.abc import Mapping

PACKED_INPUT_SIZE = (768, 480)  # 宽、高；模型接口 imgsz 使用 (480, 768)。
REFERENCE_CARD_HEIGHT = 38.581787109375
MEASURED_CARD_HEIGHTS: dict[str, float] = {
    "landlord_cards": 26.15540885925293,
    "opponent_left": 38.57362365722656,
    "opponent_right": 38.489990234375,
    "player_hand": 54.80694580078125,
    "player_played": 46.147979736328125,
}
REGION_SCALE_FACTORS = {key: REFERENCE_CARD_HEIGHT/value*(1.12 if key == "landlord_cards" else 1.)
                        for key, value in MEASURED_CARD_HEIGHTS.items()}
OPENING_REGIONS = ("landlord_cards", "player_hand")
RECORDING_REGIONS = ("landlord_cards", "opponent_left", "opponent_right", "player_played")


def stage_regions(layout: Mapping[str, list[float]], recording: bool) -> tuple[list[str], list[tuple[float, float, float, float]], list[float]]:
    """按初始牌是否已经扣除，选择二个或四个完整区域及实测尺度。

    Args:
        layout: 已验证的全部五个归一化手工区域。
        recording: 初始手牌已稳定扣除时为 True。
    Returns:
        顺序一致的区域名、矩形和单牌高度归一权重。
    Raises:
        KeyError: 缺少当前阶段必需区域。
    """
    names = list(RECORDING_REGIONS if recording else OPENING_REGIONS)
    regions = [(layout[k][0], layout[k][1], layout[k][2], layout[k][3]) for k in names]
    return names, regions, [REGION_SCALE_FACTORS[k] for k in names]
