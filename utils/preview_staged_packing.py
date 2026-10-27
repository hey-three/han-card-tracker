"""用正式规划器重建二区/四区矩形与方形对照；默认只使用合成牌桌。"""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from config.packing_profile import MEASURED_CARD_HEIGHTS, PACKED_INPUT_SIZE, stage_regions
from core.packed_pipeline import PackedPipeline
from core.square_packing import make_rect_plan, plan_metrics
from utils.preview_packing import EXAMPLE_LAYOUTS, font, pixel_regions


def measured_synthetic_source() -> Image.Image:
    """按真实单牌框中位高度绘制合成字体，避免示意图用等大字体误导缩放效果。

    Returns:
        不含私人截图的 1284×804 合成图；仅说明布局和相对尺度，不作识别评测。
    """
    image = Image.new("RGB", (1284, 804), "#12656c")
    draw = ImageDraw.Draw(image)
    keys = ("landlord_cards", "opponent_left", "opponent_right", "player_hand", "player_played")
    groups = [(("A", "K", "Q"),), (("8", "8", "9", "9"), ("J", "J")),
              (("A", "A", "A"), ("Q", "Q")), (("3",)*4+("4",)*4+("5",)*4+("6",)*4+("7",),),
              (("K", "K", "Q", "J", "10"),)]
    for name, rect, rows in zip(keys, pixel_regions(EXAMPLE_LAYOUTS["controls"]), groups):
        a, b, c, d = rect
        desired = MEASURED_CARD_HEIGHTS[name]
        size = round(desired*1.4)
        for _ in range(2):
            bbox = draw.textbbox((0, 0), "8", font=font(size))
            size = max(1, round(size*desired/max(1, bbox[3]-bbox[1])))
        face = font(size)
        body_h = round(desired)+14
        for row_index, row in enumerate(rows):
            widths = [draw.textbbox((0, 0), label, font=face)[2] for label in row]
            body_w = max(widths)+8
            step = min(body_w+16, (c-a-20-body_w)/max(1, len(row)-1))
            top = b+6+row_index*(body_h+5)
            for index, label in enumerate(row):
                left = a+10+round(index*step)
                bounds = draw.textbbox((0, 0), label, font=face)
                draw.rounded_rectangle((left, top, left+body_w, top+body_h), radius=3, fill="#fff9eb")
                draw.text((left+4, top+6-bounds[1]), label, font=face, fill="#252b31")
    return image


def main() -> None:
    """输出正式画布及阶段坐标，显式指定的私人图片仅落入忽略目录。

    Raises:
        ValueError: 输出目录超出项目，或私人图片被要求写入公开文档目录。
    """
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    private = args.source is not None
    output = args.output or (root/"tests/.tmp/v431-preview" if private else root/"docs/assets/v431")
    if not output.resolve().is_relative_to(root):
        raise ValueError("产物必须保存在项目内")
    if private and not output.resolve().is_relative_to(root/"tests/.tmp"):
        raise ValueError("私人截图的衍生图仅写入 tests/.tmp")
    output.mkdir(parents=True, exist_ok=True)
    boxes = EXAMPLE_LAYOUTS["controls"]
    keys = ("landlord_cards", "opponent_left", "opponent_right", "player_hand", "player_played")
    layout = {k: list(v) for k, v in zip(keys, boxes)}
    image = Image.open(args.source).convert("RGB") if private else measured_synthetic_source()
    reports: dict[str, dict[str, object]] = {}
    overview = Image.new("RGB", (1568, 670), "#18232d")
    draw = ImageDraw.Draw(overview)
    draw.text((18, 18), "V4.3.1 · 实测单牌尺度归一 · 两阶段固定 768 × 480", font=font(28), fill="white")
    for index, recording in enumerate((False, True)):
        names, regions, scales = stage_regions(layout, recording)
        stage = "recording" if recording else "opening"
        for canvas in (PACKED_INPUT_SIZE, (608, 608), (640, 640)):
            plan = make_rect_plan(image.size, regions, scales, canvas)
            rendered = PackedPipeline()._render(image, plan)
            name = f"{stage}-{canvas[0]}x{canvas[1]}"
            rendered.save(output/(name+".png"))
            reports[name] = {"regions": names, "scales": scales, "metrics": plan_metrics(plan),
                             "tiles": [{"source": t.source, "destination": t.bounds} for t in plan.tiles]}
            if canvas == PACKED_INPUT_SIZE:
                x = 16+index*784
                draw.text((x, 70), "已扣手牌：三家出牌＋底牌" if recording else "尚未扣手牌：底牌＋本家手牌", font=font(22), fill="white")
                overview.paste(rendered, (x, 110))
                draw.text((x, 606), f"完整保留所选区 · {len(plan.tiles)} 裁片 · 留白 {plan_metrics(plan)['padding_fraction']:.2%}",
                          font=font(20), fill="#b8cad6")
    overview.save(output/"stages-overview.png")
    (output/"plans.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: v["metrics"] for k, v in reports.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
