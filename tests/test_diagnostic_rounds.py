"""按局落盘、跨局排队与可调整总磁盘配额的独立回归。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from threading import Event, current_thread
import unittest
from unittest.mock import patch

from PIL import Image

from core.diagnostic_storage import SessionStorage, StorageLimitError
from core.diagnostics import DiagnosticLimits, DiagnosticRecorder


class DiagnosticRoundTests(unittest.TestCase):
    """真实文件与后台线程测试，不调用模型或用户诊断目录。"""

    def setUp(self) -> None:
        """使用工作区内临时目录，在线程结束后清理。"""
        Path('tests/.tmp').mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir='tests/.tmp')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()

    def payload(self, frame: int, round_id: object, padding: int = 0) -> bytes:
        """冻结最小可写帧。

        Args:
            frame: 检测帧号。
            round_id: 局号，允许测试非法输入。
            padding: 模拟磁盘负载的JSON字符数量。
        Returns:
            CPU JSON字节。
        """
        return json.dumps({'frame_id': frame, 'round_id': round_id, 'calls': [], 'padding': 'x'*padding,
                           'before': {'state': 0}, 'after': {'state': 1}}).encode('utf-8')

    def test_waiting_and_each_new_round_have_separate_folders(self) -> None:
        """开局前独立存放，首帧进新局，同局连续帧和下一局不能混目录。"""
        storage = SessionStorage(self.base/'diagnostics', 'session_test', 100000)
        for number, round_id in enumerate((0, 0, 1, 1, 2), 1):
            storage.write_frame(self.payload(number, round_id), {})
        storage.finish_round()
        expected = {'waiting': [1, 2], 'game_0001': [3, 4], 'game_0002': [5]}
        for name, numbers in expected.items():
            directory = storage.directory/name
            actual = sorted(int(path.name) for path in (directory/'frames').iterdir())
            self.assertEqual(actual, numbers)
            info = json.loads((directory/'round.json').read_text(encoding='utf-8'))
            self.assertEqual(info['state'], 'closed')
            self.assertEqual(info['saved_frames'], len(numbers))
        self.assertFalse((storage.directory/'frames').exists())
        records = [json.loads(line) for line in (storage.directory/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(records[2]['path'], 'game_0001/frames/00000003/frame.json')

    def test_queue_uses_each_frozen_frames_round_instead_of_latest_game(self) -> None:
        """旧局尚在写时新局帧已入队，也必须按各自冻结局号保存。"""
        recorder = DiagnosticRecorder(self.base)
        self.addCleanup(recorder.close)
        entered, release = Event(), Event()
        original = SessionStorage.write_frame

        def delayed(storage: SessionStorage, data: bytes, images: dict[str, Image.Image]) -> None:
            """阻塞第一帧，使旧新两局在队列中同时存在。

            Args:
                storage: 当前会话存储。
                data: 冻结JSON。
                images: 冻结图片。
            """
            if json.loads(data)['frame_id'] == 1:
                entered.set()
                release.wait(2)
            original(storage, data, images)

        with patch.object(SessionStorage, 'write_frame', delayed):
            recorder.set_enabled(True)
            for round_id in (1, 1, 2):
                frame = recorder.begin_frame({})
                assert frame is not None
                frame.finish({'round_id': round_id})
            self.assertTrue(entered.wait(2))
            release.set()
            self.assertTrue(recorder.stop(2))
        session = next((self.base/'diagnostics').glob('session_*'))
        self.assertTrue((session/'game_0001/frames/00000001/frame.json').is_file())
        self.assertTrue((session/'game_0001/frames/00000002/frame.json').is_file())
        self.assertTrue((session/'game_0002/frames/00000003/frame.json').is_file())
        self.assertEqual(recorder.status()['saved_frames'], 3)

    def test_skipped_whole_round_keeps_folder_and_does_not_move_queued_old_frames(self) -> None:
        """整局无图片时保留目录标记，延迟写入的旧局图片仍在旧目录。"""
        recorder = DiagnosticRecorder(self.base)
        self.addCleanup(recorder.close)
        entered, release = Event(), Event()
        original = SessionStorage.write_frame

        def delayed(storage: SessionStorage, data: bytes, images: dict[str, Image.Image]) -> None:
            """阻塞首帧，模拟两次开局先于旧帧写盘完成。

            Args:
                storage: 存储器。
                data: 冻结帧。
                images: 图像副本。
            """
            if json.loads(data)['frame_id'] == 1:
                entered.set()
                release.wait(3)
            original(storage, data, images)

        with patch.object(SessionStorage, 'write_frame', delayed):
            recorder.set_enabled(True)
            first = recorder.begin_frame({})
            self.assertIsNotNone(first)
            recorder.note_round(1)
            first.finish({'round_id': 1})
            self.assertTrue(entered.wait(2))
            skipped = recorder.begin_frame({})
            self.assertIsNotNone(skipped)
            recorder.note_round(2)
            recorder._defer('queue_full', 2, 1024)
            skipped.cancel()
            third = recorder.begin_frame({})
            self.assertIsNotNone(third)
            recorder.note_round(3)
            third.finish({'round_id': 3})
            release.set()
            self.assertTrue(recorder.stop(3))
        session = next((self.base/'diagnostics').glob('session_*'))
        self.assertTrue((session/'game_0001/frames/00000001/frame.json').exists())
        self.assertTrue((session/'game_0003/frames/00000003/frame.json').exists())
        empty = json.loads((session/'game_0002/round.json').read_text(encoding='utf-8'))
        self.assertEqual(empty['saved_frames'], 0)
        self.assertTrue(empty['saw_start'])
        self.assertEqual(empty['state'], 'closed')
        records = [json.loads(line) for line in (session/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertTrue(any(line.get('record_type') == 'gap' and line['first_frame'] == 2 for line in records))

    def test_lower_quota_cleans_closed_round_inside_active_session(self) -> None:
        """不必等整个程序退出，活跃会话中已结束旧局即可按总额清理。"""
        storage = SessionStorage(self.base/'diagnostics', 'session_test', 100000)
        storage.write_frame(self.payload(1, 1, 40000), {})
        storage.write_frame(self.payload(2, 2, 1000), {})
        storage.set_quota(12000)
        self.assertFalse((storage.directory/'game_0001').exists())
        self.assertTrue((storage.directory/'game_0002/frames/00000002/frame.json').exists())
        self.assertEqual(storage.pruned_rounds, 1)
        self.assertLess(storage.used, 12000)
        events = [json.loads(line) for line in (storage.directory/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(events[-1], {'record_type': 'retention_cleanup', 'directory': 'game_0001'})

    def test_active_round_is_not_deleted_to_meet_lower_quota(self) -> None:
        """当前局已超新额度时拒绝继续，不能为满足上限删除当前证据。"""
        storage = SessionStorage(self.base/'diagnostics', 'session_test', 100000)
        storage.write_frame(self.payload(1, 1, 40000), {})
        with self.assertRaises(StorageLimitError):
            storage.set_quota(12000)
        self.assertTrue((storage.directory/'game_0001/frames/00000001/frame.json').exists())

    def test_quota_update_and_cleanup_run_in_writer_thread(self) -> None:
        """额度修改立即更新内存状态，文件扫描与清理由后台独立执行。"""
        recorder = DiagnosticRecorder(self.base, limits=DiagnosticLimits(disk=100000))
        self.addCleanup(recorder.close)
        checked = Event()
        original = SessionStorage.set_quota

        def apply(storage: SessionStorage, quota: int) -> None:
            """确认磁盘操作未跑到提交设置的线程。

            Args:
                storage: 当前存储器。
                quota: 新磁盘额度。
            """
            self.assertEqual(current_thread().name, 'HanDiagnosticWriter')
            original(storage, quota)
            checked.set()

        with patch.object(SessionStorage, 'set_quota', apply):
            recorder.set_enabled(True)
            for round_id, padding in ((1, 40000), (2, 1000)):
                frame = recorder.begin_frame({})
                assert frame is not None
                frame.finish({'round_id': round_id, 'padding': 'x'*padding})
                recorder._queue.join()
            recorder.set_disk_limit(12000)
            self.assertEqual(recorder.status()['disk_budget_bytes'], 12000)
            self.assertTrue(checked.wait(2))
            self.assertTrue(recorder.stop(2))
        session = next((self.base/'diagnostics').glob('session_*'))
        self.assertFalse((session/'game_0001').exists())
        self.assertTrue((session/'game_0002').exists())
        self.assertFalse(json.loads((session/'manifest.json').read_text(encoding='utf-8'))['complete'])

    def test_legacy_closed_sessions_are_still_eligible_but_unknown_rounds_are_protected(self) -> None:
        """新布局兼容清理旧会话，但不能删除未标记的未知局目录。"""
        root = self.base/'diagnostics'
        legacy = SessionStorage(root, 'session_001', 100000)
        (legacy.directory/'frames').mkdir()
        (legacy.directory/'frames/legacy.bin').write_bytes(b'x'*20000)
        legacy.manifest({'state': 'closed', 'complete': True})
        current = SessionStorage(root, 'session_002', 100000)
        current.write_frame(self.payload(1, 1), {})
        unknown = current.directory/'game_unmanaged'
        unknown.mkdir()
        (unknown/'evidence').write_bytes(b'x'*10000)
        current.set_quota(20000)
        self.assertFalse(legacy.directory.exists())
        self.assertTrue((unknown/'evidence').exists())

    def test_invalid_or_backward_round_ids_are_rejected_without_overwriting(self) -> None:
        """局号不能构造任意路径，也不能倒退覆盖已结束旧局。"""
        storage = SessionStorage(self.base/'diagnostics', 'session_test', 100000)
        for invalid in (True, '../outside', 1.5):
            with self.assertRaises(TypeError):
                storage.write_frame(self.payload(1, invalid), {})
        with self.assertRaises(ValueError):
            storage.write_frame(self.payload(1, -1), {})
        storage.write_frame(self.payload(1, 2), {})
        with self.assertRaises(ValueError):
            storage.write_frame(self.payload(2, 1), {})
        self.assertTrue((storage.directory/'game_0002/frames/00000001/frame.json').exists())

    def test_quota_validates_type_and_positive_bytes(self) -> None:
        """磁盘额度API拒绝布尔值、字符串及非正值。"""
        recorder = DiagnosticRecorder(self.base)
        self.addCleanup(recorder.close)
        for bad in (False, '1024', 1.5):
            with self.assertRaises(TypeError):
                recorder.set_disk_limit(bad)
        with self.assertRaises(ValueError):
            recorder.set_disk_limit(0)
