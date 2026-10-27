"""按布局选择整图或手动分阶段拼图；只缓存几何与画布，不缓存识别结果。"""

from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter
from typing import Protocol, cast

from PIL import Image

from core.auto_layout import Box, YoloResult
from core.frame_packing import Detection, PackingPlan
from core.square_packing import make_square_plan, make_rect_plan, plan_metrics


class Detector(Protocol):
    """当前模型的最小运行接口。"""

    def detect(self, img: Image.Image | None) -> tuple[object | None, float]:
        """执行原始整图推理。

        Args:
            img: 当前截图，失败时为 None。
        Returns:
            原始结果和模型调用毫秒。
        """
        ...

    def detect_packed(self, img: Image.Image) -> tuple[object | None, float]:
        """执行兼容模型输入尺寸的拼图推理。

        Args:
            img: 同步消费的画布。
        Returns:
            原始结果与模型调用毫秒。
        """
        ...

    def restore_result(self, raw: object, img: Image.Image, cards: Sequence[Detection]) -> object:
        """构造原图坐标结果。

        Args:
            raw: 当前模型原始结果，保留类别元数据。
            img: 同一帧原图。
            cards: 已恢复的原图检测框。
        Returns:
            可解析和绘制的原图坐标结果。
        """
        ...


def read_detections(raw: object | None) -> list[Detection]:
    """读取模型画布坐标和置信度。

    Args:
        raw: 当前帧 YOLO 兼容结果，None 表示未取得结果。

    Returns:
        已校验检测；无结果时为空。
    Raises:
        ValueError: 模型框、类别及置信度数量不一致，或检测数值无效。
    """
    if raw is None:
        return []
    result = cast(YoloResult, raw)
    if result.boxes is None:
        return []
    boxes = result.boxes.xyxy.cpu().tolist()
    classes = result.boxes.cls.cpu().tolist()
    confidence_tensor = getattr(result.boxes, "conf", None)
    scores = confidence_tensor.cpu().tolist() if confidence_tensor is not None else [1.] * len(boxes)
    if not len(boxes) == len(classes) == len(scores):
        raise ValueError("检测输出长度不一致")
    return [Detection(result.names[int(class_id)], cast(Box, tuple(box)), score)
            for box, class_id, score in zip(boxes, classes, scores)]


class PackedPipeline:
    """管理固定手动区域的规划缓存、画布复用和同帧整图回退。

    Attributes:
        route: str，当前路线及回退原因。
        timings: dict[str, float]，当前分阶段毫秒、画布尺寸和模型调用次数。
        last_input: 当前帧最后一次模型调用的原图或拼图，供同步预览编码。
    """

    def __init__(self) -> None:
        """建立空会话，不申请模型或大型画布。"""
        self._canvas: Image.Image | None = None
        self.last_input: Image.Image | None = None
        self._plan_key: tuple[object, ...] | None = None
        self._plan: PackingPlan | None = None
        self._metrics: dict[str, float] = {}
        self.route = "整图"
        self.timings: dict[str, float] = {}

    def reset(self) -> None:
        """清除几何和画布；不复用上个模型、布局或会话的规划。"""
        self._canvas = None
        self.last_input = None
        self._plan_key = None
        self._plan = None
        self._metrics = {}

    def _render(self, image: Image.Image, plan: PackingPlan) -> Image.Image:
        """复用固定画布，按每片的实际等比渲染尺寸粘贴，空白填 114 灰色。

        Args:
            image: 当前原图。
            plan: 不拉伸的裁片规划。

        Returns:
            下次调用前有效的 RGB 画布；检测必须在当前线程同步完成。
        """
        if self._canvas is None or self._canvas.size != plan.canvas_size:
            self._canvas = Image.new("RGB", plan.canvas_size, (114, 114, 114))
        else:
            self._canvas.paste((114, 114, 114), (0, 0, *plan.canvas_size))
        for tile in plan.tiles:
            crop = image.crop(tile.source)
            if crop.size != tile.render_size:
                crop = crop.resize(tile.render_size, Image.Resampling.BILINEAR)
            self._canvas.paste(crop, (tile.x, tile.y))
        return self._canvas

    def detect(self, image: Image.Image | None, detector: Detector, regions: Sequence[Box] | None,
               guard: float = .04, presence_region: Box | None = None,
               scales: Sequence[float] | None = None, canvas_size: tuple[int, int] = (768, 480)) -> tuple[object | None, float]:
        """选择整图或拼图，所有不确定拼图只回退当前原图，不向 Tracker 发布部分结果。

        Args:
            image: 当前原图；None 沿用截图失败语义。
            detector: 当前单模型实例。
            regions: 全部手动区域；None 表示自动布局，始终整图推理。
            guard: 切口单侧保护宽度比例。
            presence_region: 手动底牌门禁区域；该区无牌时即使其他区有牌也复查同一原图。
            scales: 单牌检测框实测权重；None 保留旧统一缩放方图接口。
            canvas_size: 加权阶段拼图的目标宽高。
        Returns:
            原图坐标的模型结果、包含可能补查的模型调用总毫秒。
        Raises:
            Exception: 整图推理失败时传播真实故障，不能伪装为成功。
        """
        start = perf_counter()
        self.timings = {"prepare_ms": 0., "restore_ms": 0., "model_calls": 0.}
        self.last_input = None
        reason = ""
        inference_ms = 0.
        if regions is not None and image is not None:
            call_pending = False
            call_started = 0.
            try:
                key = (image.size, tuple(cast(Box, tuple(r)) for r in regions), guard,
                       tuple(scales) if scales is not None else None, canvas_size)
                if self._plan_key != key or self._plan is None:
                    self._plan = (make_square_plan(image.size, regions, guard) if scales is None else
                                  make_rect_plan(image.size, regions, scales, canvas_size, guard))
                    self._plan_key = key
                    self._metrics = plan_metrics(self._plan)
                    self._canvas = None
                    self.timings["plan_ms"] = (perf_counter()-start)*1000
                else:
                    self.timings["plan_ms"] = 0.
                plan = self._plan
                self.timings.update(self._metrics)
                self.timings["region_count"] = float(len(regions))
                canvas = self._render(image, plan)
                self.timings["prepare_ms"] = (perf_counter()-start)*1000
                self.timings["model_calls"] += 1
                call_started, call_pending = perf_counter(), True
                self.last_input = canvas
                raw, elapsed = detector.detect_packed(canvas)
                call_pending = False
                inference_ms += elapsed
                restore_start = perf_counter()
                cards, reason = plan.restore(read_detections(raw))
                if not reason and cards and presence_region is not None:
                    a, b, c, d = presence_region
                    width, height = image.size
                    if not any(a <= (card.bbox[0]+card.bbox[2])/(2*width) <= c
                               and b <= (card.bbox[1]+card.bbox[3])/(2*height) <= d for card in cards):
                        reason = "拼图底牌区无牌，复查原图"
                if not reason and raw is not None and cards:
                    result = detector.restore_result(raw, image, cards)
                    self.timings["restore_ms"] = (perf_counter()-restore_start)*1000
                    self.route = f"手动 {len(regions)} 区拼图 {plan.canvas_size[0]}×{plan.canvas_size[1]}"
                    return result, inference_ms
                # 裁片为空不能证明全图无牌，必须用同一原图复查后才能判断结束。
                reason = reason or "拼图无可靠牌面，复查全图"
                self.timings["restore_ms"] = (perf_counter()-restore_start)*1000
            except (ValueError, RuntimeError) as exc:
                if call_pending:
                    inference_ms += (perf_counter()-call_started)*1000
                reason = f"拼图回退：{exc}"
        if self.timings["prepare_ms"] == 0.:
            self.timings["prepare_ms"] = (perf_counter()-start)*1000
        self.last_input = image
        result, elapsed = detector.detect(image)
        inference_ms += elapsed
        self.timings["model_calls"] += 1
        self.route = f"整图：{reason}" if reason else ("自动布局整图" if regions is None else "截图不可用")
        return result, inference_ms
