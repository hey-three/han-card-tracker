"""YOLO 推理引擎模块。

仅负责加载模型与执行纯视觉推理，返回原始 YOLO 结果对象和耗时。
"""

import os
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Tuple, cast

import torch
from PIL import Image
from ultralytics import YOLO

import config.settings as settings
from core.image_saver import ImageSaver
from core.frame_packing import Detection, PACKED_CANVAS_SIZE

if TYPE_CHECKING:
    from ultralytics.engine.results import Results


class YoloInferencer:
    """基于 YOLO 的纯视觉推理器。

    不包含业务坐标的计算和状态机的处理，只接受单帧图片返回原始边框和执行时间。
    
    Attributes:
        yolo_iou: YOLO IOU 阈值。
        yolo_conf: YOLO 置信度阈值。
        weight_path: 模型权重路径。
        model: 加载的 YOLO 模型。
        device: 推理设备（cuda/cpu）。
        debug_manager: 调试图片保存器（由 GameController 传入，可选）。
        _full_input_size: 首次完整推理的输入配置，避免拼图参数污染整图回退。
    """

    def __init__(self, debug_manager: ImageSaver | None = None) -> None:
        """初始化 YOLO 推理器。

        Args:
            debug_manager: 调试图片保存器实例（由 GameController 统一管理并传入，可选）。
        """
        self.yolo_iou: float = settings.YOLO_IOU_THRESHOLD
        self.yolo_conf: float = settings.YOLO_CONFIDENCE_THRESHOLD
        self.weight_path: str = settings.YOLO_MODEL_PATH

        self.model, self.device = self.__load_model()
        self.debug_manager: ImageSaver | None = debug_manager
        self._full_input_size: int | list[int] | None = None

    def __load_model(self) -> Tuple[YOLO, str]:
        """按现有设备设置加载本地模型。

        Returns:
            模型与实际设备名称。
        Raises:
            FileNotFoundError: 本地权重不存在。
            Exception: 最终回退的 PyTorch 模型也无法加载。
        """
        if not os.path.exists(self.weight_path):
            raise FileNotFoundError(f"未找到本地模型文件: {self.weight_path}。请确认打包时包含 yolo/weights/best.pt。")

        device_choice = settings.DEVICE_CHOICE
        print(f"[YoloInferencer] 当前设备选择: {device_choice}")

        use_gpu = device_choice == "cuda" and torch.cuda.is_available()
        if device_choice == "cuda" and not use_gpu:
            print("[YoloInferencer] 警告: 用户选择了GPU，但CUDA不可用，回退到CPU")

        weights_dir = os.path.dirname(self.weight_path)

        if use_gpu:
            engine_path = os.path.join(weights_dir, "best.engine")
            model = self.__load_tensorrt(engine_path)
            if model is not None:
                return model, "cuda"
            print("[YoloInferencer] TensorRT 不可用，使用 PyTorch CUDA + FP16")
            model = YOLO(self.weight_path)
            model.to("cuda")
            return model, "cuda"
        else:
            onnx_path = os.path.join(weights_dir, "best.onnx")
            model = self.__load_onnx(onnx_path)
            if model is not None:
                return model, "cpu"
            print("[YoloInferencer] ONNX Runtime 不可用，使用 PyTorch CPU")
            model = YOLO(self.weight_path)
            model.to("cpu")
            return model, "cpu"

    def __load_tensorrt(self, engine_path: str) -> YOLO | None:
        """尝试现有 TensorRT 路线。

        Args:
            engine_path: 本地引擎路径。
        Returns:
            已加载的模型；依赖、导出或加载失败时为 None，由上层回退。
        """
        try:
            import tensorrt  # noqa: F401
        except ImportError:
            print("[YoloInferencer] 未安装 tensorrt，跳过 TensorRT 加速")
            return None

        if os.path.exists(engine_path):
            print(f"[YoloInferencer] 加载已有 TensorRT 引擎: {engine_path}")
            try:
                return YOLO(engine_path)
            except Exception as e:
                print(f"[YoloInferencer] 加载 TensorRT 引擎失败: {e}，将重新导出")

        print("[YoloInferencer] 正在导出 TensorRT 引擎（FP16），首次导出可能需要几分钟...")
        try:
            pt_model = YOLO(self.weight_path)
            pt_model.export(format="engine", half=True)
            print(f"[YoloInferencer] TensorRT 引擎导出完成: {engine_path}")
            return YOLO(engine_path)
        except Exception as e:
            print(f"[YoloInferencer] TensorRT 导出失败: {e}")
            return None

    def __load_onnx(self, onnx_path: str) -> YOLO | None:
        """尝试现有 ONNX 路线。

        Args:
            onnx_path: 本地导出模型路径。
        Returns:
            已加载的模型；依赖、导出或加载失败时为 None，由上层回退。
        """
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            print("[YoloInferencer] 未安装 onnxruntime，跳过 ONNX Runtime 加速")
            return None

        if os.path.exists(onnx_path):
            print(f"[YoloInferencer] 加载已有 ONNX 模型: {onnx_path}")
            try:
                return YOLO(onnx_path)
            except Exception as e:
                print(f"[YoloInferencer] 加载 ONNX 模型失败: {e}，将重新导出")

        print("[YoloInferencer] 正在导出 ONNX 模型，首次导出可能需要几十秒...")
        try:
            pt_model = YOLO(self.weight_path)
            pt_model.export(format="onnx")
            print(f"[YoloInferencer] ONNX 模型导出完成: {onnx_path}")
            return YOLO(onnx_path)
        except Exception as e:
            print(f"[YoloInferencer] ONNX 导出失败: {e}")
            return None

    def detect(self, img: Image.Image | None) -> Tuple[object, float]:
        """执行纯粹的 YOLO 推理。

        Returns:
            Tuple[object, float]: (YOLO 单帧 raw_result 或 None, 推理耗时毫秒)
        """
        if img is None:
            return None, 0.0

        return self._run(img)

    def detect_packed(self, img: Image.Image) -> tuple[object | None, float]:
        """对拼图尝试 640 输入，静态引擎沿用自己的固定尺寸。

        Args:
            img: 当前线程同步消费的 RGB 拼图画布。
        Returns:
            原始画布结果与模型调用耗时；不为静态模型自动重新导出。
        """
        backend = getattr(getattr(self.model, "predictor", None), "model", None)
        flexible = backend is not None and (getattr(backend, "pt", False) or getattr(backend, "dynamic", False))
        return self._run(img, PACKED_CANVAS_SIZE[0] if flexible else None, packed=True)

    def _run(self, img: Image.Image, input_size: int | None = None, *, packed: bool = False) -> tuple[object | None, float]:
        """运行单模型并同步 GPU 计时。

        Args:
            img: 当前输入图。
            input_size: 可变模型的候选尺寸；None 恢复整图原始尺寸。
            packed: 是否拼图调用；拼图使用固定方形填充，整图恢复原矩形策略。

        Returns:
            单帧结果及整个 Ultralytics 调用毫秒，包含其内部预处理和后处理。
        Raises:
            Exception: 推理或设备同步失败时向控制器传播。
        """
        selected_size = input_size if input_size is not None else self._full_input_size
        if selected_size is None:
            configured = self.model.overrides.get("imgsz", 640)
            if not isinstance(configured, (int, list)):
                raise ValueError("模型输入尺寸元数据无效")
            selected_size = configured
        if self.device == "cuda":
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        results = self.model(
            img,
            conf=self.yolo_conf,
            iou=self.yolo_iou,
            device=self.device,
            verbose=False,
            half=(self.device == "cuda"),
            rect=not packed,
            imgsz=selected_size,
        )
        if self.device == "cuda":
            torch.cuda.synchronize()
        yolo_ms = (time.perf_counter() - t0) * 1000
        if not packed and self._full_input_size is None:
            predictor = self.model.predictor
            if predictor is None:
                raise RuntimeError("模型未建立推理器")
            size = predictor.args.imgsz
            self._full_input_size = size.copy() if isinstance(size, list) else size

        return (results[0] if results else None), yolo_ms

    @staticmethod
    def restore_result(raw: object, img: Image.Image, cards: Sequence[Detection]) -> object:
        """构造原图结果供布局解析和标注。

        Args:
            raw: 当前模型画布结果，保留类别元数据。
            img: 当前原图。
            cards: 已恢复并去重的原图像素检测框。

        Returns:
            原始截图尺寸的 Ultralytics Results；不同位置的同点数牌均保留。
        """
        import numpy as np
        from ultralytics.engine.results import Results

        metadata = cast("Results", raw)
        names = metadata.names
        indices = {name: index for index, name in names.items()}
        boxes = torch.tensor([(*c.bbox, c.confidence, indices[c.name]) for c in cards], dtype=torch.float32).reshape(-1, 6)
        return Results(np.asarray(img.convert("RGB"))[:, :, ::-1].copy(), path=metadata.path, names=names, boxes=boxes)

    def save_frame(self, img: Image.Image | None, raw: object | None) -> None:
        """仅保存最终原图和原图坐标标注，不把拼图误当作原始截图。

        Args:
            img: 当前原图，失败时为 None。
            raw: 整图或已还原到原图的最终结果。
        """

        if settings.SAVE_DEBUG_IMAGES and img is not None and raw is not None and self.debug_manager is not None:
            try:
                yolo_bgr = cast("Results", raw).plot()
                yolo_rgb = yolo_bgr[:, :, ::-1]
                yolo_image = Image.fromarray(yolo_rgb)
                self.debug_manager.save_frame(img, yolo_image)
            except Exception as exc:
                if settings.DEBUG_MODE:
                    print(f"[YoloInferencer] 保存标注帧失败: {exc}")
