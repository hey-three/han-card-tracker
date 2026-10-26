"""可关闭的单帧拼图执行器；原始截图保留用于同帧回退，不缓存识别结果。"""

from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter
from math import ceil, floor
from typing import Protocol, cast

from PIL import Image, ImageChops, ImageDraw

from core.auto_layout import Box, YoloResult
from core.frame_packing import Detection, PackingPlan, make_plan


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
    """管理拼图、变化检查和同帧回退。

    Attributes:
        route: str，当前路线及回退原因。
        timings: dict[str, float]，当前分阶段毫秒、画布尺寸和模型调用次数。
    """

    def __init__(self) -> None:
        """建立空会话，不申请模型或大型画布。"""
        self._canvas: Image.Image | None = None
        self._reference: Image.Image | None = None
        self._size: tuple[int, int] | None = None
        self._last_full = 0.
        self._packed_count = 0
        self.route = "整图"
        self.timings: dict[str, float] = {}

    def reset(self) -> None:
        """清除画面参照和复查计数，不复用上个模型、布局或会话的候选。"""
        self._reference = None
        self._canvas = None
        self._size = None
        self._last_full = 0.
        self._packed_count = 0

    def _changed_outside(self, image: Image.Image, plan: PackingPlan) -> bool:
        """在低分辨率参照图中检查未覆盖区变化。

        Args:
            image: 当前原图。
            plan: 当前候选覆盖范围。

        Returns:
            是否要求整图复查；这是触发器而非证明未覆盖区为空的识别器。
        """
        current = image.resize((128, 128), Image.Resampling.BILINEAR).convert("L")
        if self._reference is None:
            return True
        difference = ImageChops.difference(current, self._reference).point([0]*24 + [255]*232)
        draw = ImageDraw.Draw(difference)
        width, height = image.size
        for tile in plan.tiles:
            a, b, c, d = tile.source
            # 向内取整，边界缩略像素也保持监视，宁可多复查而非扩大盲区。
            left, top, right, bottom = ceil(a/width*128), ceil(b/height*128), floor(c/width*128)-1, floor(d/height*128)-1
            if right >= left and bottom >= top:
                draw.rectangle((left, top, right, bottom), fill=0)
        return difference.getbbox() is not None

    def _render(self, image: Image.Image, plan: PackingPlan) -> Image.Image:
        """复用固定画布，以统一比例缩放并粘贴原图裁片，空白填充 114 灰色。

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
               enabled: bool, confirm_frames: int, guard: float = .04) -> tuple[object | None, float]:
        """选择整图或拼图，所有不确定拼图只回退当前原图，不向 Tracker 发布部分结果。

        Args:
            image: 当前原图；None 沿用截图失败语义。
            detector: 当前单模型实例。
            regions: 当前布局的保守候选区；未校准为 None。
            enabled: 用户主动开启试验功能。
            confirm_frames: 确认阈值，用于避免定期复查打断稳定观测。
            guard: 切口单侧保护宽度比例。
        Returns:
            原图坐标的模型结果、包含可能补查的模型调用总毫秒。
        Raises:
            Exception: 整图推理失败时传播真实故障，不能伪装为成功。
        """
        start = perf_counter()
        self.timings = {"prepare_ms": 0., "restore_ms": 0., "model_calls": 0.}
        reason = ""
        inference_ms = 0.
        if image is not None and image.size != self._size:
            self.reset()
            self._size = image.size
        eligible = enabled and image is not None and regions and self._reference is not None
        if eligible and image is not None and regions is not None:
            if self._packed_count >= max(3, confirm_frames)*2 or perf_counter()-self._last_full >= 2.:
                reason = "定期整图复查"
            else:
                call_pending = False
                call_started = 0.
                try:
                    plan = make_plan(image.size, regions, guard).fit()
                    if self._changed_outside(image, plan):
                        reason = "候选区外画面变化"
                    else:
                        canvas = self._render(image, plan)
                        self.timings["prepare_ms"] = (perf_counter()-start)*1000
                        self.timings["model_calls"] += 1
                        call_started, call_pending = perf_counter(), True
                        raw, elapsed = detector.detect_packed(canvas)
                        call_pending = False
                        inference_ms += elapsed
                        restore_start = perf_counter()
                        cards, reason = plan.restore(read_detections(raw))
                        if not reason and raw is not None and cards:
                            result = detector.restore_result(raw, image, cards)
                            self.timings["restore_ms"] = (perf_counter()-restore_start)*1000
                            self.timings["canvas_width"], self.timings["canvas_height"] = plan.canvas_size
                            self.route = "保护拼图（试验）"
                            self._packed_count += 1
                            return result, inference_ms
                        reason = reason or "拼图无可靠牌面"
                        self.timings["restore_ms"] = (perf_counter()-restore_start)*1000
                except (ValueError, RuntimeError) as exc:
                    if call_pending:
                        inference_ms += (perf_counter()-call_started)*1000
                    reason = f"拼图回退：{exc}"
        if self.timings["prepare_ms"] == 0.:
            self.timings["prepare_ms"] = (perf_counter()-start)*1000
        result, elapsed = detector.detect(image)
        inference_ms += elapsed
        self.timings["model_calls"] += 1
        self.route = f"整图：{reason}" if reason else ("整图校准" if enabled else "整图")
        reference_started = perf_counter()
        if enabled and image is not None:
            self._reference = image.resize((128, 128), Image.Resampling.BILINEAR).convert("L")
        else:
            self._reference = None
        self.timings["prepare_ms"] += (perf_counter()-reference_started)*1000
        self._last_full, self._packed_count = perf_counter(), 0
        return result, inference_ms
