"""生成真实规划器/渲染器的合成拼图示意，不读取私人截图或第三方 APK。"""

from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from core.auto_layout import AutoLayoutAnalyzer, CardBox
from core.frame_packing import PackingPlan, make_plan
from core.packed_pipeline import PackedPipeline


def card_row(names: Sequence[str], x: float, y: float, step: float = .04, height: float = .045) -> list[CardBox]:
    """生成用于可视化的归一化牌框，不代表真实检测结果。

    Args:
        names: 牌面标签。
        x: 第一张牌的左边界。
        y: 行顶边。
        step: 横向步长。
        height: 单框高度。

    Returns:
        一行检测框。
    """
    return [CardBox(name, (x+i*step, y, x+i*step+.025, y+height)) for i, name in enumerate(names)]


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """优先使用 Windows 微软雅黑，其他环境退回 Pillow 默认字体。

    Args:
        size: 字号像素。

    Returns:
        可用于绘制文字的字体。
    """
    path = Path("C:/Windows/Fonts/msyh.ttc")
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default(size=size)


def synthetic_frame() -> tuple[Image.Image, PackingPlan]:
    """以模拟牌桌校准，再用实际规划器生成带跨行出牌的固定画布规划。

    Returns:
        合成原图及 640×640 规划，不包含任何真实游戏图片。
    """
    image = Image.new("RGB", (1000, 800), "#12656c")
    hand = card_row(["3"]*4 + ["4"]*4 + ["5"]*4 + ["6"]*4 + ["7"], .1, .76, .047)
    bottom = card_row(["A", "K", "Q"], .43, .1, .035, .035)
    analyzer = AutoLayoutAnalyzer()
    for _ in range(3):
        analyzer.observe(hand+bottom, image.size, 3)
    plays = (card_row(["8", "8", "9", "9", "10", "10", "J", "J"], .22, .37, .032)
             + card_row(["Q", "Q"], .22, .425, .032)
             + card_row(["A", "A", "A"], .7, .37)
             + card_row(["K", "K"], .47, .57))
    draw = ImageDraw.Draw(image)
    draw.text((25, 20), "合成牌桌 · 非真实游戏截图", font=font(20), fill="#e1f2ed")
    for card in hand+bottom+plays:
        a, b, c, d = card.bbox
        rect = (round(a*1000), round(b*800), round(c*1000), round(d*800))
        draw.rounded_rectangle(rect, radius=3, fill="#fff9eb")
        draw.text((rect[0]+2, rect[1]+4), card.name, font=font(14), fill="#a9383c" if card.name in ("A", "Q", "4") else "#252b31")
    regions, guard = analyzer.inference_regions()
    assert regions is not None
    return image, make_plan(image.size, regions, guard).fit()


def main() -> None:
    """写入原图、实际固定画布及带说明的对照图，供文档与训练约定引用。"""
    output = Path(__file__).resolve().parent.parent / "docs/assets"
    output.mkdir(parents=True, exist_ok=True)
    source, plan = synthetic_frame()
    # 与运行时使用同一渲染入口；标注边框只画在额外的讲解图上。
    packed = PackedPipeline()._render(source, plan)
    source.save(output / "packing-source.png")
    packed.save(output / "packing-fixed640.png")
    left, right = source.copy(), packed.copy()
    ld, rd = ImageDraw.Draw(left), ImageDraw.Draw(right)
    colors = ("#55dca7", "#ffc76c", "#77c9ff", "#d5a3ff", "#ff8e96")
    for i, tile in enumerate(plan.tiles):
        color = colors[i % len(colors)]
        ld.rectangle(tile.source, outline=color, width=3)
        rd.rectangle(tile.bounds, outline=color, width=2)
        rd.text((tile.x+5, tile.y+5), str(i+1), font=font(16), fill=color)
    overview = Image.new("RGB", (1400, 900), "#18232d")
    draw = ImageDraw.Draw(overview)
    draw.text((36, 22), "自动选区域 → 重叠保护 → 统一缩放 → 固定 640 × 640", font=font(27), fill="white")
    draw.text((36, 85), "原图：1000 × 800（彩框是裁片原始范围）", font=font(20), fill="#b8cad6")
    draw.text((725, 85), "固定画布：640 × 640", font=font(20), fill="#b8cad6")
    overview.paste(left.resize((640, 512), Image.Resampling.BILINEAR), (36, 130))
    overview.paste(right, (725, 130))
    draw.text((36, 675), "相邻裁片的中间部分会重叠，保护切口处的牌。", font=font(20), fill="white")
    draw.text((36, 710), "保留全宽桌面带，覆盖长牌组和向下展开的第二行。", font=font(20), fill="white")
    draw.text((36, 800), "颜色和编号仅用于讲解；实际模型输入没有这些标注。", font=font(20), fill="#b8cad6")
    draw.text((36, 835), "裁片数量、位置可变化；最终画布尺寸不变化。", font=font(20), fill="#b8cad6")
    overview.save(output / "packing-explained.png")


if __name__ == "__main__":
    main()
