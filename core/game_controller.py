"""游戏控制器：编排截图、识别、几何分区与斗地主记牌；不写配置文件。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from io import BytesIO
from typing import TYPE_CHECKING, cast
import time

import config.settings as settings
from config.packing_profile import PACKED_INPUT_SIZE, stage_regions
from core.base_tracker import BaseCardTracker
from core.auto_layout import AutoLayoutAnalyzer, read_yolo_boxes
from core.games.doudizhu import DoudizhuTracker
from core.packed_pipeline import PackedPipeline

if TYPE_CHECKING:
    from core.yolo_inferencer import YoloInferencer
from core.layout_analyzer import LayoutAnalyzer
from core.games import create_tracker
from core.screen_capture import ScreenCapture
from core.image_saver import ImageSaver


class GameController:
    """游戏控制器，编排截图→识别→状态更新，并格式化输出。

    运行在推理子进程中，通过组合模式持有三个核心组件：
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
        debug_manager: 调试图片管理器实例（由 GameController 统一管理）。
        last_screenshot_jpeg: 预览启用时最近一帧的 JPEG 截图。
        last_screenshot_regions: 自动布局当前确认的牌区整体边界。
        packed_pipeline: 同步单帧拼图执行器，处理切口保护和整图回退。
        last_performance: 当前子进程阶段耗时快照，经过回执传回 UI。
    """

    def __init__(self, game_name: str, layout_name: str | None = None) -> None:
        """初始化游戏控制器，创建截图器、检测器、分析器和 Tracker。

        Args:
            game_name: 游戏名称（如 ``"doudizhu"``）。
            layout_name: 布局名称。为 None 时自动使用第一个可用配置。
        """
        self.game_name: str = game_name
        
        if layout_name is None or layout_name not in settings.WINDOW_LAYOUTS:
            available_layouts = list(settings.WINDOW_LAYOUTS.keys())
            if available_layouts:
                layout_name = available_layouts[0]
            else:
                raise ValueError("WINDOW_LAYOUTS 字典为空，没有可用的配置")
        
        self.layout_name: str = layout_name
        self.layout_config: settings.LayoutConfig = settings.validate_layout(settings.WINDOW_LAYOUTS[self.layout_name])
        
        # 创建调试图片保存器（由 GameController 统一管理）
        self.debug_manager: ImageSaver = ImageSaver(settings.BASE_DIR)
        # 根据当前运行时设置初始化保存器（允许热切换）
        try:
            self.debug_manager.bootstrap(settings.SAVE_DEBUG_IMAGES)
        except Exception:
            pass
        
        self.yolo_inferencer: YoloInferencer = self._create_inferencer()
        self.layout_analyzer: LayoutAnalyzer | AutoLayoutAnalyzer = self._create_analyzer(self.layout_config)
        window_title: str = self.layout_config["window_title"]
        self.screen_capture: ScreenCapture = ScreenCapture(window_title)
        self.tracker: BaseCardTracker = create_tracker(game_name, layout_name, self.debug_manager)
        self.last_screenshot_jpeg: bytes | None = None
        self.last_screenshot_regions: dict[str, tuple[float, float, float, float]] = {}
        self.packed_pipeline = PackedPipeline()
        self.last_performance: dict[str, float] = {}

    def detect(self) -> tuple[dict[str, int], dict[str, list[list[str]]], float]:
        """执行一次完整检测：截图→识别→解析排版→映射→状态更新→格式化输出。

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
        self.last_screenshot_jpeg = None
        self.last_screenshot_regions = {}
        raw_result: object | None
        yolo_ms: float
        # 路线由布局模式唯一决定；旧 packed_inference 偏好不再覆盖用户的布局选择。
        names: list[str] = []
        regions = None
        scales = None
        if not isinstance(self.layout_analyzer, AutoLayoutAnalyzer):
            names, regions, scales = stage_regions(self.layout_config["layout"],
                                                  self.tracker.state == settings.STARTED_RECORD_CARD)
        bottom_region = (None if regions is None else
                         cast(tuple[float, float, float, float], tuple(self.layout_config["layout"]["landlord_cards"])))
        raw_result, yolo_ms = self.packed_pipeline.detect(img, self.yolo_inferencer, regions,
                                                       presence_region=bottom_region, scales=scales,
                                                       canvas_size=PACKED_INPUT_SIZE)
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
        # 开局回调先建立新局目录，首张有效开局图不再写入上一局。
        self.yolo_inferencer.save_frame(img, raw_result)
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
        if isinstance(self.layout_analyzer, AutoLayoutAnalyzer):
            return self.layout_analyzer.last_observation.message + " · " + self.packed_pipeline.route
        state = "记牌中"
        if isinstance(self.tracker, DoudizhuTracker) and self.tracker.state != settings.STARTED_RECORD_CARD:
            state = "保留上一局，等待底牌出现" if self.tracker.awaiting_next_round else "等待底牌出现"
            if self.tracker.state == settings.HAS_STARTED:
                state = "已开局，等待非空手牌稳定"
        return f"手动布局 · {self.packed_pipeline.route} · {state}"

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

        return YoloInferencer(self.debug_manager)

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
        tracker = create_tracker(self.game_name, layout_name, self.debug_manager)
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
            tracker = create_tracker(self.game_name, self.layout_name, self.debug_manager)
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

    def switch_game(self, game_name: str) -> None:
        """在子进程内存中切换已注册游戏。

        Args:
            game_name: 已注册游戏名称。

        Raises:
            TypeError: 名称类型错误。
            ValueError: 游戏未注册。
            Exception: Tracker 构造失败，原游戏配置恢复。
        """
        previous = self.game_name
        try:
            settings.apply_game_choice(game_name)
            tracker = create_tracker(game_name, self.layout_name, self.debug_manager)
        except Exception:
            settings.apply_game_choice(previous)
            raise
        self.game_name, self.tracker = game_name, tracker
        self.packed_pipeline = PackedPipeline()
        if isinstance(self.layout_analyzer, AutoLayoutAnalyzer):
            self.layout_analyzer.reset()

    def update_settings(self, updates: Mapping[str, object]) -> None:
        """应用允许热更新的参数，禁止绕过组件切换流程。

        Args:
            updates: 可热更新的运行时属性。

        Raises:
            TypeError: 参数值类型错误。
            ValueError: 属性不可热更新或值无效。
        """
        allowed = {
            "DEBUG_MODE", "SAVE_DEBUG_IMAGES", "SHOW_CURRENT_SCREENSHOT", "RESET_TIME", "FRAME_LENGTH", "PACKED_INFERENCE",
            "YOLO_CONFIDENCE_THRESHOLD", "YOLO_IOU_THRESHOLD",
        }
        if not set(updates).issubset(allowed):
            raise ValueError("存在不能直接热更新的属性")
        settings.apply_runtime_settings(updates)
        if "PACKED_INFERENCE" in updates:
            self.packed_pipeline = PackedPipeline()
        self.yolo_inferencer.yolo_conf = settings.YOLO_CONFIDENCE_THRESHOLD
        self.yolo_inferencer.yolo_iou = settings.YOLO_IOU_THRESHOLD
        if "SAVE_DEBUG_IMAGES" in updates:
            self.debug_manager.set_enabled(settings.SAVE_DEBUG_IMAGES)

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
        elif name in ("switch_model", "switch_device", "switch_game"):
            if not isinstance(payload, str):
                raise TypeError("切换命令参数必须是字符串")
            handlers = {
                "switch_model": self.switch_model,
                "switch_device": self.switch_device,
                "switch_game": self.switch_game,
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
