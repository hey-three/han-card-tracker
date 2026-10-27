"""推理进程回执、故障和旧结果隔离的回归测试。"""

import os
import queue
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.inference_process import InferenceWorker
from core.inference_protocol import Response
from ui.settings_controller import SettingsController


class WorkerTests(unittest.TestCase):
    """使用真实 Qt 信号和内存队列验证协议行为。"""

    @classmethod
    def setUpClass(cls) -> None:
        """为所有测试持有同一个无窗口 QApplication。"""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """构造已就绪的服务及可控子进程。"""
        self.worker = InferenceWorker("old")
        self.worker.state = "ready"
        self.worker._process = Mock()
        self.worker._process.is_alive.return_value = True
        self.worker._cmd_queue = Mock(wraps=queue.Queue(), cancel_join_thread=Mock(), close=Mock())
        self.worker._result_queue = Mock(wraps=queue.Queue(), cancel_join_thread=Mock(), close=Mock())
        self.results = Mock()
        self.finished = Mock()
        self.errors = Mock()
        self.worker.result_ready.connect(self.results)
        self.worker.finished.connect(self.finished)
        self.worker.error.connect(self.errors)

    def tearDown(self) -> None:
        """停止定时器并释放测试对象。"""
        if self.worker._process is not None:
            self.worker._process.is_alive.return_value = False
        self.worker.stop()

    def test_diagnostic_notification_never_completes_pending_detection(self) -> None:
        """异步保存故障只报告状态，不能提前解锁识别请求。"""
        diagnostics = Mock()
        self.worker.diagnostic_status_changed.connect(diagnostics)
        self.assertTrue(self.worker.request_detect())
        command = self.worker._cmd_queue.get_nowait()
        self.worker._result_queue.put(Response('diagnostic', diagnostic_status={'state': 'paused', 'reason': 'disk full'}))
        self.worker._poll_result()
        diagnostics.assert_called_once_with({'state': 'paused', 'reason': 'disk full'})
        self.finished.assert_not_called()
        self.assertIn(command.request_id, self.worker._pending)
        self.assertEqual(self.worker.state, 'ready')

    def test_crash_stops_new_detection(self) -> None:
        """子进程崩溃后不会再发出永远等不到结果的请求。"""
        self.worker._process.is_alive.return_value = False
        self.worker._process.exitcode = 1
        self.worker._poll_result()
        self.assertEqual(self.worker.state, "failed")
        self.assertFalse(self.worker.request_detect())
        self.errors.assert_called_once()

    def test_startup_failure_is_delivered_before_exit_check(self) -> None:
        """即使启动失败后退出码为零，仍报告原始错误详情。"""
        self.worker.state = "starting"
        self.worker._result_queue.put(Response("fatal", error="missing weights"))
        self.worker._process.is_alive.return_value = False
        self.worker._process.exitcode = 0
        self.worker._poll_result()
        self.errors.assert_called_once_with("missing weights")
        self.assertEqual(self.worker.state, "failed")

    def test_reset_discards_old_result_and_waits_for_ack(self) -> None:
        """重置前的检测结果被丢弃，收到回执后才能检测新牌局。"""
        metrics = Mock()
        self.worker.performance_ready.connect(metrics)
        self.assertTrue(self.worker.request_detect())
        detect = self.worker._cmd_queue.get_nowait()
        self.worker.request_reset()
        reset = self.worker._cmd_queue.get_nowait()
        self.worker._result_queue.put(Response("ok", detect.request_id, detect.generation, {"A": 1}, {},
                                               performance={"pipeline_ms": 999.}))
        self.worker._poll_result()
        self.results.assert_not_called()
        metrics.assert_not_called()
        self.assertFalse(self.worker.can_detect())
        self.worker._result_queue.put(Response("ok", reset.request_id, reset.generation))
        self.worker._poll_result()
        self.assertTrue(self.worker.request_detect())
        current = self.worker._cmd_queue.get_nowait()
        self.worker._result_queue.put(Response("ok", current.request_id, current.generation, {"A": 4}, {},
                                               performance={"pipeline_ms": 10.}))
        self.worker._poll_result()
        self.results.assert_called_once_with({"A": 4}, {}, 0.0)
        metrics.assert_called_once_with({"pipeline_ms": 10.})

    def test_control_failure_does_not_complete_inflight_detection(self) -> None:
        """设置失败回执不能冒充另一个检测请求的完成信号。"""
        self.worker.request_detect()
        self.worker._cmd_queue.get_nowait()
        request_id = self.worker.switch_device("cpu")
        command = self.worker._cmd_queue.get_nowait()
        self.worker._result_queue.put(Response("error", request_id, command.generation, error="failed"))
        self.worker._poll_result()
        self.finished.assert_not_called()
        self.assertFalse(self.worker.can_detect())

    def test_queue_failure_is_visible(self) -> None:
        """入队失败时立即进入故障状态并释放 busy。"""
        self.worker._cmd_queue.put_nowait.side_effect = OSError("closed")
        self.assertFalse(self.worker.request_detect())
        self.assertEqual(self.worker.state, "failed")
        self.finished.assert_called_once()

    def test_auto_layout_status_is_delivered_with_current_result(self) -> None:
        """自动定位进度沿结果协议回传，旧代次提示不得覆盖当前状态。"""
        status = Mock()
        self.worker.layout_status_changed.connect(status)
        self.worker.request_detect()
        command = self.worker._cmd_queue.get_nowait()
        self.worker._result_queue.put(Response(
            "ok", command.request_id, command.generation, {}, {}, layout_status="自动布局：正在确认开局",
        ))
        self.worker._poll_result()
        status.assert_called_once_with("自动布局：正在确认开局")
        self.finished.assert_called_once()

    def test_settings_are_saved_only_after_success(self) -> None:
        """保存、UI 刷新都等待对应控制命令成功。"""
        coordinator = SettingsController(self.worker)
        save, after = Mock(), Mock()
        coordinator.apply(save, lambda: self.worker.switch_device("cpu"), after)
        command = self.worker._cmd_queue.get_nowait()
        save.assert_not_called()
        self.worker._result_queue.put(Response("ok", command.request_id, command.generation))
        self.worker._poll_result()
        save.assert_called_once()
        after.assert_called_once()

    def test_failed_switch_preserves_persisted_settings(self) -> None:
        """后端切换失败时不保存，也不更新界面选择状态。"""
        coordinator = SettingsController(self.worker)
        save, after, failure = Mock(), Mock(), Mock()
        coordinator.failed.connect(failure)
        coordinator.apply(save, lambda: self.worker.switch_model("bad"), after)
        command = self.worker._cmd_queue.get_nowait()
        self.worker._result_queue.put(Response("error", command.request_id, command.generation, error="bad model"))
        self.worker._poll_result()
        save.assert_not_called()
        after.assert_not_called()
        failure.assert_called_once()
        self.assertTrue(self.worker.can_detect())

    def test_disk_failure_after_ack_stops_detection(self) -> None:
        """后端已切换但保存失败时显式停止，避免配置悄悄分歧。"""
        coordinator = SettingsController(self.worker)
        save = Mock(side_effect=PermissionError("locked"))
        coordinator.apply(save, lambda: self.worker.switch_device("cpu"))
        command = self.worker._cmd_queue.get_nowait()
        self.worker._result_queue.put(Response("ok", command.request_id, command.generation))
        self.worker._poll_result()
        self.assertEqual(self.worker.state, "failed")
        self.assertFalse(self.worker.can_detect())

    def test_restart_uses_fresh_queues(self) -> None:
        """重启不继承旧命令、旧结果或未完成设置。"""
        self.worker.request_reset()
        previous_cmd, previous_result = self.worker._cmd_queue, self.worker._result_queue
        self.worker._process.is_alive.return_value = False
        process = Mock()
        process.is_alive.return_value = True
        channels = [Mock(wraps=queue.Queue(), cancel_join_thread=Mock(), close=Mock()), Mock(wraps=queue.Queue(), cancel_join_thread=Mock(), close=Mock())]
        with patch("core.inference_process.mp.Process", return_value=process), patch(
            "core.inference_process.mp.Queue", side_effect=channels,
        ):
            self.worker.restart()
        self.assertIsNot(self.worker._cmd_queue, previous_cmd)
        self.assertIsNot(self.worker._result_queue, previous_result)
        self.assertEqual(self.worker._pending, {})
        self.assertEqual(self.worker.state, "starting")
        previous_cmd.close.assert_called_once()
        previous_result.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
