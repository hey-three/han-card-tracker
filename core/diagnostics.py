"""完整诊断的有界快照队列与单后台线程；默认关闭，不参与业务判定。"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
import json
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread
import time
import traceback
from uuid import uuid4
from typing import cast

from PIL import Image

from core.diagnostic_storage import DailyLog, SessionStorage
from core.frame_packing import PackingPlan, read_detections


class DiagnosticLimits:
    """固定安全上限，不把无限队列当作后台加速。

    Attributes:
        frames: 待完成帧上限，包含后台正在写入的一帧。
        memory: 待完成图片和JSON预算，不是整个应用内存限制。
        disk: diagnostics目录总容量。
    """

    def __init__(self, frames: int = 8, memory: int = 64*1024*1024, disk: int = 1024*1024*1024) -> None:
        """校验正整数上限。

        Args:
            frames: 待完成帧上限，默认8帧。
            memory: 队列字节预算。
            disk: 磁盘字节预算。
        Raises:
            TypeError: 参数不是整数。
            ValueError: 参数非正。
        """
        if any(type(v) is not int for v in (frames, memory, disk)):
            raise TypeError('诊断上限必须是整数')
        if min(frames, memory, disk) <= 0:
            raise ValueError('诊断上限必须为正')
        self.frames, self.memory, self.disk = frames, memory, disk


def boxes_json(raw: object | None) -> list[dict[str, object]]:
    """将模型结果冻结为CPU数值，不把GPU张量交给写盘线程。

    Args:
        raw: 当前模型或还原后的结果。
    Returns:
        类别、坐标和置信度，不改变原结果。
    """
    return [{'name': c.name, 'bbox': list(c.bbox), 'confidence': c.confidence} for c in read_detections(raw)]


class DiagnosticFrame:
    """由识别线程独占构建、提交后不再修改的单帧快照。

    Attributes:
        payload: 帧状态和每次模型调用记录。
        images: 已复制的RGB图；后台只消费这些副本。
        reserved: 当前内存预算，含JSON预留。
        cancelled: 是否已取消或提交。
    """

    def __init__(self, owner: DiagnosticRecorder, number: int, context: dict[str, object]) -> None:
        """创建已预留一个帧名额的记录。

        Args:
            owner: 会话所有者。
            number: 进程内单调帧号，不按局归零。
            context: 当前配置与请求标识。
        """
        self.owner = owner
        self.payload: dict[str, object] = {'schema': 'han-diagnostic-frame/v1', 'frame_id': number,
            'time_utc': datetime.now(timezone.utc).isoformat(), 'monotonic': time.monotonic(), 'context': context}
        self.images: dict[str, Image.Image] = {}
        self.calls: list[dict[str, object]] = []
        self.reserved = 65536
        self.cancelled = False
        self.capture_ms = 0.

    def image(self, key: str, image: Image.Image | None) -> None:
        """先检查预算再复制，不能把可复用画布引用放入队列。

        Args:
            key: 此帧内部固定图片名。
            image: 本次输入；None仅记录缺失。
        """
        if image is None or self.cancelled:
            return
        started = time.perf_counter()
        # PIL的RGB内部可按四字节存储，额外预留小量对象开销。
        size = image.width*image.height*4+1024
        if not self.owner.claim(self, size):
            self.cancel()
            return
        try:
            self.images[key] = image.copy() if image.mode == 'RGB' else image.convert('RGB')
        except Exception as exc:
            self.owner.pause('snapshot_failed: '+str(exc), cast(int, self.payload['frame_id']))
            self.cancel()
        self.capture_ms += (time.perf_counter()-started)*1000

    def begin_call(self, kind: str, image: Image.Image | None, plan: PackingPlan | None) -> int:
        """在模型调用前冻结实际输入及坐标变换。

        Args:
            kind: packed或full。
            image: 此次模型接口输入。
            plan: 拼图布局；整图为None。
        Returns:
            当前调用序号；取消后仍可安全结束调用。
        """
        index = len(self.calls)
        if self.cancelled:
            return index
        try:
            key = f'call_{index+1:02d}_input'
            self.image(key, image)
            self.calls.append({'kind': kind, 'input_key': key, 'input_size': list(image.size) if image else None,
                'tiles': [] if plan is None else [{'source': t.source, 'source_region': t.source_region,
                        'x': t.x, 'y': t.y, 'render_size': t.render_size} for t in plan.tiles]})
        except Exception as exc:
            self.owner.pause('call_input_snapshot_failed: '+str(exc), cast(int, self.payload['frame_id']))
            self.cancel()
        return index

    def end_call(self, index: int, raw: object | None, elapsed: float, error: str = '') -> None:
        """冻结输出，包含失败或空结果，不伪装成成功识别。

        Args:
            index: begin_call返回序号。
            raw: 当前调用结果。
            elapsed: 模型接口耗时毫秒。
            error: 原始异常堆栈，正常为空。
        """
        if self.cancelled:
            return
        started = time.perf_counter()
        try:
            self.calls[index].update(boxes=boxes_json(raw), elapsed_ms=elapsed, error=error,
                                     result_known=raw is not None and getattr(raw, 'boxes', None) is not None)
        except Exception as exc:
            self.calls[index].update(boxes=[], elapsed_ms=elapsed, error=error, serialization_error=str(exc))
        self.capture_ms += (time.perf_counter()-started)*1000

    def finish(self, fields: dict[str, object]) -> None:
        """冻结JSON并非阻塞提交；失败仅暂停诊断，不影响牌局。

        Args:
            fields: 更新后状态、原因、最终检测和计时。
        """
        if self.cancelled:
            return
        try:
            self.payload.update(fields, calls=self.calls)
            data = json.dumps(self.payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(data) > 256*1024:
                raise ValueError('单帧JSON超过256KiB')
            if not self.owner.claim(self, max(0, len(data)-65536)):
                self.cancel()
                return
            self.owner.submit(self, data)
        except Exception as exc:
            self.owner.pause('snapshot_failed: '+str(exc), cast(int, self.payload['frame_id']))
            self.cancel()

    def cancel(self) -> None:
        """释放未提交快照及名额，可重复调用。"""
        if not self.cancelled:
            self.cancelled = True
            for image in self.images.values():
                image.close()
            self.images.clear()
            self.owner.release(self.reserved)


class DiagnosticRecorder:
    """轻量日志常开，完整诊断开启时才创建一个后台保存线程。

    Attributes:
        daily: 限量轮转的关键事件日志。
        limits: 队列和磁盘上限。
        frame_id: 每次检测递增，包括未启用记录的帧。
    """

    def __init__(self, base: str | Path, notify: Callable[[dict[str, object]], None] | None = None,
                 limits: DiagnosticLimits | None = None) -> None:
        """建立无图片复制、无诊断线程的关闭状态。

        Args:
            base: 应用可写目录。
            notify: 向主进程发送非Qt状态通知。
            limits: 测试或部署使用的限额。
        """
        self.base = Path(base)
        self.daily = DailyLog(self.base)
        self.limits = limits or DiagnosticLimits()
        self.frame_id = 0
        self._notify = notify
        self._lock = Lock()
        self._thread: Thread | None = None
        self._stop = Event()
        self._queue: Queue[tuple[bytes, dict[str, Image.Image], int]] = Queue(self.limits.frames)
        self._pending_bytes = self._pending_frames = self._saved = 0
        self._state = 'off'
        self._reason = ''
        self._session = ''
        self._session_start = 1
        self._first_missing: int | None = None
        self._dropped_frames = 0
        self._resume_required = 65536
        self._pending_gaps: deque[dict[str, object]] = deque()
        self._recent_gaps: deque[dict[str, object]] = deque(maxlen=8)
        self.daily.write('service_started')

    def status(self) -> dict[str, object]:
        """返回可跨进程发送的状态副本。

        Returns:
            实际记录状态、积压与失败信息，不把已暂停说成完整保存。
        """
        with self._lock:
            return {'at_monotonic_ns': time.monotonic_ns(), 'state': self._state, 'reason': self._reason, 'session': self._session,
                    'pending_frames': self._pending_frames, 'pending_bytes': self._pending_bytes,
                    'memory_budget_bytes': self.limits.memory, 'disk_budget_bytes': self.limits.disk,
                    'first_frame': self._session_start, 'last_observed_frame': self.frame_id,
                    'saved_frames': self._saved, 'first_missing_frame': self._first_missing,
                    'dropped_frames': self._dropped_frames,
                    'recent_gaps': [dict(gap) for gap in self._recent_gaps],
                    'daily_log_error': self.daily.failure}

    def _emit(self) -> None:
        """线程仅发送纯数据，通知失败不向识别链路抛出。"""
        if self._notify is not None:
            try:
                self._notify(self.status())
            except Exception:
                pass

    def set_enabled(self, enabled: bool) -> None:
        """开启单线程或请求关闭；旧线程收尾时拒绝重复创建。

        Args:
            enabled: 用户开关。
        Raises:
            TypeError: 开关不是bool。
            RuntimeError: 上次会话仍在收尾，或线程无法启动。
        """
        if type(enabled) is not bool:
            raise TypeError('完整诊断开关必须是bool')
        if not enabled:
            if self.stop(.25):
                with self._lock:
                    self._state = 'off'
                self._emit()
            return
        if self._thread is not None and self._thread.is_alive():
            if self._state in ('starting', 'recording', 'backpressure'):
                return
            raise RuntimeError('上次诊断仍在收尾，请稍后重新开启')
        with self._lock:
            self._state, self._reason = 'starting', ''
            self._first_missing = None
            self._dropped_frames = 0
            self._pending_gaps.clear()
            self._recent_gaps.clear()
            self._resume_required = 65536
            self._saved = 0
            self._session_start = self.frame_id+1
            self._session = 'session_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+uuid4().hex[:8]
        self._stop = Event()
        self._thread = Thread(target=self._run, name='HanDiagnosticWriter', daemon=True)
        try:
            self._thread.start()
        except Exception:
            with self._lock:
                self._state, self._reason = 'paused', '后台诊断线程无法启动'
            raise
        self._emit()

    def begin_frame(self, context: dict[str, object]) -> DiagnosticFrame | None:
        """每次检测都编号；仅实际记录时预留有界快照名额。

        Args:
            context: 当前配置、请求编号与generation。
        Returns:
            可记录帧；关闭或积压时为None。
        """
        self.frame_id += 1
        with self._lock:
            if self._state == 'backpressure':
                self._note_gap_locked(self.frame_id, self._reason)
                return None
            if self._state not in ('starting', 'recording'):
                return None
            memory_full = self._pending_bytes+65536 > self.limits.memory
            full = self._pending_frames >= self.limits.frames or memory_full
            if not full:
                self._pending_frames += 1
                self._pending_bytes += 65536
        if full:
            reason = 'memory_limit: 诊断快照达到内存预算，识别继续' if memory_full else 'queue_full: 保存队列积压，识别继续，详细记录已暂停'
            self._defer(reason, self.frame_id, 65536)
            return None
        return DiagnosticFrame(self, self.frame_id, context)

    def claim(self, frame: DiagnosticFrame, size: int) -> bool:
        """在复制图像前预留预算，包括后台正在写的一帧。

        Args:
            frame: 尚未提交的当前帧。
            size: 计划新增字节。
        Returns:
            是否允许继续构建。
        """
        with self._lock:
            active = self._state in ('starting', 'recording')
            good = active and self._pending_bytes+size <= self.limits.memory
            if good:
                self._pending_bytes += size
                frame.reserved += size
        if not good and active:
            reason = 'memory_limit: 诊断快照达到内存预算，识别继续'
            required = frame.reserved+size
            if required > self.limits.memory:
                self.pause('frame_too_large: 单个诊断帧超过内存预算，识别继续', cast(int, frame.payload['frame_id']))
            else:
                self._defer(reason, cast(int, frame.payload['frame_id']), required)
        return good

    def submit(self, frame: DiagnosticFrame, data: bytes) -> None:
        """转移副本所有权给后台，绝不等待磁盘。

        Args:
            frame: 已预留名额并冻结的帧。
            data: 不再修改的JSON。
        """
        with self._lock:
            accepted = self._state in ('starting', 'recording', 'backpressure')
            if accepted:
                self._queue.put_nowait((data, dict(frame.images), frame.reserved))
                frame.images.clear()
                frame.cancelled = True
        if not accepted:
            frame.cancel()

    def _note_gap_locked(self, number: int, reason: str) -> None:
        """在状态锁内记录连续缺帧区间，后台负责写入会话索引。

        Args:
            number: 本次未完整接收的检测帧号。
            reason: 队列或内存压力原因。
        """
        self._dropped_frames += 1
        self._first_missing = number if self._first_missing is None else min(self._first_missing, number)
        if self._pending_gaps and self._pending_gaps[-1]['last_frame'] == number-1:
            self._pending_gaps[-1]['last_frame'] = number
        else:
            if len(self._pending_gaps) >= 128:
                self._state, self._reason = 'paused', 'gap_metadata_limit: 缺帧索引无法及时写入，请处理保存故障后重开'
                self._stop.set()
                return
            gap: dict[str, object] = {'first_frame': number, 'last_frame': number, 'reason': reason}
            self._pending_gaps.append(gap)
            self._recent_gaps.append(gap)

    def _defer(self, reason: str, number: int, required: int) -> None:
        """只暂缓诊断采样；不停止写者或等待磁盘，识别照常继续。

        Args:
            reason: 压力原因。
            number: 首个被跳过的帧号。
            required: 恢复后需能容纳的一整帧估计字节。
        """
        with self._lock:
            if self._state not in ('starting', 'recording', 'backpressure'):
                return
            self._state, self._reason = 'backpressure', reason
            self._resume_required = required
            self._note_gap_locked(number, reason)
        self.daily.write('diagnostics_backpressure', reason=reason, first_skipped_frame=number)
        self._emit()
        self._try_resume()

    def _flush_gaps(self, storage: SessionStorage) -> None:
        """仅后台调用，持久化区间后释放元数据，不积累整个会话的缺帧列表。

        Args:
            storage: 当前会话存储器。
        Raises:
            OSError: 区间索引写入失败，转为需要用户处理的诊断故障。
        """
        with self._lock:
            gaps = [dict(gap) for gap in self._pending_gaps]
            self._pending_gaps.clear()
        for gap in gaps:
            storage.write_gap(gap)

    def release(self, size: int) -> None:
        """一帧完成或取消后释放名额及预算。

        Args:
            size: 此帧全部预留字节。
        """
        with self._lock:
            self._pending_bytes -= size
            self._pending_frames -= 1
        self._try_resume()

    def _try_resume(self) -> None:
        """低水位且有足够字节余量时恢复，兼顾写者释放与生产者检查间的竞态。"""
        with self._lock:
            resumed = (self._state == 'backpressure' and self._pending_frames <= self.limits.frames//2
                       and self._pending_bytes+self._resume_required <= self.limits.memory)
            if resumed:
                self._state, self._reason = 'recording', ''
        if resumed:
            self.daily.write('diagnostics_resumed', dropped_frames=self.status()['dropped_frames'])
            self._emit()

    def pause(self, reason: str, missing: int | None = None) -> None:
        """诊断故障只暂停诊断，明确标记不完整，不停止记牌。

        Args:
            reason: 失败或积压原因。
            missing: 首个未完整保存的帧号。
        """
        with self._lock:
            self._state, self._reason = 'paused', reason
            if missing is not None:
                self._first_missing = missing if self._first_missing is None else min(self._first_missing, missing)
        self._stop.set()
        self.daily.write('diagnostics_paused', reason=reason, first_missing_frame=missing)
        self._emit()

    def stop(self, timeout: float = 1.) -> bool:
        """有界等待后台收尾；超时不假报完整，也不无限卡住设置或退出。

        Args:
            timeout: 最大等待秒数。
        Returns:
            线程是否已经结束。
        """
        self._stop.set()
        if self._thread is None or not self._thread.is_alive():
            return True
        with self._lock:
            if self._state in ('starting', 'recording', 'backpressure'):
                self._state = 'stopping'
        self._thread.join(timeout)
        self._emit()
        return not self._thread.is_alive()

    def close(self) -> None:
        """进程退出前最多等待一秒；未完成会话保留非完整标记。"""
        complete = self.stop(1.)
        self.daily.write('service_stopped', diagnostic_writer_finished=complete)
        self.daily.close()

    def _run(self) -> None:
        """唯一后台写者，负责编码、配额、清理、标注及会话完成度。"""
        storage: SessionStorage | None = None
        failed = False
        try:
            storage = SessionStorage(self.base/'diagnostics', self._session, self.limits.disk)
            with self._lock:
                if self._state == 'starting':
                    self._state = 'recording'
            self._emit()
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    data, images, reserved = self._queue.get(timeout=.05)
                except Empty:
                    if not failed:
                        self._flush_gaps(storage)
                    continue
                try:
                    if not failed:
                        storage.write_frame(data, images)
                        with self._lock:
                            self._saved += 1
                except Exception:
                    failed = True
                    self.pause('write_failed: '+traceback.format_exc(), json.loads(data)['frame_id'])
                finally:
                    for image in images.values():
                        image.close()
                    self.release(reserved)
                    self._queue.task_done()
                if not failed:
                    self._flush_gaps(storage)
            if not failed:
                self._flush_gaps(storage)
            status = self.status()
            storage.manifest({**status, 'state': 'paused' if status['state'] == 'paused' else 'closed',
                              'complete': status['state'] != 'paused' and status['pending_frames'] == 0 and status['dropped_frames'] == 0})
        except Exception:
            self.pause('writer_failed: '+traceback.format_exc())
        finally:
            # 初始化失败也必须释放已接受的快照。
            while True:
                try:
                    _, images, reserved = self._queue.get_nowait()
                except Empty:
                    break
                for image in images.values():
                    image.close()
                self.release(reserved)
                self._queue.task_done()
            with self._lock:
                if self._state != 'paused':
                    self._state = 'off'
            self._emit()
