"""配置文件的严格读取与原子保存，调用者统一由主进程管理。"""

from collections.abc import Mapping
from pathlib import Path
import os
import tempfile

import yaml


def read_config(path: str) -> dict[str, object]:
    """读取配置，拒绝把损坏或不可读的文件当作空配置。

    Args:
        path: YAML 文件路径。

    Returns:
        配置字典；文件不存在时返回空字典。

    Raises:
        OSError: 已有文件无法读取。
        ValueError: 文件不是合法的非空 YAML 字典。
    """
    try:
        with open(path, encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as exc:
        raise ValueError(f"配置文件格式错误: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"配置文件必须包含字典: {path}")
    return value


def write_config(path: str, values: Mapping[str, object]) -> None:
    """先写同目录临时文件，再原子替换配置。

    Args:
        path: 目标路径，其父目录必须存在。
        values: 完整配置内容。

    Raises:
        OSError: 创建、写入或替换失败；原文件在替换前保持完整。
        yaml.YAMLError: 配置包含不能序列化的值。
    """
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=Path(path).parent,
            prefix=".config-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = stream.name
            yaml.safe_dump(dict(values), stream, allow_unicode=True, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
