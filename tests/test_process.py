"""真实 Windows spawn 与进程入口错误回传测试。"""

import multiprocessing as mp
import queue
import unittest
from unittest.mock import Mock, patch

from config import settings
from core.controller_process import run_controller_loop
from core.inference_protocol import Command


class ProcessTests(unittest.TestCase):
    """覆盖进程级消息传递与可变结果复制。"""

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
        controller.detect.return_value = (remain, zones, 2.0)
        controller.execute_command.side_effect = lambda *args: (remain.clear(), zones.clear())
        with patch("core.game_controller.GameController", return_value=controller):
            run_controller_loop(commands, results, settings.CURRENT_LAYOUT, "doudizhu", {})
        self.assertEqual(results.get_nowait().status, "ready")
        response = results.get_nowait()
        self.assertEqual(response.remain_cards, {"A": 1})
        self.assertEqual(response.zone_cards, {"left": [["A"]]})
        self.assertEqual(results.get_nowait().request_id, 2)


if __name__ == "__main__":
    unittest.main()
