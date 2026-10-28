"""保存手动布局的本机参考截图，与推理诊断材料和运行时布局配置独立。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from config import settings


def _validate_identity(layout_name: str, window_title: str) -> None:
    """验证参考图绑定的布局名称与目标标题。

    Args:
        layout_name: 已保存或即将保存的布局名称。
        window_title: 该截图对应的目标窗口标题。
    Raises:
        TypeError: 参数不是字符串。
        ValueError: 参数为空或只有空白。
    """
    for value in (layout_name, window_title):
        if not isinstance(value, str):
            raise TypeError('布局名称和窗口标题必须为字符串')
        if not value.strip():
            raise ValueError('布局名称和窗口标题不能为空')


def _reference_path(layout_name: str) -> Path:
    """从已验证的布局名称生成本机缓存位置，名称不参与文件路径拼接。

    Args:
        layout_name: 已经验证的非空布局名称。
    Returns:
        当前用户配置目录内对应的唯一PNG路径。
    """
    digest = hashlib.sha256(layout_name.encode('utf-8')).hexdigest()
    return Path(settings.CONFIG_PATH).resolve().parent / 'layout_previews' / f'{digest}.png'


def load_reference_image(layout_name: str, window_title: str) -> Image.Image | None:
    """读取对应布局及窗口的原始参考图，不载入其他窗口的截图。

    Args:
        layout_name: 布局名称。
        window_title: 布局当前保存的窗口标题。
    Returns:
        独立的RGB图像；没有缓存或目标标题不匹配时为None。
    Raises:
        TypeError: 身份参数类型错误。
        ValueError: 身份为空或缓存元数据损坏。
        OSError: 缓存图像损坏或无法读取。
    """
    _validate_identity(layout_name, window_title)
    try:
        with Image.open(_reference_path(layout_name)) as image:
            metadata = image.info.get('han-layout-reference')
            if image.format != 'PNG' or not isinstance(metadata, str):
                raise ValueError('布局参考图格式或元数据无效')
            identity = json.loads(metadata)
            if not isinstance(identity, dict) or identity.get('schema') != 'han-layout-reference/v1':
                raise ValueError('布局参考图元数据损坏')
            if identity.get('layout_name') != layout_name or identity.get('window_title') != window_title:
                return None
            return image.convert('RGB')
    except FileNotFoundError:
        return None


def save_reference_image(layout_name: str, window_title: str, image: Image.Image) -> None:
    """原子替换一个布局的参考PNG，图片和身份元数据保存在同一个文件内。

    Args:
        layout_name: 布局名称，与原名称相同则覆盖旧参考图。
        window_title: 实际截图目标标题。
        image: 原始参考截图，不包含编辑器叠加的五区边框。
    Raises:
        TypeError: 身份参数或图片类型错误。
        ValueError: 身份为空或图片尺寸无效。
        OSError: 创建目录、编码或替换文件失败；已有文件保持不变。
    """
    _validate_identity(layout_name, window_title)
    if not isinstance(image, Image.Image):
        raise TypeError('参考截图必须为PIL图像')
    if min(image.size) <= 0:
        raise ValueError('参考截图尺寸必须为正')
    target = _reference_path(layout_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = PngInfo()
    metadata.add_text('han-layout-reference', json.dumps({
        'schema': 'han-layout-reference/v1', 'layout_name': layout_name, 'window_title': window_title,
    }, ensure_ascii=True))
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.stem + '-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
        image.save(temporary, format='PNG', pnginfo=metadata)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
