"""游戏控制器：编排截图、识别、几何分区与斗地主记牌；不写配置文件。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, cast
import time
import traceback

from core.diagnostics import DiagnosticRecorder, DiagnosticFrame, boxes_json
from core.diagnostic_state import tracker_snapshot, decision_reasons

import config.settings as settings
from config.packing_profile import PACKED_INPUT_SIZE, stage_selection
from core.base_tracker import BaseCardTracker
from core.auto_layout import AutoLayoutAnalyzer, read_yolo_boxes
from core.games.doudizhu import BOTTOM_START_FRAMES, DoudizhuTracker
from core.packed_pipeline import PackedPipeline

if TYPE_CHECKING:
    from core.yolo_inferencer import YoloInferencer
from core.layout_analyzer import LayoutAnalyzer
from core.games import create_tracker
from core.screen_capture import ScreenCapture


class GameController:
    """游戏控制器，编排截图→识别→状态更新，并格式化输出。

    运行在推理子进程中，通过组合模式持有四个核心组件：
        - ScreenCapture：负责截图
        - YoloInferencer：负责纯视觉推理
        - LayoutAnalyzer：负责几何空间划分
        - Tracker 子类：负责游戏状态机逻辑

    Attributes:
        game_name: 当前游戏名称。
        layout_name: 当前布局名称。
        screen_capture: 窗口截图器实例。
        yolo_inferencer: YOLO 推理器实例。
        layout_analyzer: 手动空间分析或自动牌组归属实例。
        tracker: 当前 Tracker 子类实例。
        last_screenshot_jpeg: 预览启用时最近一帧的 JPEG 截图。
        last_screenshot_regions: 为旧预览协议保留的空区域字典。
        packed_pipeline: 同步单帧拼图执行器，处理切口保护和整图回退。
        last_performance: 当前子进程阶段耗时快照，经过回执传回 UI。
    """

    def __init__(self, layout_name: str | None = None) -> None:
        """初始化游戏控制器，创建截图器、检测器、分析器和 Tracker。

        Args:
            layout_name: 布局名称。为 None 时自动使用第一个可用配置。
        """
        self.game_name: str = settings.GAME_NAME
        
        if layout_name is None or layout_name not in settings.WINDOW_LAYOUTS:
            available_layouts = list(settings.WINDOW_LAYOUTS.keys())
            if available_layouts:
                layout_name = available_layouts[0]
            else:
                raise ValueError("WINDOW_LAYOUTS 字典为空，没有可用的配置")
        
        self.layout_name: str = layout_name
        self.layout_config: settings.LayoutConfig = settings.validate_layout(settings.WINDOW_LAYOUTS[self.layout_name])
        
        self.yolo_inferencer: YoloInferencer = self._create_inferencer()
        self.layout_analyzer: LayoutAnalyzer | AutoLayoutAnalyzer = self._create_analyzer(self.layout_config)
        window_title: str = self.layout_config["window_title"]
        self.screen_capture: ScreenCapture = ScreenCapture(window_title)
        self.tracker: BaseCardTracker = create_tracker(self.game_name, layout_name)
        self.last_screenshot_jpeg: bytes | None = None
        self.last_screenshot_regions: dict[str, tuple[float, float, float, float]] = {}
        self.packed_pipeline = PackedPipeline()
        self.last_performance: dict[str, float] = {}
        self.diagnostics: DiagnosticRecorder | None = None
        self.request_context = (0, 0)
        self._diagnostic_round = 0

    def attach_diagnostics(self, recorder: DiagnosticRecorder) -> None:
        """由进程入口注入诊断服务，独立控制器测试不向真实目录写日志。

        Args:
            recorder: 当前推理进程唯一的诊断服务。
        """
        self.diagnostics = recorder
        recorder.set_disk_limit(settings.diagnostic_disk_bytes())
        recorder.set_enabled(settings.FULL_DIAGNOSTICS)

    def _sync_diagnostic_round(self, before_state: int) -> None:
        """在业务更新后记录局号，异常回执也不能丢失已经发生的开局边界。

        Args:
            before_state: 本帧更新前的Tracker状态。
        """
        if self.tracker.state != settings.WAIT_BEGIN and (before_state == settings.WAIT_BEGIN
                or getattr(self, '_diagnostic_round', 0) == 0):
            self._diagnostic_round = getattr(self, '_diagnostic_round', 0)+1
            recorder = getattr(self, 'diagnostics', None)
            if recorder is not None:
                recorder.note_round(self._diagnostic_round, before_state == settings.WAIT_BEGIN)

    def _diagnostic_context(self) -> dict[str, object]:
        """冻结当前选中布局和参数，不复制整个用户配置。

        Returns:
            请求标识、模型与布局的可序列化上下文。
        """
        model = getattr(self.yolo_inferencer, 'model', None)
        artifact = getattr(model, 'model', None)
        if not isinstance(artifact, (str, Path)):
            artifact = getattr(model, 'ckpt_path', None)
        model_input = getattr(self.yolo_inferencer, 'input_description', None)
        return {'request_id': self.request_context[0], 'generation': self.request_context[1],
                'game': self.game_name, 'layout_name': self.layout_name, 'layout': deepcopy(self.layout_config),
                'weight_path': settings.YOLO_MODEL_PATH, 'model': settings.YOLO_MODEL_NAME,
                'artifact_path': str(artifact) if isinstance(artifact, (str, Path)) else None,
                'model_input': model_input if isinstance(model_input, dict) else None,
                'device': settings.DEVICE_CHOICE, 'frame_length': settings.FRAME_LENGTH,
                'reset_time': settings.RESET_TIME, 'confidence': settings.YOLO_CONFIDENCE_THRESHOLD,
                'iou': settings.YOLO_IOU_THRESHOLD, 'packed_inference': settings.PACKED_INFERENCE,
                'class_mapping': dict(settings.YOLO_TO_CARD_MAPPING)}

    def _model_canvas_size(self) -> tuple[int, int]:
        """读取所选模型的画布宽高，兼容旧检测器和测试替身。

        Returns:
            配置指定的实际像素宽高；旧接口回退到历史画布。
        """
        size = getattr(self.yolo_inferencer, 'input_canvas_size', None)
        if (isinstance(size, tuple) and len(size) == 2
                and all(type(value) is int and value > 0 for value in size)):
            return size[0], size[1]
        return PACKED_INPUT_SIZE

    def detect(self) -> tuple[dict[str, int], dict[str, list[list[str]]], float]:
        """执行检测并只读记录证据；诊断错误不能改变记牌结果。

        Returns:
            剩余牌、历史与模型调用毫秒。
        Raises:
            Exception: 原始截图、模型或业务错误仍按原协议回传。
        """
        controller_started = time.perf_counter()
        recorder = getattr(self, 'diagnostics', None)
        trace = recorder.begin_frame({}) if recorder else None
        started = time.perf_counter()
        before_light = (self.tracker.state, dict(self.tracker.remain_cards),
                        {key: len(value) for key, value in self.tracker.show_cards.items()}) if recorder else None
        if trace:
            try:
                trace.payload['context'] = self._diagnostic_context()
                trace.payload['before'] = tracker_snapshot(self.tracker)
            except Exception as exc:
                trace.owner.pause('state_snapshot_failed: '+str(exc), cast(int, trace.payload['frame_id']))
                trace.cancel()
        preparation_ms = (time.perf_counter()-started)*1000
        try:
            result = self._detect_frame(trace)
        except Exception:
            error = traceback.format_exc()
            if recorder:
                if before_light is not None:
                    self._sync_diagnostic_round(before_light[0])
                recorder.daily.write('detect_failed', frame_id=recorder.frame_id, error=error)
            if trace:
                try:
                    trace.finish({'error': error, 'after': tracker_snapshot(self.tracker),
                                  'round_id': getattr(self, '_diagnostic_round', 0)})
                except Exception as exc:
                    trace.owner.pause('state_snapshot_failed: '+str(exc), cast(int, trace.payload['frame_id']))
                    trace.cancel()
            raise
        if recorder:
            after_light = (self.tracker.state, dict(self.tracker.remain_cards),
                           {key: len(value) for key, value in self.tracker.show_cards.items()})
            if before_light is not None:
                self._sync_diagnostic_round(before_light[0])
            if before_light != after_light:
                recorder.daily.write('state_changed', frame_id=recorder.frame_id, state=self.tracker.state,
                                     remaining=self.tracker.remain_cards, played=self.tracker.show_cards,
                                     initial_hand=list(getattr(self.tracker, '_initial_hand', [])))
        if trace and not trace.cancelled:
            started = time.perf_counter()
            try:
                after = tracker_snapshot(self.tracker)
                before = cast(dict[str, object], trace.payload['before'])
                reasons = decision_reasons(before, after, cast(dict[str, list[str]], trace.payload['frame_data']),
                                           cast(list[str], trace.payload['known_regions']),
                                           bool(trace.payload['valid_frame']), settings.FRAME_LENGTH)
                trace.finish({'after': after, 'round_id': getattr(self, '_diagnostic_round', 0), 'reasons': reasons, 'performance': dict(self.last_performance), 'error': ''})
            except Exception as exc:
                trace.owner.pause('state_snapshot_failed: '+str(exc), cast(int, trace.payload['frame_id']))
                trace.cancel()
            self.last_performance['diagnostic_snapshot_ms'] = preparation_ms+trace.capture_ms+(time.perf_counter()-started)*1000
        self.last_performance["controller_ms"] = (time.perf_counter()-controller_started)*1000
        return result

    def _detect_frame(self, trace: DiagnosticFrame | None = None) -> tuple[dict[str, int], dict[str, list[list[str]]], float]:
        """执行一次完整检测：截图→识别→解析排版→映射→状态更新→格式化输出。

        Args:
            trace: 可选的诊断帧，关闭时为None，不复制诊断图片。

        Returns:
            tuple: 包含三个元素：
                - remain_cards (dict[str, int]): 各牌点剩余数量。
                - zone_cards (dict[str, list[list[str]]]): 各出牌区域的出牌记录，
                  key 为 played_zones 中的 key，value 为出牌记录。
                - yolo_ms (float): YOLO 推理耗时（毫秒）。

        Raises:
            ValueError: 自动布局使用了非斗地主 Tracker 或开局数据不合法。
            RuntimeError: 截图或推理失败。
        """
        started = time.perf_counter()
        img = self.screen_capture.capture_window()
        captured = time.perf_counter()
        if trace:
            trace.image("original", img)
        self.last_screenshot_jpeg = None
        self.last_screenshot_regions = {}
        raw_result: object | None
        yolo_ms: float
        # 自动布局整图；手动布局由已确认的检测方式偏好选择。
        names: list[str] = []
        regions = None
        scales = None
        if not isinstance(self.layout_analyzer, AutoLayoutAnalyzer):
            names, selected = stage_selection(self.layout_config["layout"],
                                                self.tracker.state == settings.STARTED_RECORD_CARD)
            if settings.PACKED_INFERENCE:
                regions = selected
                if img is not None:
                    scales = self.packed_pipeline.scale_weights(img.size, self.layout_config['layout'], names)
        bottom_region = (None if regions is None else
                         cast(tuple[float, float, float, float], tuple(self.layout_config["layout"]["landlord_cards"])))
        raw_result, yolo_ms = self.packed_pipeline.detect(img, self.yolo_inferencer, regions,
                                                       presence_region=bottom_region, scales=scales,
                                                       canvas_size=self._model_canvas_size(), region_names=names or None,
                                                       allow_full_fallback=self.tracker.state == settings.STARTED_RECORD_CARD,
                                                       full_label='自动布局整图' if isinstance(self.layout_analyzer, AutoLayoutAnalyzer) else '手动布局原图', trace=trace)
        detected = time.perf_counter()
        preview_started = time.perf_counter()
        if settings.SHOW_CURRENT_SCREENSHOT and self.packed_pipeline.last_input is not None:
            image_buffer = BytesIO()
            self.packed_pipeline.last_input.convert("RGB").save(image_buffer, format="JPEG", quality=75)
            self.last_screenshot_jpeg = image_buffer.getvalue()
        preview_ms = (time.perf_counter()-preview_started)*1000
        valid_frame = img is not None and raw_result is not None and getattr(raw_result, "boxes", None) is not None
        size = img.size if img is not None and valid_frame else (0, 0)
        cards = read_yolo_boxes(raw_result, size)
        
        # 几何布局分析（把 raw box 切分到不同区域并做二维排序）
        # 输入：YOLO 原始检测结果 + 图片尺寸
        # 输出：{区域名: [{"bbox": (x1,y1,x2,y2), "name": yolo_label}, ...]}
        known_regions: set[str] | None = None
        if isinstance(self.layout_analyzer, AutoLayoutAnalyzer):
            if not isinstance(self.tracker, DoudizhuTracker):
                raise ValueError("自动布局仅支持斗地主")
            observation = self.layout_analyzer.observe(cards, size, settings.FRAME_LENGTH)
            region_boxes = observation.as_boxes()
            known_regions = set(region_boxes)
        else:
            region_boxes = self.layout_analyzer.parse_and_sort(raw_result, img.size if img else (0, 0), names)
            known_regions = set(names) if valid_frame else set()
        # 业务逻辑映射（将 YOLO 标签转换为游戏牌点名称）
        # 输入：{区域名: [{"bbox": ..., "name": "three"}, ...]}  （YOLO 原始标签）
        # 输出：{区域名: ["3", "A", "小", ...]}  （通过 YOLO_TO_CARD_MAPPING 转换后的业务牌点）
        frame_data: dict[str, list[str]] = self.tracker.translate_boxes_to_cards(region_boxes)

        remain_cards: dict[str, int]
        show_cards: dict[str, list[list[str]]]
        remain_cards, show_cards, yolo_ms = self.tracker.get_cards_number(frame_data, yolo_ms, known_regions)
        if (isinstance(self.layout_analyzer, AutoLayoutAnalyzer) and isinstance(self.tracker, DoudizhuTracker)
                and self.tracker.awaiting_next_round and self.layout_analyzer.ready):
            self.layout_analyzer.wait_for_next_round(self.tracker.round_end_reason)
            self.packed_pipeline.reset()
        zone_cards: dict[str, list[list[str]]] = self._format_zone_cards(show_cards)
        finished = time.perf_counter()
        self.last_performance = dict(self.packed_pipeline.timings)
        self.last_performance.update(capture_ms=(captured-started)*1000, model_ms=yolo_ms,
                                     preview_ms=preview_ms,
                                     layout_save_ms=(finished-detected)*1000-preview_ms, pipeline_ms=(finished-started)*1000)
        if settings.DEBUG_MODE:
            print(f"[V4.3] {self.packed_pipeline.route} {self.last_performance}")
        if trace and not trace.cancelled:
            try:
                trace.payload.update(region_boxes=deepcopy(region_boxes), selected_regions=list(names),
                                     source_size=list(img.size) if img else None,
                                     frame_data=deepcopy(frame_data), known_regions=sorted(known_regions or []),
                                     valid_frame=valid_frame, final_boxes=boxes_json(raw_result),
                                     route=self.packed_pipeline.route, layout_status=self.layout_status)
            except Exception as exc:
                trace.owner.pause('result_snapshot_failed: '+str(exc), cast(int, trace.payload['frame_id']))
                trace.cancel()
        return remain_cards, zone_cards, yolo_ms

    def reset(self) -> None:
        """重置记牌器和自动布局参照到初始状态。"""
        self.tracker.reset()
        self.packed_pipeline = PackedPipeline()
        if isinstance(self.layout_analyzer, AutoLayoutAnalyzer):
            self.layout_analyzer.reset()

    @property
    def layout_status(self) -> str:
        """返回当前布局、真实识别路线及等待状态。"""
        capture = getattr(self.screen_capture, 'last_capture_method', '')
        suffix = ' · 桌面回退（需无遮挡）' if capture == 'desktop' else ''
        if isinstance(self.layout_analyzer, AutoLayoutAnalyzer):
            return self.layout_analyzer.last_observation.message + " · " + self.packed_pipeline.route + suffix
        state = "记牌中"
        if isinstance(self.tracker, DoudizhuTracker) and self.tracker.state != settings.STARTED_RECORD_CARD:
            state = "保留上一局，等待底牌出现" if self.tracker.awaiting_next_round else "等待底牌出现"
            if self.tracker.state == settings.HAS_STARTED:
                state = "已开局，等待非空手牌稳定"
            elif self.tracker._bottom_start_frames:
                state = f"底牌确认 {self.tracker._bottom_start_frames}/{BOTTOM_START_FRAMES}"
        return f"手动布局 · {self.packed_pipeline.route} · {state}{suffix}"

    @staticmethod
    def _create_analyzer(config: settings.LayoutConfig) -> LayoutAnalyzer | AutoLayoutAnalyzer:
        """根据布局模式构建分析器，自动模式不使用矩形配置。

        Args:
            config: 已验证的布局配置。

        Returns:
            自动或手动分析器。
        """
        if config.get("mode") == "auto":
            limits = {label: settings.TOTAL_CARDS[card] for label, card in settings.YOLO_TO_CARD_MAPPING.items()}
            return AutoLayoutAnalyzer(limits)
        return LayoutAnalyzer(config)

    def _create_inferencer(self) -> YoloInferencer:
        """延迟加载重型依赖并创建推理器。

        Returns:
            按当前子进程配置创建的推理器。

        Raises:
            ImportError: 缺少推理依赖。
            OSError: 权重不可读取。
            RuntimeError: 模型初始化失败。
        """
        from core.yolo_inferencer import YoloInferencer

        return YoloInferencer()

    def switch_layout(self, layout_name: str, layout_config: object) -> None:
        """使用命令携带的完整布局，所有组件创建成功后一起替换。

        Args:
            layout_name: 新布局名。
            layout_config: 包含窗口标题和坐标的完整布局。

        Raises:
            TypeError: 参数类型错误。
            ValueError: 名称或布局无效。
            RuntimeError: 新组件构造失败，原组件保留。
        """
        if not isinstance(layout_name, str):
            raise TypeError("布局名称必须是字符串")
        if not layout_name.strip():
            raise ValueError("布局名称不能为空")
        config = settings.validate_layout(layout_config)
        analyzer = self._create_analyzer(config)
        capture = ScreenCapture(config["window_title"])
        tracker = create_tracker(self.game_name, layout_name)
        self.layout_name, self.layout_config = layout_name, config
        self.layout_analyzer, self.screen_capture, self.tracker = analyzer, capture, tracker
        self.packed_pipeline = PackedPipeline()
        settings.WINDOW_LAYOUTS[layout_name] = deepcopy(config)
        settings.CURRENT_LAYOUT = layout_name

    def switch_model(self, model_name: str) -> None:
        """模型和 Tracker 都创建成功后才发布新状态。

        Args:
            model_name: 权重子目录名。

        Raises:
            TypeError: 名称类型错误。
            ValueError: 名称无效。
            Exception: 模型或 Tracker 创建失败，恢复原配置和组件。
        """
        previous = settings.runtime_snapshot()
        try:
            settings.apply_runtime_settings({"YOLO_MODEL_NAME": model_name})
            inferencer = self._create_inferencer()
            tracker = create_tracker(self.game_name, self.layout_name)
        except Exception:
            settings.apply_runtime_settings(previous)
            raise
        self.yolo_inferencer, self.tracker = inferencer, tracker
        self.packed_pipeline = PackedPipeline()
        if isinstance(self.layout_analyzer, AutoLayoutAnalyzer):
            self.layout_analyzer.reset()

    def switch_device(self, device_name: str) -> None:
        """设备切换失败时保留原推理器及配置。

        Args:
            device_name: cpu 或 cuda。

        Raises:
            TypeError: 设备名类型错误。
            ValueError: 不支持的设备。
            Exception: 推理器构造失败。
        """
        previous = settings.DEVICE_CHOICE
        try:
            settings.apply_runtime_settings({"DEVICE_CHOICE": device_name})
            inferencer = self._create_inferencer()
        except Exception:
            settings.DEVICE_CHOICE = previous
            raise
        self.yolo_inferencer = inferencer
        self.packed_pipeline = PackedPipeline()

    def update_settings(self, updates: Mapping[str, object]) -> None:
        """应用允许热更新的参数，禁止绕过组件切换流程。

        Args:
            updates: 可热更新的运行时属性。

        Raises:
            TypeError: 参数值类型错误。
            ValueError: 属性不可热更新或值无效。
        """
        allowed = {
            "DEBUG_MODE", "SHOW_CURRENT_SCREENSHOT", "RESET_TIME", "FRAME_LENGTH", "PACKED_INFERENCE",
            "YOLO_CONFIDENCE_THRESHOLD", "YOLO_IOU_THRESHOLD", "FULL_DIAGNOSTICS", "DIAGNOSTIC_DISK_GIB",
        }
        if not set(updates).issubset(allowed):
            raise ValueError("存在不能直接热更新的属性")
        previous = settings.runtime_snapshot()
        settings.apply_runtime_settings(updates)
        if 'FULL_DIAGNOSTICS' in updates or 'DIAGNOSTIC_DISK_GIB' in updates:
            recorder = getattr(self, 'diagnostics', None)
            old_quota = recorder.limits.disk if recorder is not None else None
            try:
                if recorder is None:
                    raise RuntimeError('诊断服务尚未初始化')
                if 'DIAGNOSTIC_DISK_GIB' in updates:
                    recorder.set_disk_limit(settings.diagnostic_disk_bytes())
                if 'FULL_DIAGNOSTICS' in updates:
                    recorder.set_enabled(settings.FULL_DIAGNOSTICS)
            except Exception:
                settings.apply_runtime_settings(previous)
                if recorder is not None and old_quota is not None:
                    recorder.set_disk_limit(old_quota)
                raise
        if "PACKED_INFERENCE" in updates:
            self.packed_pipeline = PackedPipeline()
        self.yolo_inferencer.yolo_conf = settings.YOLO_CONFIDENCE_THRESHOLD
        self.yolo_inferencer.yolo_iou = settings.YOLO_IOU_THRESHOLD

    def execute_command(self, name: str, payload: object) -> None:
        """分派控制命令，统一由进程入口捕获错误并回执。

        Args:
            name: 命令名。
            payload: 命令参数。

        Raises:
            TypeError: 参数类型不符。
            ValueError: 命令未知或参数无效。
            Exception: 对应控制操作执行失败。
        """
        if name == "reset":
            self.reset()
        elif name == "touch_time":
            self.touch_time()
        elif name == "switch_layout":
            if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
                raise TypeError("布局命令必须包含名称与完整配置")
            self.switch_layout(payload["name"], payload.get("config"))
        elif name == "update_settings":
            if not isinstance(payload, Mapping):
                raise TypeError("设置参数必须是字典")
            self.update_settings(cast(Mapping[str, object], payload))
        elif name in ("switch_model", "switch_device"):
            if not isinstance(payload, str):
                raise TypeError("切换命令参数必须是字符串")
            handlers = {
                "switch_model": self.switch_model,
                "switch_device": self.switch_device,
            }
            handlers[name](payload)
        else:
            raise ValueError(f"未知命令: {name}")

    def touch_time(self) -> None:
        """刷新时间并打断无牌连续性，暂停时长不能被当作持续空白。"""
        self.tracker.no_target_time = time.time()
        if isinstance(self.tracker, DoudizhuTracker):
            self.tracker.interrupt_observation()
        if isinstance(self.layout_analyzer, AutoLayoutAnalyzer):
            self.layout_analyzer.interrupt_observation()

    def _format_zone_cards(self, show_cards: dict[str, list[list[str]]]) -> dict[str, list[list[str]]]:
        """将 Tracker 的 show_cards 格式化为 zone_cards。

        只保留 played_zones 中定义的区域，过滤掉非出牌区域（如手牌、底牌）。

        Args:
            show_cards: Tracker 原始出牌记录，key 为 played_zones 的 key。

        Returns:
            dict[str, list[list[str]]]: 格式化后的 zone_cards。
        """
        zone_keys = {z["key"] for z in settings.PLAYED_ZONES}
        return {k: v for k, v in show_cards.items() if k in zone_keys}
