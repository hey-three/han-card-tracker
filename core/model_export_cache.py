"""按权重内容、输入尺寸及环境隔离静态导出，不覆盖用户原始文件。"""

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import os

from core.model_input import ModelInput


def file_digest(path: Path) -> str:
    """流式计算文件 SHA-256，仅用于模型加载和缓存确认。

    Args:
        path: 待读取的权重或导出文件。
    Returns:
        SHA-256 十六进制摘要。
    Raises:
        OSError: 文件不可读。
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class ModelExportCache:
    """保存一次性导出及完成标记，未完成或内容改变的导出不复用。"""

    def __init__(self, root: Path, weight: Path, spec: ModelInput,
                 backend: str, environment: Mapping[str, str]) -> None:
        """建立缓存身份，不导出、不创建目录。

        Args:
            root: 可写运行目录中的模型缓存根目录。
            weight: 原始 PT 权重。
            spec: 固定输入契约。
            backend: onnx 或 engine。
            environment: 导出库、精度及设备版本标识。
        Raises:
            ValueError: 后端格式不受支持。
            OSError: 无法读取原始权重。
        """
        if backend not in {"onnx", "engine"}:
            raise ValueError("仅支持 onnx/engine 静态导出")
        self.weight = weight
        self.identity: dict[str, object] = {
            "version": 2, "weight_sha256": file_digest(weight),
            "imgsz": list(spec.imgsz), "batch": 1, "dynamic": False,
            "format": backend, "environment": dict(environment),
        }
        key = hashlib.sha256(json.dumps(self.identity, sort_keys=True).encode()).hexdigest()
        self.directory = root / key
        self.artifact = self.directory / f"best.{backend}"
        self.manifest = self.directory / "complete.json"

    def ready(self) -> bool:
        """确认身份与导出文件摘要一致，损坏或半成品缓存视为不可用。

        Returns:
            是否可尝试加载已有导出；加载时仍须验证真实输入形状。
        """
        try:
            data = json.loads(self.manifest.read_text(encoding="utf-8"))
            return (isinstance(data, dict) and data.get("identity") == self.identity
                    and self.artifact.is_file()
                    and data.get("artifact_sha256") == file_digest(self.artifact))
        except (OSError, ValueError):
            return False

    def prepare_weight(self) -> Path:
        """复制源权重到隔离目录，使导出器只在缓存中产生文件。

        Returns:
            导出时加载的 best.pt 副本路径。
        Raises:
            OSError: 创建目录或复制失败，不能使用未完成标记。
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        self.manifest.unlink(missing_ok=True)
        target = self.directory / "best.pt"
        shutil.copyfile(self.weight, target)
        if file_digest(target) != self.identity["weight_sha256"]:
            raise OSError("导出期间源权重发生变化，请停止替换权重后重试")
        return target

    def seal(self) -> None:
        """在导出并验证形状后原子写入完成标记，失败则不留下可信半标记。

        Raises:
            OSError: 导出文件不可读或完成标记写入失败。
        """
        data = {"identity": self.identity, "artifact_sha256": file_digest(self.artifact)}
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".complete-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump(data, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.manifest)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
