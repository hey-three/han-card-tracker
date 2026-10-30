"""同帧同位置检测去重：通常按置信度，普通J与大小王冲突时保留大小王。"""

from collections.abc import Collection, Sequence

from core.frame_packing import Detection, overlap


DUPLICATE_IOU = .65


def _is_joker_letter(jack: Detection, joker: Detection) -> bool:
    """判断普通J是否为大小王竖排文字顶部的内嵌字母。

    Args:
        jack: 普通J候选。
        joker: 大小王候选，已通过模型自身的检测阈值。
    Returns:
        J至少90%面积落入竖长框且宽度、中心、顶部位置符合字母关系时为True。
    """
    a, b, c, d = jack.bbox
    x1, y1, x2, y2 = joker.bbox
    width, height, jw, jh = c-a, d-b, x2-x1, y2-y1
    intersection = max(0., min(c, x2)-max(a, x1))*max(0., min(d, y2)-max(b, y1))
    return (jh >= 1.5*jw and intersection/(width*height) >= .9 and height <= .65*jh
            and .45*jw <= width <= 1.15*jw and abs((a+c-x1-x2)/2) <= .2*jw
            and b <= y1+.25*jh)


def resolve_duplicate_detections(
    detections: Sequence[Detection], *, joker_labels: Collection[str] = ('JOKER', 'joker'),
    jack_labels: Collection[str] = ('J',),
) -> tuple[list[Detection], list[Detection]]:
    """在恢复后的原图坐标中，按位置去重且保持原输入顺序。

    Args:
        detections: 同帧有效完整候选；拼图须先处理内部残片并还原坐标。
        joker_labels: 当前模型映射为大小王的类别名。
        jack_labels: 当前模型映射为普通J的类别名。
    Returns:
        保留项与删除项。通常IoU至少0.65判为同一位置，保留置信度最高者，
        同分保留先出现者；J与大小王高重叠或符合内嵌字母几何时先排除J，
        不额外设置大小王置信度门槛。相邻牌、不同位置的相同点数不合并。
    """
    jokers = [card for card in detections if card.name in joker_labels]
    excluded = {index for index, card in enumerate(detections) if card.name in jack_labels
                and any(overlap(card.bbox, joker.bbox) >= DUPLICATE_IOU or _is_joker_letter(card, joker)
                        for joker in jokers)}
    kept: list[int] = []
    for index in sorted((i for i in range(len(detections)) if i not in excluded),
                        key=lambda i: -detections[i].confidence):
        if not any(overlap(detections[index].bbox, detections[old].bbox) >= DUPLICATE_IOU for old in kept):
            kept.append(index)
    kept_set = set(kept)
    return ([card for i, card in enumerate(detections) if i in kept_set],
            [card for i, card in enumerate(detections) if i not in kept_set])
