"""YOLO 推理引擎模块。

仅负责加载模型与执行纯视觉推理，返回原始 YOLO 结果对象和耗时。
"""

import os
import re
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from collections.abc import Sequence
from typing import TYPE_CHECKING, Tuple, cast

import torch
from PIL import Image
from ultralytics import YOLO

import config.settings as settings
from core.frame_packing import Detection
from core.model_input import ModelInput, load_model_input, static_backend_shape
from core.model_export_cache import ModelExportCache
from config.packing_profile import PACKED_INPUT_SIZE

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
        input_spec: 权重旁的固定输入契约；缺文件时采用旧模型兼容行为。
        _full_input_size: 已确认输入配置，避免拼图参数污染整图回退。
    """

    def __init__(self) -> None:
        """根据模型和设备配置初始化纯视觉推理器。"""
        self.yolo_iou: float = settings.YOLO_IOU_THRESHOLD
        self.yolo_conf: float = settings.YOLO_CONFIDENCE_THRESHOLD
        self.weight_path: str = settings.YOLO_MODEL_PATH
        self.input_spec: ModelInput | None = load_model_input(Path(self.weight_path))
        if self.input_spec is None:
            print("[YoloInferencer] 缺少 model_config.yaml，使用旧模型兼容尺寸；新增模型请附带输入配置")

        self.model, self.device = self.__load_model()
        self._full_input_size: int | list[int] | tuple[int, int] | None = None

    @property
    def input_canvas_size(self) -> tuple[int, int]:
        """返回所选模型的实际拼图像素宽高；旧模型保留历史画布。"""
        spec = getattr(self, "input_spec", None)
        return spec.canvas_size if spec is not None else PACKED_INPUT_SIZE

    @property
    def input_description(self) -> dict[str, object]:
        """返回已加载的输入契约供日志和诊断使用，不逐帧读取配置。"""
        spec = getattr(self, "input_spec", None)
        if spec is None:
            return {"source": "legacy", "packing_canvas_wh": list(PACKED_INPUT_SIZE)}
        return {"source": str(spec.config_path), "canvas_wh": list(spec.canvas_size),
                "imgsz_hw": list(spec.imgsz), "resize": "letterbox", "rect": False}

    def _prepare_configured_model(self, model: YOLO, device: str) -> YOLO:
        """在接收首帧前加载后端并核对形状，不用整图或虚构截图校准。

        Args:
            model: 已选择的 PT 或静态导出模型。
            device: 实际推理设备。
        Returns:
            后端就绪且与旁置配置匹配的模型。
        Raises:
            ValueError: 实际静态绑定或模型步长与配置不兼容。
            Exception: 后端初始化失败。
        """
        spec = self.input_spec
        if spec is None:
            return model
        overrides = {**model.overrides, "imgsz": spec.imgsz, "rect": False,
                     "batch": 1, "device": device, "half": device == "cuda",
                     "task": "detect", "mode": "predict", "save": False, "verbose": False}
        predictor = model._smart_load("predictor")(overrides=overrides, _callbacks=model.callbacks)
        predictor.setup_model(model=model.model, verbose=False)
        backend = predictor.model
        if not (getattr(backend, "pt", False) or getattr(backend, "dynamic", False)):
            shape = static_backend_shape(backend)
            if shape != (1, 3, *spec.imgsz):
                raise ValueError(f"静态模型输入 {shape} 与 {spec.config_path} 的 (1,3,{spec.height},{spec.width}) 不一致")
        stride = getattr(backend, "stride", 32)
        if type(stride) is int and stride > 0 and any(value % stride for value in spec.imgsz):
            raise ValueError(f"{spec.config_path}: 输入尺寸必须与模型步长 {stride} 对齐")
        model.predictor = predictor
        print(f"[YoloInferencer] 固定输入 W×H={spec.width}×{spec.height}, rect=False, device={device}")
        return model

    def _load_configured_export(self, backend: str, device: str) -> YOLO:
        """加载原权重同目录的可信导出，失效时按明确高宽重新生成。

        Args:
            backend: onnx 或 engine。
            device: 实际推理设备。
        Returns:
            真实输入形状已核对的模型。
        Raises:
            RuntimeError: 缺少输入配置或导出未生成预期文件。
            Exception: 缓存、导出或加载失败，由设备选择层回退到 PT。
        """
        spec = self.input_spec
        if spec is None:
            raise RuntimeError("固定尺寸导出需要模型输入配置")
        environment: dict[str, str] = {"precision": "fp16" if device == "cuda" else "fp32"}
        packages = ("ultralytics", "torch", "onnx", "onnxslim", "tensorrt" if device == "cuda" else "onnxruntime")
        for package in packages:
            try:
                environment[package] = version(package)
            except PackageNotFoundError:
                environment[package] = "unknown"
        if device == "cuda":
            environment["gpu"] = torch.cuda.get_device_name()
            environment["cuda"] = str(torch.version.cuda)
        cache = ModelExportCache(Path(self.weight_path), spec, backend, environment)
        if cache.ready():
            try:
                return self._prepare_configured_model(YOLO(str(cache.artifact), task="detect"), device)
            except Exception as exc:
                print(f"[YoloInferencer] 缓存无法加载，将重新导出: {exc}")
        self._check_export_dependencies(backend)
        print(f"[YoloInferencer] 首次导出 {backend}, H×W={spec.height}×{spec.width}，可能需要较长时间")
        with cache.export_source() as source:
            candidate = source.with_suffix(f".{backend}")
            exported = YOLO(str(source)).export(format=backend, imgsz=spec.imgsz, batch=1,
                                               dynamic=False, half=device == "cuda",
                                               device=0 if device == "cuda" else "cpu", simplify=False)
            if not exported or Path(str(exported)).resolve() != candidate.resolve():
                raise RuntimeError(f"导出结果不是预期路径: {exported}")
            if backend == "onnx":
                self._simplify_onnx(candidate)
            validated = self._prepare_configured_model(YOLO(str(candidate), task="detect"), device)
            del validated
            cache.publish(candidate)
        return self._prepare_configured_model(YOLO(str(cache.artifact), task="detect"), device)

    @staticmethod
    def _check_export_dependencies(backend: str) -> None:
        """在进入导出器前检查依赖，缺失时由上层回退，不自动安装包。

        Args:
            backend: onnx 或 engine；TensorRT 自行优化图，无需 ONNX 简化器。
        Raises:
            ImportError: 缺少 requirements 中的导出依赖。
        """
        import onnx  # noqa: F401
        if backend == 'onnx':
            import onnxslim  # noqa: F401

    @staticmethod
    def _simplify_onnx(path: Path) -> None:
        """在应用内显式简化ONNX并保留元数据，避免导出器自动装GPU版运行库。

        Args:
            path: 本次导出的ONNX文件。
        Raises:
            Exception: 图读取、简化、校验或保存失败；完成标记尚未写入。
        """
        import onnx
        import onnxslim
        original = onnx.load(str(path))
        metadata = {item.key: item.value for item in original.metadata_props}
        optimized = onnxslim.slim(original)
        onnx.helper.set_model_props(optimized, metadata)
        onnx.checker.check_model(optimized)
        onnx.save(optimized, str(path))

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
            return self._prepare_configured_model(model, "cuda"), "cuda"
        else:
            onnx_path = os.path.join(weights_dir, "best.onnx")
            model = self.__load_onnx(onnx_path)
            if model is not None:
                return model, "cpu"
            print("[YoloInferencer] ONNX Runtime 不可用，使用 PyTorch CPU")
            model = YOLO(self.weight_path)
            model.to("cpu")
            return self._prepare_configured_model(model, "cpu"), "cpu"

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

        if self.input_spec is not None:
            try:
                return self._load_configured_export("engine", "cuda")
            except Exception as exc:
                print(f"[YoloInferencer] 配置尺寸的 TensorRT 不可用，回退 PT: {exc}")
                return None

        if os.path.exists(engine_path):
            print(f"[YoloInferencer] 加载已有 TensorRT 引擎: {engine_path}")
            try:
                return YOLO(engine_path)
            except Exception as e:
                print(f"[YoloInferencer] 加载 TensorRT 引擎失败: {e}，将重新导出")

        print("[YoloInferencer] 正在导出 TensorRT 引擎（FP16），首次导出可能需要几分钟...")
        try:
            self._check_export_dependencies('engine')
            pt_model = YOLO(self.weight_path)
            pt_model.export(format="engine", half=True, imgsz=PACKED_INPUT_SIZE[::-1], batch=1, dynamic=False, simplify=False)
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

        try:
            self._check_onnx_runtime(onnxruntime)
        except RuntimeError as exc:
            print(f"[YoloInferencer] ONNX Runtime 组件异常，回退 PT: {exc}")
            return None

        if self.input_spec is not None:
            try:
                return self._load_configured_export("onnx", "cpu")
            except Exception as exc:
                print(f"[YoloInferencer] 配置尺寸的 ONNX 不可用，回退 PT: {exc}")
                return None

        if os.path.exists(onnx_path):
            print(f"[YoloInferencer] 加载已有 ONNX 模型: {onnx_path}")
            try:
                return YOLO(onnx_path)
            except Exception as e:
                print(f"[YoloInferencer] 加载 ONNX 模型失败: {e}，将重新导出")

        print("[YoloInferencer] 正在导出 ONNX 模型，首次导出可能需要几十秒...")
        try:
            self._check_export_dependencies('onnx')
            pt_model = YOLO(self.weight_path)
            pt_model.export(format="onnx", imgsz=PACKED_INPUT_SIZE[::-1], batch=1, dynamic=False, simplify=False)
            self._simplify_onnx(Path(onnx_path))
            print(f"[YoloInferencer] ONNX 模型导出完成: {onnx_path}")
            return YOLO(onnx_path)
        except Exception as e:
            print(f"[YoloInferencer] ONNX 导出失败: {e}")
            return None

    @staticmethod
    def _check_onnx_runtime(runtime: object) -> None:
        """在导出/首帧前排除Python包装与原生库混装，避免加载成功后才崩溃。

        Args:
            runtime: 导入的 onnxruntime 模块。
        Raises:
            RuntimeError: 版本查询失败，或包装与原生库的主/次/修订版本不同。
        """
        getter = getattr(runtime, 'get_version_string', None)
        if not callable(getter):
            return
        python_version = str(getattr(runtime, '__version__', 'unknown'))
        try:
            native_version = str(getter())
        except Exception as exc:
            raise RuntimeError(f"无法查询ONNX Runtime原生版本: {exc}") from exc
        python_parts = re.findall(r'\d+', python_version)[:3]
        native_parts = re.findall(r'\d+', native_version)[:3]
        if len(python_parts) != 3 or python_parts != native_parts:
            raise RuntimeError(f"Python包装 {python_version} 与原生库 {native_version} 不一致，请修复依赖环境")

    def detect(self, img: Image.Image | None) -> Tuple[object, float]:
        """执行原图推理；有旁置配置时使用其精确高宽和固定补边。

        Args:
            img: 原始截图；None 表示本轮没有有效画面。
        Returns:
            Tuple[object, float]: (YOLO 单帧 raw_result 或 None, 推理耗时毫秒)
        """
        if img is None:
            return None, 0.0
        if getattr(self, "input_spec", None) is not None:
            return self._run(img)

        backend = getattr(getattr(self.model, "predictor", None), "model", None)
        flexible = (bool(getattr(backend, "pt", False) or getattr(backend, "dynamic", False)) if backend is not None
                    else str(self.model.overrides.get("model", "")).lower().endswith(".pt"))
        width, height = PACKED_INPUT_SIZE
        return self._run(img, (height, width) if flexible else None)

    def detect_packed(self, img: Image.Image) -> tuple[object | None, float]:
        """按当前模型配置执行拼图推理，旧模型才沿用历史画布规则。

        Args:
            img: 当前线程同步消费的 RGB 拼图画布。
        Returns:
            原始画布结果与模型调用耗时；导出仅发生在模型加载阶段。
        """
        if getattr(self, "input_spec", None) is not None:
            return self._run(img, packed=True)
        backend = getattr(getattr(self.model, "predictor", None), "model", None)
        flexible = (bool(getattr(backend, "pt", False) or getattr(backend, "dynamic", False)) if backend is not None
                    else str(self.model.overrides.get("model", "")).lower().endswith(".pt"))
        size: int | tuple[int, int] = img.width if img.width == img.height else (img.height, img.width)
        return self._run(img, size if flexible else None, packed=True)

    def _run(self, img: Image.Image, input_size: int | tuple[int, int] | None = None, *, packed: bool = False) -> tuple[object | None, float]:
        """运行单模型并同步 GPU 计时。

        Args:
            img: 当前输入图。
            input_size: 旧可变模型的候选尺寸；旁置配置始终优先。
            packed: 是否拼图调用；仅旧模型的补边策略随路线改变。

        Returns:
            单帧结果及整个 Ultralytics 调用毫秒，包含其内部预处理和后处理。
        Raises:
            ValueError: 旧模型元数据无效，或实际静态/预处理尺寸与配置冲突。
            Exception: 推理或设备同步失败时向控制器传播。
        """
        spec = getattr(self, "input_spec", None)
        if spec is not None:
            self._full_input_size = spec.imgsz
        elif self._full_input_size is None:
            configured = self.model.overrides.get("imgsz", 640)
            if not isinstance(configured, (int, list, tuple)):
                raise ValueError("模型输入尺寸元数据无效")
            self._full_input_size = configured.copy() if isinstance(configured, list) else configured
        selected_size = input_size if input_size is not None else self._full_input_size
        fixed_size = self._static_input_size()
        if fixed_size is not None:
            selected_size = fixed_size
        if spec is not None:
            if fixed_size is not None and fixed_size != spec.imgsz:
                raise ValueError(f"实际静态输入 {fixed_size} 与模型配置 {spec.imgsz} 不一致")
            selected_size = spec.imgsz
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
            rect=False if spec is not None else not packed,
            imgsz=selected_size,
        )
        if self.device == "cuda":
            torch.cuda.synchronize()
        yolo_ms = (time.perf_counter() - t0) * 1000
        actual = getattr(getattr(self.model, "predictor", None), "imgsz", None)
        if spec is not None and isinstance(actual, (tuple, list)) and tuple(actual) != spec.imgsz:
            raise ValueError(f"实际预处理尺寸 {actual} 与模型配置 {spec.imgsz} 不一致")
        # 首次 packed 调用也可能刚刚建立静态后端，立即记住其元数据形状。
        fixed_size = self._static_input_size()
        if fixed_size is not None:
            self._full_input_size = fixed_size
            self._full_size_confirmed = True
        if not packed and not getattr(self, "_full_size_confirmed", False):
            predictor = self.model.predictor
            if predictor is None:
                raise RuntimeError("模型未建立推理器")
            size = predictor.args.imgsz
            self._full_input_size = size.copy() if isinstance(size, list) else size
            self._full_size_confirmed = True

        return (results[0] if results else None), yolo_ms

    def _static_input_size(self) -> tuple[int, int] | None:
        """读取已加载静态后端的导出高宽，防止首帧拼图后被原默认尺寸覆盖。

        Returns:
            可信的固定 (height,width)；动态后端或没有二元尺寸元数据时为 None。
        """
        backend = getattr(getattr(self.model, "predictor", None), "model", None)
        shape = static_backend_shape(backend)
        return (shape[2], shape[3]) if shape is not None else None

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
