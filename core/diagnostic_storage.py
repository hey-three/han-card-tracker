"""诊断后台的限量文件存储与日常轮转日志，不依赖Qt或模型。"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import shutil
import sys
import subprocess
import platform
from typing import cast

from PIL import Image, ImageDraw


class QuietRotatingHandler(RotatingFileHandler):
    """捕获日志写入错误，避免磁盘故障递归刷屏。

    Attributes:
        failure: 最近一次日志文件故障，空字符串表示正常。
    """

    def __init__(self, path: Path, max_bytes: int, backups: int) -> None:
        """建立延迟打开的日志处理器。

        Args:
            path: 唯一推理进程的日志路径。
            max_bytes: 每个文件的轮转阈值。
            backups: 旧文件数，不包括当前文件。
        """
        self.failure = ''
        super().__init__(path, maxBytes=max_bytes, backupCount=backups, encoding='utf-8', delay=True)

    def handleError(self, record: logging.LogRecord) -> None:
        """记录文件错误而不抛回业务链路。

        Args:
            record: 未能落盘的日志记录。
        """
        self.failure = str(sys.exc_info()[1])


class DailyLog:
    """只由推理进程写入的日常JSONL日志，默认含当前共五份约50MiB。

    Attributes:
        failure: 初始化或写入错误，不影响识别。
    """

    def __init__(self, base: Path, max_bytes: int = 10*1024*1024) -> None:
        """建立轮转日志；无写权限时保留故障状态。

        Args:
            base: 可写应用目录。
            max_bytes: 每份日志大小阈值。
        """
        self.failure = ''
        self._closed = False
        self._logger = logging.Logger('han.runtime', logging.INFO)
        self._handler: QuietRotatingHandler | None = None
        try:
            directory = base/'logs'
            directory.mkdir(parents=True, exist_ok=True)
            self._handler = QuietRotatingHandler(directory/'runtime.jsonl', max_bytes, 4)
            self._handler.setFormatter(logging.Formatter('%(message)s'))
            self._logger.addHandler(self._handler)
        except OSError as exc:
            self.failure = str(exc)

    def write(self, event: str, **fields: object) -> None:
        """写少量关键事件，不逐帧输出；异常文本截断至64KiB。

        Args:
            event: 事件名称。
            fields: 可JSON序列化的事件字段。
        """
        if self._closed or self.failure or self._handler is None:
            return
        try:
            text = json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'event': event,
                               **fields}, ensure_ascii=False, default=str)
            if len(text) > 65536:
                text = json.dumps({'event': event, 'truncated': True, 'message': text[:60000]}, ensure_ascii=False)
            self._logger.info(text)
            self.failure = self._handler.failure
        except Exception as exc:
            self.failure = str(exc)

    def close(self) -> None:
        """关闭并释放当前日志文件；不删除历史文件。"""
        self._closed = True
        if self._handler is not None:
            self._handler.close()


class StorageLimitError(OSError):
    """当前及历史材料达到配额，不能继续写入。

    Attributes:
        args: 容量停止的原因。
    """

    def __init__(self, message: str) -> None:
        """携带配额错误。

        Args:
            message: 用户可见原因。
        """
        super().__init__(message)


class SessionStorage:
    """仅在后台线程运行的完整诊断存储。

    Attributes:
        directory: 当前会话目录，开局前即存在。
        used: 诊断根目录已使用的估计字节数。
    """

    def __init__(self, root: Path, session: str, quota: int) -> None:
        """扫描并创建会话，清理仅限已结束且带本项目标记的旧目录。

        Args:
            root: 专用diagnostics目录。
            session: 本进程生成的唯一会话名。
            quota: 所有诊断会话总字节上限。
        Raises:
            OSError: 目录创建、读取或容量检查失败。
        """
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.directory = self.root/session
        self.quota = quota
        self.used = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file() and not p.is_symlink())
        self._hashes: dict[tuple[str, int, int], str] = {}
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.environment: dict[str, object] = {'python': sys.version, 'system': platform.system(),
            'release': platform.release(), 'machine': platform.machine(),
            'loaded_packages': {name: str(getattr(sys.modules[name], '__version__', 'unknown'))
                                for name in ('torch', 'ultralytics', 'numpy', 'PIL') if name in sys.modules}}
        if (self.root.parent/'.git').exists():
            try:
                revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.root.parent,
                    capture_output=True, text=True, timeout=2,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.environment['source_head_at_start'] = revision.stdout.strip() if revision.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                self.environment['source_head_at_start'] = None
        self._ensure_space(4096)
        self.directory.mkdir()
        (self.directory/'frames').mkdir()
        self.manifest({'state': 'recording', 'complete': False})

    def _ensure_space(self, required: int) -> None:
        """腾出配额；不删除当前会话、未知目录或未正常结束的会话。

        Args:
            required: 计划新增的字节数。
        Raises:
            StorageLimitError: 没有可安全清理的材料或当前会话已触顶。
            OSError: 已结束会话无法清理。
        """
        if self.used+required+4096 <= self.quota:
            return
        for path in sorted(self.root.glob('session_*')):
            if path == self.directory or path.is_symlink() or path.resolve().parent != self.root:
                continue
            try:
                info = json.loads((path/'manifest.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if info.get('schema') != 'han-diagnostics/v1' or info.get('state') not in ('closed', 'paused', 'failed'):
                continue
            size = sum(p.stat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink())
            shutil.rmtree(path)
            self.used -= size
            if self.used+required+4096 <= self.quota:
                return
        raise StorageLimitError('完整诊断容量达到上限，已暂停记录；请导出或清理旧会话后重新开启')

    def _blob(self, path: Path, data: bytes) -> None:
        """检查配额并写一个会话内文件。

        Args:
            path: 当前会话内路径。
            data: 已编码内容。
        Raises:
            OSError: 配额、路径或写入失败。
        """
        if not path.resolve().is_relative_to(self.directory):
            raise OSError('诊断输出路径超出当前会话')
        self._ensure_space(len(data))
        path.write_bytes(data)
        self.used += len(data)

    def manifest(self, fields: dict[str, object]) -> None:
        """原子更新会话完成度；recording不等于完整保存。

        Args:
            fields: 状态和已保存/缺失计数。
        Raises:
            OSError: 状态文件写入或替换失败。
        """
        target = self.directory/'manifest.json'
        previous = target.stat().st_size if target.exists() else 0
        data = json.dumps({'schema': 'han-diagnostics/v1', 'session': self.directory.name,
                          'started_at': self.started_at, 'environment': self.environment, **fields},
                         ensure_ascii=False, indent=2).encode('utf-8')
        temporary = self.directory/'manifest.tmp'
        temporary.write_bytes(data)
        temporary.replace(target)
        self.used += len(data)-previous

    def _model_hash(self, payload: dict[str, object], path_key: str = 'weight_path') -> str | None:
        """后台计算选中权重文件当时的SHA，不向日志复制权重。

        Args:
            payload: 含配置上下文的帧数据。
            path_key: 原始权重或实际载入文件的上下文字段。
        Returns:
            文件SHA或None；不承诺文件被外部改写后等同已加载模型内存。
        """
        context = cast(dict[str, object], payload.get('context', {}))
        name = context.get(path_key)
        if not isinstance(name, str):
            return None
        try:
            path = Path(name)
            stat = path.stat()
            key = (name, stat.st_mtime_ns, stat.st_size)
            if key not in self._hashes:
                digest = hashlib.sha256()
                with path.open('rb') as source:
                    for block in iter(lambda: source.read(1024*1024), b''):
                        digest.update(block)
                self._hashes[key] = digest.hexdigest()
            return self._hashes[key]
        except OSError:
            return None

    def write_frame(self, payload_bytes: bytes, images: dict[str, Image.Image]) -> None:
        """后台压缩、绘制标注并原子发布一帧，最后追加索引。

        Args:
            payload_bytes: 推理线程冻结的JSON数据。
            images: 推理线程复制的原图和每次模型输入。
        Raises:
            OSError: 容量、磁盘或原子发布失败。
            ValueError: 数据或图像无效。
        """
        payload = json.loads(payload_bytes)
        number = int(payload['frame_id'])
        pending = self.directory/'frames'/f'.pending_{number:08d}'
        final = pending.with_name(f'{number:08d}')
        pending.mkdir()
        try:
            for name, image in images.items():
                buffer = io.BytesIO()
                image.save(buffer, format='PNG', compress_level=1)
                self._blob(pending/(name+'.png'), buffer.getvalue())
            for call in payload['calls']:
                name = call['input_key']
                if name in images:
                    self._annotated(pending/(name+'_annotated.png'), images[name], call['boxes'])
            if 'original' in images:
                self._annotated(pending/'final_original_annotated.png', images['original'], payload.get('final_boxes', []))
            payload['model_file_sha256_at_write'] = self._model_hash(payload)
            payload['model_artifact_sha256_at_write'] = self._model_hash(payload, 'artifact_path')
            self._blob(pending/'frame.json', json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'))
            pending.replace(final)
            line = json.dumps({'frame_id': number, 'path': f'frames/{number:08d}/frame.json',
                               'reasons': payload.get('reasons'), 'error': payload.get('error')}, ensure_ascii=False).encode('utf-8')+b'\n'
            self._ensure_space(len(line))
            with (self.directory/'events.jsonl').open('ab') as index:
                index.write(line)
            self.used += len(line)
        except Exception:
            if pending.exists():
                size = sum(p.stat().st_size for p in pending.rglob('*') if p.is_file())
                shutil.rmtree(pending)
                self.used -= size
            raise

    def write_gap(self, gap: dict[str, object]) -> None:
        """后台追加明确的缺帧区间，与逐帧索引使用同一文件。

        Args:
            gap: 首尾帧号和跳过原因，首尾均包含在缺口中。
        Raises:
            OSError: 配额不足或索引写入失败。
        """
        line = json.dumps({'record_type': 'gap', **gap}, ensure_ascii=False).encode('utf-8')+b'\n'
        self._ensure_space(len(line))
        with (self.directory/'events.jsonl').open('ab') as index:
            index.write(line)
        self.used += len(line)

    def _annotated(self, path: Path, image: Image.Image, boxes: list[dict[str, object]]) -> None:
        """在副本画检测框，避免调用GPU模型对象或修改原始快照。

        Args:
            path: 目标PNG路径。
            image: 已冻结的输入图。
            boxes: 相应坐标空间的检测数据。
        Raises:
            OSError: 编码或写入失败。
            ValueError: 标注数据无效。
        """
        marked = image.copy() if image.mode == 'RGB' else image.convert('RGB')
        draw = ImageDraw.Draw(marked)
        for box in boxes:
            coords = cast(list[float], box['bbox'])
            draw.rectangle(tuple(coords), outline=(255, 70, 40), width=2)
            draw.text((coords[0], max(0., coords[1]-12)), f"{box['name']} {float(cast(float, box['confidence'])):.2f}", fill=(255, 220, 0))
        buffer = io.BytesIO()
        marked.save(buffer, format='PNG', compress_level=1)
        self._blob(path, buffer.getvalue())
        marked.close()
