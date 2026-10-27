"""真实 Windows spawn 与进程入口错误回传测试。"""

import multiprocessing as mp
import queue
import unittest
from multiprocessing.queues import Queue
from unittest.mock import Mock, patch

from config import settings
from core.controller_process import run_controller_loop
from core.inference_protocol import Command


def diagnostic_child(directory: str, results: Queue) -> None:
    """在真实spawn子进程内启停唯一诊断保存线程。

    Args:
        directory: 测试独立输出目录。
        results: 向父进程回传纯数据。
    """
    from threading import enumerate as enumerate_threads
    from PIL import Image
    from core.diagnostics import DiagnosticRecorder
    recorder = DiagnosticRecorder(directory)
    recorder.set_enabled(True)
    writers = sum(t.name == 'HanDiagnosticWriter' for t in enumerate_threads())
    frame = recorder.begin_frame({'request_id': 1, 'generation': 0})
    assert frame is not None
    frame.image('original', Image.new('RGB', (32, 24)))
    frame.finish({})
    recorder.close()
    results.put({'writers': writers, 'status': recorder.status(),
                 'remaining_writers': sum(t.name == 'HanDiagnosticWriter' for t in enumerate_threads())})


class ProcessTests(unittest.TestCase):
    """覆盖进程级消息传递与可变结果复制。"""

    def setUp(self) -> None:
        """所有进程入口单元测试的日志写入隔离临时目录。"""
        import tempfile
        from pathlib import Path
        Path('tests/.tmp').mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir='tests/.tmp')
        self.addCleanup(temporary.cleanup)
        self.log_base = str(Path(temporary.name).resolve())
        self.enterContext(patch.object(settings, 'BASE_DIR', str(Path(temporary.name).resolve())))

    def test_spawn_full_diagnostics_writer_closes_without_orphan(self) -> None:
        """真实Windows子进程内单线程成功写入、退出并释放，非仅模拟测试。"""
        context = mp.get_context('spawn')
        results = context.Queue()
        process = context.Process(target=diagnostic_child, args=(self.log_base, results))
        process.start()
        try:
            payload = results.get(timeout=15)
            process.join(timeout=5)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(payload['writers'], 1)
            self.assertEqual(payload['remaining_writers'], 0)
            self.assertEqual(payload['status']['saved_frames'], 1)
            self.assertEqual(payload['status']['pending_bytes'], 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            process.close()
            results.cancel_join_thread()
            results.close()

    def test_spawn_returns_startup_error_then_exits_cleanly(self) -> None:
        """真实子进程对无效启动配置回传原始错误并退出。"""
        context = mp.get_context("spawn")
        commands, results = context.Queue(), context.Queue()
        process = context.Process(
            target=run_controller_loop,
            args=(commands, results, settings.CURRENT_LAYOUT, "doudizhu", {"FRAME_LENGTH": 0}),
        )
        try:
            process.start()
            response = results.get(timeout=15)
            self.assertEqual(response.status, "fatal")
            self.assertIn("frame_length", response.error)
            process.join(timeout=5)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            process.close()
            for channel in (commands, results):
                channel.cancel_join_thread()
                channel.close()

    def test_detect_response_is_snapshot_before_next_command(self) -> None:
        """后续重置修改可变对象时，之前的检测回执仍保持快照。"""
        commands, results = queue.Queue(), queue.Queue()
        commands.put(Command(1, 0, "detect"))
        commands.put(Command(2, 1, "reset"))
        commands.put(None)
        remain = {"A": 1}
        zones = {"left": [["A"]]}
        controller = Mock()
        metrics = {"pipeline_ms": 15.}
        controller.last_performance = metrics
        controller.detect.return_value = (remain, zones, 2.0)
        controller.execute_command.side_effect = lambda *args: (remain.clear(), zones.clear(), metrics.clear())
        with patch("core.game_controller.GameController", return_value=controller):
            run_controller_loop(commands, results, settings.CURRENT_LAYOUT, "doudizhu", {})
        self.assertEqual(results.get_nowait().status, "ready")
        response = results.get_nowait()
        self.assertEqual(response.remain_cards, {"A": 1})
        self.assertEqual(response.zone_cards, {"left": [["A"]]})
        self.assertEqual(response.performance, {"pipeline_ms": 15.})
        self.assertEqual(results.get_nowait().request_id, 2)


if __name__ == "__main__":
    unittest.main()
