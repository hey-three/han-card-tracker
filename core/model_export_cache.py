"""在原权重同目录保存经过身份校验的静态导出，原始 PT 保持不变。"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
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

    def __init__(self, weight: Path, spec: ModelInput,
                 backend: str, environment: Mapping[str, str]) -> None:
        """建立缓存身份，不导出、不创建目录。

        Args:
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
            "version": 3, "weight_sha256": file_digest(weight),
            "imgsz": list(spec.imgsz), "batch": 1, "dynamic": False,
            "format": backend, "environment": dict(environment),
        }
        self.directory = weight.parent
        self.artifact = weight.with_suffix(f".{backend}")
        self.manifest = self.artifact.with_name(self.artifact.name + ".meta.json")

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

    @contextmanager
    def export_source(self) -> Iterator[Path]:
        """在权重目录内临时导出，验证成功前不覆盖已有正式文件。

        Yields:
            本次临时工作区中的PT副本；退出后清理工作区。
        Raises:
            OSError: 目录不可写、复制失败或源权重在导出期间变化。
        """
        with tempfile.TemporaryDirectory(dir=self.directory, prefix=".model-export-") as temporary:
            target = Path(temporary).resolve() / self.weight.name
            shutil.copyfile(self.weight, target)
            if file_digest(target) != self.identity["weight_sha256"]:
                raise OSError("导出期间源权重发生变化，请停止替换权重后重试")
            yield target

    def publish(self, exported: Path) -> None:
        """验证后的导出原子替换同目录文件，随后原子保存各自校验记录。

        Args:
            exported: 本次工作区中的已验证ONNX或engine，不得为原始PT。
        Raises:
            ValueError: 导出后缀不是对应后端格式。
            OSError: 源权重变化、导出不可读或写入/替换失败。
        """
        if exported.suffix != self.artifact.suffix:
            raise ValueError("导出文件后缀与后端格式不一致")
        if file_digest(self.weight) != self.identity["weight_sha256"]:
            raise OSError("导出期间源权重发生变化，保留原有导出")
        # 摘要取自本次候选文件，不能在替换后读取可能被另一实例覆盖的目标。
        data = {"identity": self.identity, "artifact_sha256": file_digest(exported)}
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".complete-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump(data, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(exported, self.artifact)
            os.replace(temporary, self.manifest)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
