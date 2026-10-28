"""拼图画布、阶段区域与历史样例尺度；运行时使用在线自适应尺度。

原图 1284×804；模型 yolov11s_imgsz_960/best.pt，conf=.6，IoU=.45。
详细样本计数、大小王例外和公式见 docs/设计决策.md 与 docs/排查与验证.md。
下面实测权重也是用户确认的固定初始比例；识别后由 core/packing_scales.py 在线更新。
"""

from collections.abc import Mapping

PACKED_INPUT_SIZE = (768, 480)  # 无 model_config.yaml 的旧模型后备画布；正式尺寸由所选模型决定。
REFERENCE_CARD_HEIGHT = 38.581787109375
MEASURED_CARD_HEIGHTS: dict[str, float] = {
    "landlord_cards": 26.15540885925293,
    "opponent_left": 38.57362365722656,
    "opponent_right": 38.489990234375,
    "player_hand": 54.80694580078125,
    "player_played": 46.147979736328125,
}
INITIAL_REGION_SCALES = {key: REFERENCE_CARD_HEIGHT/value*(1.12 if key == "landlord_cards" else 1.)
                        for key, value in MEASURED_CARD_HEIGHTS.items()}
REGION_SCALE_FACTORS = INITIAL_REGION_SCALES  # 历史测量/示意工具兼容别名。
OPENING_REGIONS = ("landlord_cards", "player_hand")
RECORDING_REGIONS = ("landlord_cards", "opponent_left", "opponent_right", "player_played")


def stage_selection(layout: Mapping[str, list[float]], recording: bool) -> tuple[list[str], list[tuple[float, float, float, float]]]:
    """只选择当前用户布局的二区或四区，不携带任何样例牌桌比例。

    Args:
        layout: 当前用户框选的全部区域。
        recording: 初始手牌是否已扣除。
    Returns:
        当前阶段的名称和原始归一化矩形。
    Raises:
        KeyError: 缺少当前阶段必需区域。
    """
    names = list(RECORDING_REGIONS if recording else OPENING_REGIONS)
    return names, [(layout[k][0], layout[k][1], layout[k][2], layout[k][3]) for k in names]


def stage_regions(layout: Mapping[str, list[float]], recording: bool) -> tuple[list[str], list[tuple[float, float, float, float]], list[float]]:
    """复现历史样例的固定实测尺度；正式运行改用 stage_selection。

    Args:
        layout: 已验证的全部五个归一化手工区域。
        recording: 初始手牌已稳定扣除时为 True。
    Returns:
        顺序一致的区域名、矩形和单牌高度归一权重。
    Raises:
        KeyError: 缺少当前阶段必需区域。
    """
    names, regions = stage_selection(layout, recording)
    return names, regions, [REGION_SCALE_FACTORS[k] for k in names]
