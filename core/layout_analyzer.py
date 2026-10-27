# -*- coding: utf-8 -*-
"""布局几何分析模块。

负责将 YOLO 输出的原始检测框，根据配置好的几何区域进行裁片划分，
并对每个区域内的检测框进行按行按列的二维空间排序。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Dict, List, Tuple, cast

from config.settings import LayoutConfig
from core.auto_layout import YoloResult

class LayoutAnalyzer:
    """基于几何坐标的布局解析与框过滤排序工具。

    Attributes:
        layout_config: 已验证的手动布局，包含归一化区域。
    """

    def __init__(self, layout_config: LayoutConfig) -> None:
        """初始化分析器。
        
        Args:
            layout_config: 当前使用的布局配置字典 (包含 "layout" 键)。
        """
        self.layout_config = layout_config

    def sort_cards_by_topright_rowwise(self, dets: List[Dict], max_rows: int = 3) -> List[Dict]:
        """按右上角坐标对检测框进行先行后列排序。

        Args:
            dets: 检测结果列表，每个元素包含 ``bbox`` (x1, y1, x2, y2) 和 ``name``。
            max_rows: 期望的最大行数，默认 3。

        Returns:
            List[Dict]: 排好序的检测结果列表。
        """
        if len(dets) == 0:
            return []

        feats: List[Tuple[float, float, int]] = []
        heights = []
        for i, d in enumerate(dets):
            x1, y1, x2, y2 = d["bbox"]
            feats.append((float(y1), float(x2), i))
            heights.append(max(1, (y2 - y1)))

        heights_sorted = sorted(heights)
        mid = len(heights_sorted) // 2
        median_h = heights_sorted[mid] if len(heights_sorted) % 2 == 1 else (heights_sorted[mid - 1] + heights_sorted[mid]) / 2.0
        tol = median_h * 0.55

        feats.sort(key=lambda t: t[0])

        rows: List[Dict] = []
        for top_y, right_x, idx in feats:
            placed = False
            for row in rows:
                if abs(top_y - row["anchor_y"]) <= tol:
                    row["items"].append((top_y, right_x, idx))
                    n = len(row["items"])
                    row["anchor_y"] = (row["anchor_y"] * (n - 1) + top_y) / n
                    placed = True
                    break
            if not placed:
                rows.append({"anchor_y": top_y, "items": [(top_y, right_x, idx)]})

        rows.sort(key=lambda r: r["anchor_y"])

        if max_rows is not None and len(rows) > max_rows:
            kept = rows[:max_rows]
            extra = rows[max_rows:]
            for er in extra:
                target = min(kept, key=lambda r: abs(er["anchor_y"] - r["anchor_y"]))
                target["items"].extend(er["items"])
                ys = [it[0] for it in target["items"]]
                target["anchor_y"] = sum(ys) / len(ys)
            rows = kept
            rows.sort(key=lambda r: r["anchor_y"])

        sorted_indices: List[int] = []
        for row in rows:
            row["items"].sort(key=lambda t: t[1])
            sorted_indices.extend([idx for _, _, idx in row["items"]])

        return [dets[i] for i in sorted_indices]

    def parse_and_sort(self, raw_result: object | None, img_size: tuple[int, int],
                       region_keys: Sequence[str] | None = None) -> dict[str, list[dict[str, object]]]:
        """在原图归一化坐标中分区并按行排序，不向内截断区域边界。

        Args:
            raw_result: 原始或已恢复原图坐标的 YOLO 结果。
            img_size: 原图宽高；零尺寸或无结果表示未知画面，返回空区域。
            region_keys: 当前阶段实际识别区域；None 解析全部，不把跳过区域伪装为空区。
        Returns:
            全部手动区域及其有序目标；交叠区域按用户配置顺序确定唯一归属。
        Raises:
            ValueError: 模型框和类别数量不一致。
        """
        layout = {k: v for k, v in self.layout_config["layout"].items() if region_keys is None or k in region_keys}
        results: dict[str, list[dict[str, object]]] = {key: [] for key in layout}
        width, height = img_size
        if raw_result is None or min(img_size) <= 0:
            return results
        raw = cast(YoloResult, raw_result)
        if raw.boxes is None:
            return results
        boxes = raw.boxes.xyxy.cpu().tolist()
        classes = raw.boxes.cls.cpu().tolist()
        if len(boxes) != len(classes):
            raise ValueError("检测框和类别数量不一致")
        for box, class_id in zip(boxes, classes):
            x1, y1, x2, y2 = box
            cx, cy = (x1+x2)/(2*width), (y1+y2)/(2*height)
            for key, (a, b, c, d) in layout.items():
                if a <= cx <= c and b <= cy <= d:
                    results[key].append({"bbox": (float(x1), float(y1), float(x2), float(y2)),
                                         "name": raw.names[int(class_id)]})
                    break
        return {key: self.sort_cards_by_topright_rowwise(cards) for key, cards in results.items()}
