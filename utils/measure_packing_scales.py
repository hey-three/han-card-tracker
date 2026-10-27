"""从现有 YOLO 返回的单牌框测量各区字体尺度；只在显式运行 CLI 时加载模型。"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
from statistics import median

from core.packed_pipeline import read_detections


def measure(images: Sequence[Path], model_path: Path, layout: Mapping[str, Sequence[float]]) -> dict[str, object]:
    """测量普通单牌中位宽高，将大小王长竖框单独列出。

    Args:
        images: 用户的参考图路径，不修改图片。
        model_path: 已有 .pt 权重，不下载或训练。
        layout: 归一化手动区域，仅用于分配目标所属区域。
    Returns:
        模型/图片哈希、单牌检测、各区域统计和实测缩放系数。
    Raises:
        FileNotFoundError: 本地权重或图片不存在。
        ValueError: 缺少底牌、手牌或三家出牌的实测普通牌样本。
        RuntimeError: 模型执行失败。
    """
    if not model_path.is_file():
        raise FileNotFoundError(model_path)
    from PIL import Image
    import torch
    from ultralytics import YOLO

    torch.set_num_threads(2)
    model = YOLO(str(model_path))
    heights: dict[str, list[float]] = defaultdict(list)
    widths: dict[str, list[float]] = defaultdict(list)
    jokers: dict[str, list[float]] = defaultdict(list)
    sources: list[dict[str, object]] = []
    for path in images:
        with Image.open(path) as loaded:
            image = loaded.convert("RGB")
        # 固定测量条件，独立于应用后来选取的矩形输入。
        raw = model(image, imgsz=960, conf=.6, iou=.45, device="cpu", verbose=False)[0]
        detections: list[dict[str, object]] = []
        for card in read_detections(raw):
            a, b, c, d = card.bbox
            cx, cy = (a+c)/(2*image.width), (b+d)/(2*image.height)
            owner = next((k for k, (x1, y1, x2, y2) in layout.items() if x1 <= cx <= x2 and y1 <= cy <= y2), None)
            detections.append({"name": card.name, "bbox": card.bbox, "confidence": card.confidence, "zone": owner})
            if owner is not None:
                if "joker" in card.name.lower():
                    jokers[owner].append(d-b)
                else:
                    heights[owner].append(d-b)
                    widths[owner].append(c-a)
        sources.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "size": image.size, "detections": detections})
    if any(not heights[k] for k in layout):
        raise ValueError("每个业务区域至少需要一个真实普通单牌检测，不能用区域框高度代替")
    played = [h for k in ("opponent_left", "opponent_right", "player_played") for h in heights[k]]
    reference = median(played)
    summary = {k: {"count": len(heights[k]), "median_height": median(heights[k]),
                   "median_width": median(widths[k]), "joker_heights": jokers[k]} for k in layout}
    scales = {k: reference/median(heights[k])*(1.12 if k == "landlord_cards" else 1.) for k in layout}
    return {"model": str(model_path), "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "confidence": .6, "iou": .45, "reference_height": reference, "summary": summary,
            "scales": scales, "regions": dict(layout), "records": sources}


def main() -> None:
    """读取显式指定的参考目录和手动布局，把报告写入项目内指定位置。

    Raises:
        ValueError: 布局不存在、没有参考图或输出位置在项目外。
    """
    root = Path(__file__).resolve().parents[1]
    os.environ.setdefault("YOLO_CONFIG_DIR", str(root/"tests/.tmp/ultralytics"))
    from config import settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=root/"tmp")
    parser.add_argument("--layout", required=True)
    parser.add_argument("--model", type=Path, default=root/"yolo/weights"/settings.YOLO_MODEL_NAME/"best.pt")
    parser.add_argument("--output", type=Path, default=root/"tests/.tmp/v431-scale-measurements.json")
    args = parser.parse_args()
    if args.layout not in settings.WINDOW_LAYOUTS or settings.WINDOW_LAYOUTS[args.layout].get("mode") == "auto":
        raise ValueError("需要指定已框好五区的手动布局")
    if not args.output.resolve().is_relative_to(root):
        raise ValueError("报告必须保存在项目目录内")
    images = sorted(p for p in args.source_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    if not images:
        raise ValueError("参考目录没有图片")
    report = measure(images, args.model, settings.WINDOW_LAYOUTS[args.layout]["layout"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("reference_height", "summary", "scales")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
