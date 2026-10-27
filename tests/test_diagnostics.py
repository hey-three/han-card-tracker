"""诊断的限量、快照、异常隔离及磁盘生命周期回归，不加载权重。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from config import settings
from core.diagnostics import DiagnosticFrame, DiagnosticLimits, DiagnosticRecorder
from core.diagnostic_storage import DailyLog, SessionStorage, StorageLimitError
from core.diagnostic_state import tracker_snapshot, decision_reasons
from core.frame_packing import Detection
from core.games.doudizhu import DoudizhuTracker
from core.packed_pipeline import PackedPipeline
from test_packed_pipeline import raw_result


class DiagnosticTests(unittest.TestCase):
    """真实后台线程和隔离目录覆盖保存协议与异常边界。"""

    def setUp(self) -> None:
        """建立工作区内独立目录；线程必须先于目录销毁。"""
        Path('tests/.tmp').mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir='tests/.tmp')
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()

    def recorder(self, limits: DiagnosticLimits | None = None) -> DiagnosticRecorder:
        """创建会被测试自动关闭的服务。

        Args:
            limits: 可选测试限额。
        Returns:
            新服务，默认未开启完整诊断。
        """
        recorder = DiagnosticRecorder(self.base, limits=limits)
        self.addCleanup(recorder.close)
        return recorder

    def frame(self, recorder: DiagnosticRecorder) -> DiagnosticFrame:
        """申请当前会话下一帧并断言未被拒绝。

        Args:
            recorder: 已开启的记录器。
        Returns:
            已预留容量的帧。
        """
        frame = recorder.begin_frame({'request_id': 7, 'generation': 2})
        self.assertIsNotNone(frame)
        assert frame is not None
        return frame

    def saved(self) -> tuple[dict[str, object], Path]:
        """读取唯一已发布的帧，隐藏的pending目录不算成功。

        Returns:
            帧元数据与所属帧目录。
        """
        paths = list((self.base/'diagnostics').glob('session_*/frames/*/frame.json'))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text(encoding='utf-8')), paths[0].parent

    def test_off_has_no_writer_no_frame_and_logs_rotate(self) -> None:
        """关闭不申请图片/线程；日常日志持续运行且只保留五份。"""
        recorder = self.recorder()
        self.assertIsNone(recorder.begin_frame({}))
        self.assertIsNone(recorder._thread)
        self.assertFalse((self.base/'diagnostics').exists())
        self.assertEqual(recorder.frame_id, 1)
        daily = DailyLog(self.base/'rotation', max_bytes=1024)
        self.addCleanup(daily.close)
        for index in range(50):
            daily.write('changed', index=index, reason='变化'*40)
        daily.close()
        paths = list((self.base/'rotation'/'logs').glob('runtime.jsonl*'))
        self.assertEqual(len(paths), 5)
        self.assertLessEqual(sum(p.stat().st_size for p in paths), 5*1024)
        for path in paths:
            for line in path.read_text(encoding='utf-8').splitlines():
                self.assertEqual(json.loads(line)['event'], 'changed')
        daily.write('must_not_reopen')
        self.assertTrue(daily._closed)

    def test_input_and_json_are_copied_before_caller_mutates(self) -> None:
        """复用画布与后续状态变更不能污染已提交的这一帧。"""
        recorder = self.recorder()
        recorder.set_enabled(True)
        frame = self.frame(recorder)
        image = Image.new('RGB', (40, 20), 'red')
        frame.image('original', image)
        index = frame.begin_call('full', image, None)
        frame.end_call(index, raw_result([Detection('A', (2., 2., 12., 16.), .95)]), 3.)
        remain = {'A': 3}
        frame.finish({'after': {'remain_cards': remain}, 'final_boxes': frame.calls[0]['boxes']})
        image.paste('blue', (0, 0, 40, 20))
        remain['A'] = 0
        self.assertTrue(recorder.stop(2))
        data, directory = self.saved()
        self.assertEqual(data['context'], {'request_id': 7, 'generation': 2})
        self.assertEqual(data['after'], {'remain_cards': {'A': 3}})
        with Image.open(directory/'call_01_input.png') as frozen:
            self.assertEqual(frozen.getpixel((0, 0)), (255, 0, 0))
        self.assertTrue((directory/'call_01_input_annotated.png').exists())
        self.assertTrue((directory/'final_original_annotated.png').exists())
        manifest = json.loads((directory.parent.parent/'manifest.json').read_text(encoding='utf-8'))
        self.assertTrue(manifest['complete'])
        self.assertEqual(recorder.status()['pending_bytes'], 0)

    def test_failed_packed_call_and_full_fallback_are_both_recorded(self) -> None:
        """首个模型调用异常仍保留输入和异常，后续整图调用有独立结果。"""
        recorder = self.recorder()
        recorder.set_enabled(True)
        frame = self.frame(recorder)
        pipeline = PackedPipeline()
        detector = Mock()
        detector.detect_packed.side_effect = RuntimeError('bad packed shape')
        detector.detect.return_value = (raw_result([]), 9.)
        image = Image.new('RGB', (80, 40))
        with self.assertRaisesRegex(RuntimeError, 'bad packed shape'):
            pipeline._model_call(detector, image, True, frame)
        pipeline._model_call(detector, image, False, frame)
        frame.finish({})
        self.assertTrue(recorder.stop(2))
        data, directory = self.saved()
        self.assertEqual([c['kind'] for c in data['calls']], ['packed', 'full'])
        self.assertIn('bad packed shape', data['calls'][0]['error'])
        self.assertFalse(data['calls'][0]['result_known'])
        self.assertTrue(data['calls'][1]['result_known'])
        self.assertTrue((directory/'call_02_input_annotated.png').exists())

    def test_inflight_write_counts_toward_capacity_and_stop_is_bounded(self) -> None:
        """正在落盘的帧仍占名额；慢磁盘不使生产者等待。"""
        recorder = self.recorder(DiagnosticLimits(frames=1))
        entered, release = Event(), Event()
        original = SessionStorage.write_frame

        def delayed(storage: SessionStorage, data: bytes, images: dict[str, Image.Image]) -> None:
            """用同步屏障模拟慢磁盘，而非依赖长sleep。

            Args:
                storage: 当前存储器。
                data: 冻结数据。
                images: 冻结图片。
            """
            entered.set()
            release.wait(2)
            original(storage, data, images)

        with patch.object(SessionStorage, 'write_frame', delayed):
            recorder.set_enabled(True)
            frame = self.frame(recorder)
            frame.finish({})
            self.assertTrue(entered.wait(2))
            started = time.perf_counter()
            self.assertIsNone(recorder.begin_frame({}))
            self.assertLess(time.perf_counter()-started, .25)
            self.assertEqual(recorder.status()['state'], 'backpressure')
            self.assertEqual(recorder.status()['first_missing_frame'], 2)
            self.assertFalse(recorder.stop(.001))
            with self.assertRaisesRegex(RuntimeError, '收尾'):
                recorder.set_enabled(True)
            manifest = next((self.base/'diagnostics').glob('session_*/manifest.json'))
            self.assertFalse(json.loads(manifest.read_text(encoding='utf-8'))['complete'])
            release.set()
            self.assertTrue(recorder.stop(2))
        self.assertFalse(json.loads(manifest.read_text(encoding='utf-8'))['complete'])
        self.assertEqual(recorder.status()['pending_frames'], 0)

    def test_image_budget_checked_before_copy_and_paused_frame_cannot_submit(self) -> None:
        """拒绝超预算图片；故障后的活动帧不能滞留到已退出线程的队列。"""
        recorder = self.recorder(DiagnosticLimits(memory=65536+10))
        recorder.set_enabled(True)
        frame = self.frame(recorder)
        image = Image.new('RGB', (20, 20))
        with patch.object(image, 'copy', side_effect=AssertionError('must not copy')):
            frame.image('original', image)
        self.assertTrue(frame.cancelled)
        self.assertEqual(recorder.status()['pending_bytes'], 0)
        self.assertEqual(recorder.status()['first_missing_frame'], 1)
        self.assertTrue(recorder.stop(2))
        recorder.set_enabled(False)
        recorder.set_enabled(True)
        second = self.frame(recorder)
        recorder.pause('disk failed', 2)
        second.finish({})
        self.assertTrue(recorder.stop(2))
        self.assertEqual(recorder.status()['pending_frames'], 0)
        self.assertTrue(recorder._queue.empty())

    def test_disk_error_is_reported_and_switch_can_restart_new_session(self) -> None:
        """磁盘错误只暂停诊断，释放队列内存；重新开启创建新会话。"""
        recorder = self.recorder()
        with patch.object(SessionStorage, 'write_frame', side_effect=OSError('disk full')):
            recorder.set_enabled(True)
            frame = self.frame(recorder)
            old = recorder.status()['session']
            frame.finish({})
            self.assertTrue(recorder.stop(2))
        self.assertEqual(recorder.status()['state'], 'paused')
        self.assertIn('disk full', recorder.status()['reason'])
        self.assertEqual(recorder.status()['pending_bytes'], 0)
        recorder.set_enabled(False)
        recorder.set_enabled(True)
        self.assertNotEqual(recorder.status()['session'], old)
        self.frame(recorder).finish({})
        self.assertTrue(recorder.stop(2))
        self.assertEqual(recorder.status()['saved_frames'], 1)

    def test_storage_init_failure_does_not_throw_into_producer(self) -> None:
        """诊断目录不可写时通过状态报告，识别调用方不收到磁盘异常。"""
        recorder = self.recorder()
        with patch('core.diagnostics.SessionStorage', side_effect=OSError('permission denied')):
            recorder.set_enabled(True)
            self.assertTrue(recorder.stop(2))
        self.assertEqual(recorder.status()['state'], 'paused')
        self.assertIn('permission denied', recorder.status()['reason'])

    def test_quota_removes_only_closed_owned_sessions(self) -> None:
        """仅清理本工具已封口的旧会话，保护崩溃材料及未知目录。"""
        root = self.base/'diagnostics'
        for name, size, state in [('session_001', 12000, 'closed'), ('session_crashed', 2000, 'recording'), ('unknown', 2000, '')]:
            directory = root/name
            directory.mkdir(parents=True)
            (directory/'evidence').write_bytes(b'x'*size)
            if state:
                (directory/'manifest.json').write_text(json.dumps({'schema': 'han-diagnostics/v1', 'state': state}), encoding='utf-8')
        storage = SessionStorage(root, 'session_999', 20000)
        self.assertFalse((root/'session_001').exists())
        self.assertTrue((root/'session_crashed'/'evidence').exists())
        self.assertTrue((root/'unknown'/'evidence').exists())
        self.assertFalse(json.loads((storage.directory/'manifest.json').read_text(encoding='utf-8'))['complete'])
        with self.assertRaises(StorageLimitError):
            SessionStorage(root, 'session_1000', 6000)

    def test_invalid_or_oversized_metadata_pauses_and_releases(self) -> None:
        """不可序列化状态和超限单帧不能隐性丢失或一直占内存。"""
        recorder = self.recorder()
        recorder.set_enabled(True)
        self.frame(recorder).finish({'invalid': object()})
        self.assertTrue(recorder.stop(2))
        self.assertEqual(recorder.status()['state'], 'paused')
        self.assertEqual(recorder.status()['pending_frames'], 0)
        recorder.set_enabled(False)
        recorder.set_enabled(True)
        self.frame(recorder).finish({'large': 'x'*(256*1024)})
        self.assertTrue(recorder.stop(2))
        self.assertIn('256KiB', recorder.status()['reason'])

    def test_tracker_snapshot_is_independent_and_reasons_follow_confirmation(self) -> None:
        """诊断是状态机的只读观察，区分缺帧、底牌门禁和真正扣牌。"""
        tracker = DoudizhuTracker()
        before = tracker_snapshot(tracker)
        tracker.remain_cards['A'] -= 1
        after = tracker_snapshot(tracker)
        self.assertEqual(before['remain_cards']['A'], 4)
        reasons = decision_reasons(before, after, {'landlord_cards': [], 'player_hand': ['A']}, ['landlord_cards', 'player_hand'], True, 3)
        self.assertIn('bottom_missing_pause', [r['code'] for r in reasons])
        self.assertIn('remaining_changed', [r['code'] for r in reasons])
        self.assertEqual(decision_reasons(before, after, {}, [], False, 3), [{'code': 'result_unknown'}])

    def test_limits_reject_bool_zero_and_nonintegers(self) -> None:
        """内存/磁盘/名额必须为正整数，避免负容量隐藏无界行为。"""
        for value in (False, 1.5, '8'):
            with self.assertRaises(TypeError):
                DiagnosticLimits(frames=value)
        with self.assertRaises(ValueError):
            DiagnosticLimits(frames=0)

    def test_observer_failure_preserves_model_return_and_original_exception(self) -> None:
        """记录结果失败不能改变模型输出，也不能掩盖原始模型异常。"""
        recorder = self.recorder()
        recorder.set_enabled(True)
        frame = self.frame(recorder)
        pipeline, detector = PackedPipeline(), Mock()
        result = raw_result([])
        detector.detect.return_value = (result, 1.)
        with patch.object(frame, 'end_call', side_effect=ValueError('observer failed')):
            returned, elapsed = pipeline._model_call(detector, Image.new('RGB', (10, 10)), False, frame)
        self.assertIs(returned, result)
        self.assertEqual(elapsed, 1.)
        self.assertEqual(recorder.status()['state'], 'paused')
        self.assertTrue(recorder.stop(2))
        recorder.set_enabled(False)
        recorder.set_enabled(True)
        second = self.frame(recorder)
        detector.detect.side_effect = RuntimeError('original model failure')
        with patch.object(second, 'end_call', side_effect=ValueError('observer failed')):
            with self.assertRaisesRegex(RuntimeError, 'original model failure'):
                pipeline._model_call(detector, Image.new('RGB', (10, 10)), False, second)

    def test_paused_submit_preserves_real_failure_and_earliest_missing_frame(self) -> None:
        """积压后更早的保存失败更新缺口，取消活动快照不谎报内存超限。"""
        recorder = self.recorder()
        recorder.set_enabled(True)
        frame = self.frame(recorder)
        recorder.pause('queue_full', 2)
        recorder.pause('write_failed', 1)
        frame.finish({})
        self.assertTrue(recorder.stop(2))
        self.assertEqual(recorder.status()['first_missing_frame'], 1)
        self.assertEqual(recorder.status()['reason'], 'write_failed')
        self.assertEqual(recorder.status()['pending_frames'], 0)

    def test_default_budget_is_64_mib_eight_frames_and_one_gib_disk(self) -> None:
        """保留64MiB内存与8帧限制，磁盘1GiB是独立限额。"""
        recorder = self.recorder()
        self.assertEqual(recorder.limits.memory, 64*1024*1024)
        self.assertEqual(recorder.limits.frames, 8)
        self.assertEqual(recorder.limits.disk, 1024**3)
        recorder.set_enabled(True)
        frames = [self.frame(recorder) for _ in range(8)]
        self.assertTrue(recorder.claim(frames[0], 32*1024*1024))
        self.assertLess(recorder.status()['pending_bytes'], 64*1024*1024)
        self.assertIsNone(recorder.begin_frame({}))
        self.assertIn('queue_full', recorder.status()['reason'])
        for frame in frames:
            frame.cancel()
        self.assertEqual(recorder.status()['pending_bytes'], 0)
        self.assertTrue(recorder.stop(2))

    def test_slow_writer_recovers_automatically_and_persists_gap_ranges(self) -> None:
        """队列满时跳过诊断，写者排空后自动接收新帧，完整保留缺口索引。"""
        recorder = self.recorder(DiagnosticLimits(frames=1))
        entered, release, resumed = Event(), Event(), Event()
        original = SessionStorage.write_frame

        def notify(status: dict[str, object]) -> None:
            """观察自动恢复，不向保存线程传Qt对象。

            Args:
                status: 状态快照。
            """
            if status['state'] == 'recording' and status['dropped_frames'] == 2:
                resumed.set()

        def delayed(storage: SessionStorage, data: bytes, images: dict[str, Image.Image]) -> None:
            """仅阻塞第一帧写入，允许测试在后台积压时继续生产。

            Args:
                storage: 当前会话。
                data: 当前帧JSON。
                images: 当前帧图片。
            """
            if json.loads(data)['frame_id'] == 1:
                entered.set()
                release.wait(2)
            original(storage, data, images)

        recorder._notify = notify
        with patch.object(SessionStorage, 'write_frame', delayed):
            recorder.set_enabled(True)
            self.frame(recorder).finish({})
            self.assertTrue(entered.wait(2))
            self.assertIsNone(recorder.begin_frame({}))
            self.assertIsNone(recorder.begin_frame({}))
            self.assertEqual(recorder.status()['state'], 'backpressure')
            self.assertFalse(recorder._stop.is_set())
            release.set()
            self.assertTrue(resumed.wait(2))
            self.frame(recorder).finish({})
            self.assertTrue(recorder.stop(2))
        paths = list((self.base/'diagnostics').glob('session_*/manifest.json'))
        self.assertEqual(len(paths), 1)
        manifest = json.loads(paths[0].read_text(encoding='utf-8'))
        self.assertFalse(manifest['complete'])
        self.assertEqual(manifest['dropped_frames'], 2)
        self.assertEqual(manifest['saved_frames'], 2)
        events = [json.loads(line) for line in (paths[0].parent/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([e['frame_id'] for e in events if 'frame_id' in e], [1, 4])
        gaps = [e for e in events if e.get('record_type') == 'gap']
        self.assertEqual([(g['first_frame'], g['last_frame']) for g in gaps], [(2, 3)])

    def test_memory_backpressure_waits_for_enough_bytes_then_recovers(self) -> None:
        """内存先满时取消半成品，直到可容纳整帧才恢复，字节数不会超预算。"""
        recorder = self.recorder(DiagnosticLimits(memory=3*65536))
        recorder.set_enabled(True)
        first = self.frame(recorder)
        self.assertTrue(recorder.claim(first, 65536))
        second = self.frame(recorder)
        self.assertFalse(recorder.claim(second, 1))
        second.cancel()
        self.assertEqual(recorder.status()['state'], 'backpressure')
        self.assertLessEqual(recorder.status()['pending_bytes'], recorder.limits.memory)
        first.cancel()
        self.assertEqual(recorder.status()['state'], 'recording')
        self.frame(recorder).finish({})
        self.assertTrue(recorder.stop(2))
        self.assertEqual(recorder.status()['dropped_frames'], 1)

    def test_low_water_hysteresis_keeps_sampling_deferred_until_half_queue(self) -> None:
        """8帧满后降到7帧仍暂缓，降到4帧才恢复，避免每帧频繁开关。"""
        recorder = self.recorder()
        recorder.set_enabled(True)
        frames = [self.frame(recorder) for _ in range(8)]
        self.assertIsNone(recorder.begin_frame({}))
        frames[0].cancel()
        self.assertEqual(recorder.status()['state'], 'backpressure')
        for frame in frames[1:4]:
            frame.cancel()
        self.assertEqual(recorder.status()['state'], 'recording')
        for frame in frames[4:]:
            frame.cancel()
        self.assertTrue(recorder.stop(2))
