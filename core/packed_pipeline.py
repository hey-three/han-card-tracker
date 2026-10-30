"""按布局选择整图或手动分阶段拼图；只缓存几何与画布，不缓存识别结果。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from time import perf_counter
import traceback
from typing import TYPE_CHECKING, Protocol, cast

from PIL import Image

from core.auto_layout import Box
from core.frame_packing import Detection, PackingPlan, read_detections
from core.square_packing import make_square_plan, make_rect_plan, plan_metrics
from core.packing_scales import AdaptivePackingScales

if TYPE_CHECKING:
    from core.diagnostics import DiagnosticFrame


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




class PackedPipeline:
    """管理固定手动区域的规划缓存、画布复用和同帧整图回退。

    Attributes:
        route: str，当前路线及回退原因。
        timings: dict[str, float]，当前分阶段毫秒、画布尺寸和模型调用次数。
        last_input: 当前帧最后一次模型调用的原图或拼图，供同步预览编码。
        last_input_plan: 对应last_input的实际拼图规划；整图、回退或无图时为空。
        scale_profile: 当前布局的单牌尺度学习器。
        _scale_context: 当前完整布局和截图尺寸，用于隔离不同用户选区。
    """

    def __init__(self) -> None:
        """建立空会话，不申请模型或大型画布。"""
        self._canvas: Image.Image | None = None
        self.last_input: Image.Image | None = None
        self.last_input_plan: PackingPlan | None = None
        self._plan_key: tuple[object, ...] | None = None
        self._plan: PackingPlan | None = None
        self._metrics: dict[str, float] = {}
        self.route = "整图"
        self.timings: dict[str, float] = {}
        self.scale_profile = AdaptivePackingScales()
        self._scale_context: tuple[object, ...] | None = None

    def reset(self) -> None:
        """清除几何和画布；不复用上个模型、布局或会话的规划。"""
        self._canvas = None
        self.last_input = None
        self.last_input_plan = None
        self._plan_key = None
        self._plan = None
        self._metrics = {}
        self.scale_profile = AdaptivePackingScales()
        self._scale_context = None

    def scale_weights(self, size: tuple[int, int], layout: Mapping[str, Sequence[float]],
                      names: Sequence[str]) -> list[float]:
        """按当前完整布局隔离尺度测量，布局或窗口尺寸改变立即重新学习。

        Args:
            size: 当前截图宽高。
            layout: 全部手动区域，阶段切换不改变此上下文。
            names: 当前阶段的区域名。
        Returns:
            从当前会话单牌框学习的缩放权重。
        """
        context = (size, tuple((k, tuple(v)) for k, v in sorted(layout.items())))
        if context != self._scale_context:
            self.scale_profile = AdaptivePackingScales()
            self._scale_context = context
        return self.scale_profile.weights(names)

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

    def _model_call(self, detector: Detector, image: Image.Image | None, packed: bool,
                    trace: DiagnosticFrame | None, plan: PackingPlan | None = None) -> tuple[object | None, float]:
        """在每次模型调用边界冻结证据，包含异常与回退前的调用。

        Args:
            detector: 当前模型。
            image: 本次实际输入。
            packed: 是否拼图。
            trace: 完整诊断关闭时为None，不复制图片。
            plan: 本次拼图坐标映射。
        Returns:
            未被诊断修改的模型结果和耗时。
        Raises:
            Exception: 原始模型调用失败时原样传播。
        """
        index = trace.begin_call('packed' if packed else 'full', image, plan) if trace else -1
        started = perf_counter()
        try:
            raw, elapsed = detector.detect_packed(image) if packed and image is not None else detector.detect(image)
        except Exception:
            if trace:
                try:
                    trace.end_call(index, None, (perf_counter()-started)*1000, traceback.format_exc())
                except Exception as exc:
                    trace.owner.pause('call_result_snapshot_failed: '+str(exc))
                    trace.cancel()
            raise
        if trace:
            try:
                trace.end_call(index, raw, elapsed)
                predictor = getattr(getattr(detector, 'model', None), 'predictor', None)
                shape = getattr(getattr(predictor, 'args', None), 'imgsz', None)
                if not trace.cancelled:
                    backend = getattr(predictor, 'model', None)
                    flags = {key: value for key in ('pt', 'onnx', 'engine', 'dnn', 'fp16', 'dynamic')
                             if type(value := getattr(backend, key, None)) is bool}
                    trace.calls[index]['backend'] = {'type': type(backend).__name__, 'flags': flags,
                                                    'device': str(getattr(backend, 'device', 'unknown'))}
                if not trace.cancelled and type(shape) in (int, list, tuple):
                    trace.calls[index]['requested_tensor_shape'] = shape
            except Exception as exc:
                trace.owner.pause('call_result_snapshot_failed: '+str(exc))
                trace.cancel()
        return raw, elapsed

    def detect(self, image: Image.Image | None, detector: Detector, regions: Sequence[Box] | None,
               guard: float = .04, presence_region: Box | None = None,
               scales: Sequence[float] | None = None, canvas_size: tuple[int, int] = (768, 480),
               region_names: Sequence[str] | None = None, allow_full_fallback: bool = True,
               full_label: str = '自动布局整图', trace: DiagnosticFrame | None = None) -> tuple[object | None, float]:
        """开局只推理选区、不可靠时等待；记牌阶段允许同帧整图复查。

        Args:
            image: 当前原图；None 沿用截图失败语义。
            detector: 当前单模型实例。
            regions: 当前阶段手动区域；None表示直接推理整图。
            guard: 切口单侧保护宽度比例。
            presence_region: 手动底牌区域；记牌中该区无牌时可复查同一原图。
            scales: 初值或当前单牌测量权重；None保留旧统一缩放方图接口。
            canvas_size: 加权阶段拼图的目标宽高。
            region_names: 启用真实单牌自适应测量的当前区域名。
            allow_full_fallback: 开局二区为False，禁止因无底牌而推理整张原图。
            full_label: 主动选择整图时显示的路线名称。
            trace: 可选的单帧证据接收器；关闭时没有图像复制。
        Returns:
            原图坐标的模型结果、包含可能补查的模型调用总毫秒。
        Raises:
            Exception: 整图推理失败时传播真实故障，不能伪装为成功。
        """
        start = perf_counter()
        self.timings = {"prepare_ms": 0., "restore_ms": 0., "model_calls": 0.}
        self.last_input = None
        self.last_input_plan = None
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
                self.last_input_plan = plan
                raw, elapsed = self._model_call(detector, canvas, True, trace, plan)
                call_pending = False
                inference_ms += elapsed
                restore_start = perf_counter()
                cards, reason = plan.restore(read_detections(raw))
                if not reason and cards and presence_region is not None and allow_full_fallback:
                    a, b, c, d = presence_region
                    width, height = image.size
                    if not any(a <= (card.bbox[0]+card.bbox[2])/(2*width) <= c
                               and b <= (card.bbox[1]+card.bbox[3])/(2*height) <= d for card in cards):
                        reason = "拼图底牌区无牌，复查原图"
                if (not reason and raw is not None and getattr(raw, 'boxes', None) is not None
                        and (cards or not allow_full_fallback)):
                    result = detector.restore_result(raw, image, cards)
                    if region_names is not None:
                        self.scale_profile.observe(cards, image.size, region_names, regions)
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
            if not allow_full_fallback:
                self.scale_profile.interrupt()
                self.route = f"开局两区拼图：{reason}，等待下一帧"
                return None, inference_ms
        if self.timings["prepare_ms"] == 0.:
            self.timings["prepare_ms"] = (perf_counter()-start)*1000
        self.last_input = image
        self.last_input_plan = None
        result, elapsed = self._model_call(detector, image, False, trace)
        inference_ms += elapsed
        self.timings["model_calls"] += 1
        if (result is not None and getattr(result, 'boxes', None) is not None
                and image is not None and regions is not None and region_names is not None):
            self.scale_profile.observe(read_detections(result), image.size, region_names, regions)
        elif region_names is not None:
            self.scale_profile.interrupt()
        self.route = f"整图：{reason}" if reason else (full_label if regions is None else "截图不可用")
        return result, inference_ms
