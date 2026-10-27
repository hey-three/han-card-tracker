"""使用正式五区规划器生成合成示意及可复核面积数据，不读取私人截图。"""

from collections.abc import Sequence
import json
from math import ceil, floor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from core.frame_packing import Box, PackingPlan
from core.packed_pipeline import PackedPipeline
from core.square_packing import make_square_plan, plan_metrics, union_area

EXAMPLE_LAYOUTS: dict[str, list[Box]] = {
    "full": [(.37, 0., .55, .08), (.2, .25, .47, .45), (.48, .25, .85, .45),
             (.04, .7, .96, .85), (.04, .46, .96, .57)],
    "controls": [(.35, .08, .45, .15), (.2, .32, .455, .49), (.46, .32, .8, .49),
                 (.04, .7, .96, .85), (.04, .5, .96, .6)],
}
LABELS = ("底牌", "上家出牌", "下家出牌", "本家手牌", "本家出牌")
COLORS = ("#ffc76c", "#77c9ff", "#d5a3ff", "#55dca7", "#ff8e96")
SOURCE_SIZE = (1284, 804)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """优先使用微软雅黑，其余平台使用 Pillow 默认字体。

    Args:
        size: 字体像素大小。
    Returns:
        可供 Pillow 绘图的字体。
    """
    path = Path("C:/Windows/Fonts/msyh.ttc")
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default(size=size)


def pixel_regions(regions: Sequence[Box]) -> list[tuple[int, int, int, int]]:
    """按正式规划器相同的向外取整方式返回未加保护的五区。

    Args:
        regions: 归一化业务区域。
    Returns:
        示例原图上的像素矩形。
    """
    width, height = SOURCE_SIZE
    return [(floor(a*width), floor(b*height), ceil(c*width), ceil(d*height)) for a, b, c, d in regions]


def synthetic_frame(regions: Sequence[Box] = EXAMPLE_LAYOUTS["full"]) -> tuple[Image.Image, PackingPlan]:
    """在五区内绘制十七张手牌及两行出牌，再使用正式规划器。

    Args:
        regions: 五个示例手动框，顺序为底牌、上家、下家、手牌、本家出牌。
    Returns:
        没有任何辅助标线的合成原图和真实固定 640×640 规划。
    """
    image = Image.new("RGB", SOURCE_SIZE, "#12656c")
    draw = ImageDraw.Draw(image)
    draw.text((22, 15), "合成牌桌 · 非真实游戏截图", font=font(20), fill="#e1f2ed")
    rows = [(("A", "K", "Q"),), (("8", "8", "9", "9", "10", "10"), ("J", "J")),
            (("A", "A", "A"), ("Q", "Q")),
            (("3",)*4 + ("4",)*4 + ("5",)*4 + ("6",)*4 + ("7",),), (("K", "K", "Q", "J", "10"),)]
    for rect, groups in zip(pixel_regions(regions), rows):
        a, b, c, d = rect
        card_height = min(65, (d-b-18)//len(groups)-6)
        for line, names in enumerate(groups):
            step = min(58., (c-a-32)/max(1, len(names)))
            card_width = min(36, round(step)-3)
            y = b+10+line*(card_height+10)
            for index, name in enumerate(names):
                x = a+14+round(index*step)
                draw.rounded_rectangle((x, y, x+card_width, y+card_height), radius=3, fill="#fff9eb")
                draw.text((x+2, y+2), name, font=font(18), fill="#a9383c" if name in ("A", "Q", "4") else "#252b31")
    return image, make_square_plan(image.size, regions)


def main() -> None:
    """输出两套五区的实际画布、讲解对照图及面积报告，均可安全加入项目文档。"""
    output = Path(__file__).resolve().parent.parent / "docs/assets"
    output.mkdir(parents=True, exist_ok=True)
    reports: dict[str, dict[str, object]] = {}
    for name, regions in EXAMPLE_LAYOUTS.items():
        source, plan = synthetic_frame(regions)
        packed = PackedPipeline()._render(source, plan)
        metrics = plan_metrics(plan)
        metrics["input_roi_union"] = union_area(pixel_regions(regions))/(SOURCE_SIZE[0]*SOURCE_SIZE[1])
        reports[name] = {"source_size": SOURCE_SIZE, "regions": regions, "metrics": metrics,
                         "tiles": [{"source": t.source, "destination": t.bounds} for t in plan.tiles]}
        if name == "full":
            source.save(output / "packing-source.png")
            packed.save(output / "packing-fixed640.png")
        else:
            packed.save(output / "packing-controls640.png")
        left, right = source.copy(), packed.copy()
        ld, rd = ImageDraw.Draw(left), ImageDraw.Draw(right)
        for rect, color, label in zip(pixel_regions(regions), COLORS, LABELS):
            ld.rectangle(rect, outline=color, width=3)
            ld.text((rect[0]+3, rect[3]-26), label, font=font(20), fill=color)
        for index, tile in enumerate(plan.tiles):
            rd.rectangle(tile.bounds, outline=COLORS[index % 5], width=2)
            rd.text((tile.x+3, tile.y+3), str(index+1), font=font(16), fill="#ff701a")
        overview = Image.new("RGB", (1400, 970), "#18232d")
        draw = ImageDraw.Draw(overview)
        draw.text((30, 24), "手动五区 → 重叠保护切分 → 二维装箱 → 固定 640 × 640", font=font(27), fill="white")
        draw.text((30, 90), "原图 1284 × 804：完整保留彩框内内容", font=font(20), fill="#b8cad6")
        draw.text((730, 90), f"{len(plan.tiles)} 块 · 统一比例 · 不旋转、不拉伸", font=font(20), fill="#b8cad6")
        overview.paste(left.resize((660, 413), Image.Resampling.BILINEAR), (30, 140))
        overview.paste(right, (730, 140))
        notes = [f"五区原始并集：{metrics['input_roi_union']:.2%}",
                 f"加外围保护后的覆盖：{metrics['source_coverage']:.2%}",
                 f"裁片源面积（含切口重叠）：{metrics['source_with_overlap']:.2%}",
                 f"画布留白：{metrics['padding_fraction']:.2%}",
                 "同一原位置的完整副本还原后去重，",
                 "原图不同位置的相同牌点仍保留多张。"]
        for index, line in enumerate(notes):
            draw.text((30, 590+index*34), line, font=font(21), fill="white")
        draw.text((30, 845), "自动布局始终整图推理；手动框内区域不会因当前没牌而被删掉。", font=font(21), fill="white")
        draw.text((30, 888), "辅助边框和编号仅用于讲解；实际输入没有标线。面积不是速度或准确率测评。", font=font(20), fill="#b8cad6")
        overview.save(output / ("packing-explained.png" if name == "full" else "packing-controls-explained.png"))
    (output / "packing-metrics.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: v["metrics"] for k, v in reports.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
