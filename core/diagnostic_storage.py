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
import time
from typing import cast

from PIL import Image, ImageDraw


def _replace_with_retry(source: Path, target: Path) -> None:
    """后台原子替换遇到Windows短暂文件占用时有界重试。

    Args:
        source: 已完整写入的临时文件或目录。
        target: 要原子发布的目标路径。
    Raises:
        OSError: 非Windows占用错误，或六次替换后仍无法发布。
    """
    delays = (.02, .05, .1, .2, .4)
    for attempt in range(len(delays)+1):
        try:
            source.replace(target)
            return
        except OSError as exc:
            # 5为访问拒绝，32/33为共享或锁冲突；真正权限故障最终仍须报告。
            if getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == len(delays):
                raise
            time.sleep(delays[attempt])


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
        quota: 可更新的全局磁盘字节额度。
        _round_directory: 当前写入的等待段或局目录。
        _round_info: 当前采集段状态、数量与边界信息。
        pruned_rounds: 当前会话已按额度清理的旧采集段数量。
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
        self._round_directory: Path | None = None
        self._round_info: dict[str, object] = {}
        self.pruned_rounds = 0
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
        self.manifest({'state': 'recording', 'complete': False})

    def _ensure_space(self, required: int) -> None:
        """腾出总配额，优先按时间清理已结束会话或本会话已结束旧局。

        Args:
            required: 计划新增字节。
        Raises:
            StorageLimitError: 当前局/受保护材料占满且无可安全清理空间。
            OSError: 读取或清理失败。
        """
        if self.used+required+4096 <= self.quota:
            return
        for path in sorted(self.root.glob('session_*')):
            if path.is_symlink() or path.resolve().parent != self.root:
                continue
            if path == self.directory:
                candidates = sorted(path.glob('waiting'))+sorted(path.glob('game_*'))
                for group in candidates:
                    if group == self._round_directory or group.is_symlink() or group.resolve().parent != self.directory:
                        continue
                    if not self._closed_metadata(group/'round.json', 'han-diagnostic-round/v1'):
                        continue
                    self._delete_owned(group)
                    self.pruned_rounds += 1
                    line = json.dumps({'record_type': 'retention_cleanup', 'directory': group.name}).encode('utf-8')+b'\n'
                    with (self.directory/'events.jsonl').open('ab') as index:
                        index.write(line)
                    self.used += len(line)
                    if self.used+required+4096 <= self.quota:
                        return
            elif self._closed_metadata(path/'manifest.json', 'han-diagnostics/v1'):
                self._delete_owned(path)
                if self.used+required+4096 <= self.quota:
                    return
        raise StorageLimitError('完整诊断达到磁盘上限，当前局或未封口材料不能自动删除；请提高磁盘额度或处理旧材料后重新开启')

    @staticmethod
    def _closed_metadata(path: Path, schema: str) -> bool:
        """只认可本工具已结束的目录标记，不把未知或崩溃目录当垃圾。

        Args:
            path: 标记文件。
            schema: 允许删除的schema。
        Returns:
            是否带正确标记且已经结束采集。
        """
        try:
            info = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return False
        return isinstance(info, dict) and info.get('schema') == schema and info.get('state') in ('closed', 'paused', 'failed')

    def _delete_owned(self, path: Path) -> None:
        """验证绝对路径在受管范围内，再删除已由调用者核实标记的目录。

        Args:
            path: 已结束旧会话或旧局。
        Raises:
            OSError: 路径越界、别名链接或删除失败。
        """
        resolved = path.resolve()
        if (path.is_symlink() or resolved != path.absolute()
                or resolved.parent not in (self.root, self.directory) or path == self._round_directory):
            raise OSError('拒绝清理非受管诊断目录')
        size = sum(item.stat().st_size for item in path.rglob('*') if item.is_file() and not item.is_symlink())
        shutil.rmtree(path)
        self.used -= size

    def set_quota(self, quota: int) -> None:
        """后台应用新磁盘额度，重新核对使用量并清理已结束旧局。

        Args:
            quota: 新额度，正整数且单位字节。
        Raises:
            TypeError: quota不是整数。
            ValueError: quota非正。
            OSError: 扫描、清理失败或当前局无法容纳。
        """
        if type(quota) is not int:
            raise TypeError('磁盘额度必须是整数字节数')
        if quota <= 0:
            raise ValueError('磁盘额度必须为正')
        self.quota = quota
        self.used = sum(path.stat().st_size for path in self.root.rglob('*') if path.is_file() and not path.is_symlink())
        self._ensure_space(0)

    def _write_round_metadata(self) -> None:
        """原子保存当前局标记，关键收尾使用预留的少量元数据空间。

        Raises:
            OSError: 写入或替换失败。
        """
        if self._round_directory is None:
            return
        target = self._round_directory/'round.json'
        previous = target.stat().st_size if target.exists() else 0
        data = json.dumps({'schema': 'han-diagnostic-round/v1', **self._round_info}, ensure_ascii=False, indent=2).encode('utf-8')
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(data)
        _replace_with_retry(temporary, target)
        self.used += len(data)-previous

    def finish_round(self, state: str = 'closed', reason: str = 'diagnostics_stopped') -> None:
        """封口当前局的采集段，不据此宣称已捕获整局或没有缺帧。

        Args:
            state: closed或paused。
            reason: 下一局开始、关闭诊断或保存故障。
        Raises:
            OSError: 标记写入失败。
        """
        if self._round_directory is not None and self._round_directory.is_dir():
            self._round_info.update(state=state, closed_reason=reason)
            self._write_round_metadata()

    def start_round(self, number: int, first_frame: int, started: bool = True) -> None:
        """建立开局目录，即使该局尚未成功接收图片也有可追踪标记。

        Args:
            number: 局号。
            first_frame: 开局观测帧号。
            started: 是否观察到实际Tracker开局转换。
        Raises:
            OSError: 文件夹或标记写入失败。
            TypeError: 参数类型错误。
            ValueError: 局号/帧号非正，或局号倒退。
        """
        if type(number) is not int or type(first_frame) is not int or type(started) is not bool:
            raise TypeError('局号、帧号必须为整数，开局标记必须为bool')
        if number <= 0 or first_frame <= 0:
            raise ValueError('局号、开局帧号必须为正')
        self._select_round({'round_id': number, 'frame_id': first_frame})
        self._round_info['saw_start'] = bool(self._round_info['saw_start']) or started
        self._write_round_metadata()

    def _select_round(self, payload: dict[str, object]) -> Path:
        """按冻结帧的局号切目录，确保排队中的上一局图片不会串到新局。

        Args:
            payload: 已冻结的帧数据。
        Returns:
            当前局或等待区目录。
        Raises:
            TypeError: 局号非整数。
            ValueError: 局号为负、倒退或目录重复。
            OSError: 文件夹或标记创建失败。
        """
        number = payload.get('round_id', 0)
        if type(number) is not int:
            raise TypeError('诊断局号必须为整数')
        number = cast(int, number)
        if number < 0 or number < cast(int, self._round_info.get('round_id', 0)):
            raise ValueError('诊断局号不能为负或倒退')
        target = self.directory/(f'game_{number:04d}' if number else 'waiting')
        if target != self._round_directory:
            self.finish_round(reason='next_round_started')
            self._round_directory = target
            self._round_info = {'round_id': number, 'kind': 'game' if number else 'waiting', 'state': 'recording',
                                'first_frame': payload['frame_id'], 'last_frame': payload['frame_id'], 'saved_frames': 0,
                                'saw_start': False, 'saw_end': False}
            self._ensure_space(1024)
            target.mkdir()
            (target/'frames').mkdir()
            self._write_round_metadata()
        return target

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
                          'started_at': self.started_at, 'environment': self.environment, 'pruned_rounds': self.pruned_rounds, **fields},
                         ensure_ascii=False, indent=2).encode('utf-8')
        temporary = self.directory/'manifest.tmp'
        temporary.write_bytes(data)
        _replace_with_retry(temporary, target)
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
        group = self._select_round(payload)
        pending = group/'frames'/f'.pending_{number:08d}'
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
            _replace_with_retry(pending, final)
            line = json.dumps({'frame_id': number, 'path': f'{group.name}/frames/{number:08d}/frame.json',
                               'reasons': payload.get('reasons'), 'error': payload.get('error')}, ensure_ascii=False).encode('utf-8')+b'\n'
            self._ensure_space(len(line))
            with (self.directory/'events.jsonl').open('ab') as index:
                index.write(line)
            self.used += len(line)
            self._round_info['last_frame'] = number
            self._round_info['saved_frames'] = cast(int, self._round_info['saved_frames'])+1
            before = cast(dict[str, object], payload.get('before', {}))
            after = cast(dict[str, object], payload.get('after', {}))
            if before.get('state') == 0 and after.get('state') in (1, 2):
                self._round_info['saw_start'] = True
            if after.get('awaiting_next_round') is True:
                self._round_info['saw_end'] = True
            self._write_round_metadata()
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
