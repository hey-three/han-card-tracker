"""读取权重旁的固定输入契约；不加载模型，也不在逐帧路径读取 YAML。"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from config.store import read_config


@dataclass(frozen=True)
class ModelInput:
    """已校验的固定 letterbox 输入，显式区分画布宽高和张量高宽。

    Attributes:
        width: 输入宽度，须为正的 32 倍数。
        height: 输入高度，须为正的 32 倍数。
        config_path: 此契约的来源文件。
    """

    width: int
    height: int
    config_path: Path

    @property
    def canvas_size(self) -> tuple[int, int]:
        """返回 PIL/拼图使用的 (宽, 高)。"""
        return self.width, self.height

    @property
    def imgsz(self) -> tuple[int, int]:
        """返回 Ultralytics 推理/导出使用的 (高, 宽)。"""
        return self.height, self.width


def load_model_input(weight_path: Path) -> ModelInput | None:
    """读取所选权重同目录的 model_config.yaml，缺文件时兼容旧配置。

    Args:
        weight_path: 已选中的 best.pt；不按目录名或 model 字段选择网络。
    Returns:
        固定输入契约；没有旁置文件时为 None。
    Raises:
        OSError: 已有配置文件无法读取。
        TypeError: 字段类型错误，包括将布尔值写成尺寸。
        ValueError: YAML 损坏、尺寸不合法或请求了不支持的预处理。
    """
    path = weight_path.with_name("model_config.yaml")
    if not path.exists():
        return None
    data = read_config(str(path))
    source = data.get("input")
    if not isinstance(source, Mapping):
        raise TypeError(f"{path}: input 必须是字典")
    dimensions: list[int] = []
    for name in ("width", "height"):
        value = source.get(name)
        if type(value) is not int:
            raise TypeError(f"{path}: input.{name} 必须是整数")
        if value <= 0 or value % 32:
            raise ValueError(f"{path}: input.{name} 必须是正的 32 倍数")
        dimensions.append(value)
    resize = source.get("resize", "letterbox")
    if not isinstance(resize, str):
        raise TypeError(f"{path}: input.resize 必须是字符串")
    if resize != "letterbox":
        raise ValueError(f"{path}: 仅支持等比缩放补边 resize: letterbox")
    rect = source.get("rect", False)
    if type(rect) is not bool:
        raise TypeError(f"{path}: input.rect 必须是布尔值")
    if rect:
        raise ValueError(f"{path}: 固定输入须设置 rect: false，避免最小补边改变尺寸")
    weights = data.get("weights")
    if weights is not None:
        if not isinstance(weights, list) or not all(isinstance(name, str) for name in weights):
            raise TypeError(f"{path}: weights 必须是文件名列表")
        if weight_path.name not in weights:
            raise ValueError(f"{path}: weights 未包含所选权重 {weight_path.name}")
    return ModelInput(dimensions[0], dimensions[1], path)


def static_backend_shape(backend: object) -> tuple[int, int, int, int] | None:
    """优先读取静态后端真实绑定形状，最后兼容其导出元数据。

    Args:
        backend: Ultralytics AutoBackend 或测试替身。
    Returns:
        固定 (批次, 通道, 高, 宽)；动态/PT 或未知时为 None。
    """
    if backend is None or getattr(backend, "pt", False) or getattr(backend, "dynamic", False):
        return None
    shape: object = None
    session = getattr(backend, "session", None)
    bindings = getattr(backend, "bindings", None)
    if session is not None:
        inputs = session.get_inputs()
        if inputs:
            shape = inputs[0].shape
    elif isinstance(bindings, Mapping):
        shape = getattr(bindings.get("images"), "shape", None)
    if isinstance(shape, (tuple, list)) and len(shape) == 4:
        if all(type(value) is int and value > 0 for value in shape):
            return shape[0], shape[1], shape[2], shape[3]
        return None
    size = getattr(backend, "imgsz", None)
    if (isinstance(size, (tuple, list)) and len(size) == 2
            and all(type(value) is int and value > 0 for value in size)):
        return 1, 3, size[0], size[1]
    return None
