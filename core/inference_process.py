"""管理推理进程生命周期、命令回执和过期检测结果。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import multiprocessing as mp
from multiprocessing.queues import Queue
import queue

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from config import settings
from core.controller_process import run_controller_loop
from core.inference_protocol import Command, Response


class InferenceWorker(QObject):
    """主进程中的推理服务；每次启动使用新队列。

    Attributes:
        result_ready: 有效检测结果信号。
        error: 检测或进程故障的可读说明。
        finished: 一次检测结束，通知 UI 释放 busy。
        state_changed: stopped/starting/ready/failed 生命周期状态。
        command_completed: 请求编号、命令名、成功标志和错误说明。
        state: 当前生命周期状态。
    """

    result_ready = Signal(dict, dict, float)
    error = Signal(str)
    finished = Signal()
    state_changed = Signal(str)
    command_completed = Signal(int, str, bool, str)

    def __init__(self, layout_name: str, game_name: str = "doudizhu", parent: QObject | None = None) -> None:
        """创建服务，启动前不分配跨进程资源。

        Args:
            layout_name: 初始布局名。
            game_name: 初始游戏名。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._layout_name = layout_name
        self._game_name = game_name
        self._cmd_queue: Queue[Command | None] | None = None
        self._result_queue: Queue[Response] | None = None
        self._process: mp.Process | None = None
        self._pending: dict[int, Command] = {}
        self._next_id = 0
        self._generation = 0
        self.state = "stopped"
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(20)
        self._poll_timer.timeout.connect(self._poll_result)

    def _set_state(self, state: str) -> None:
        """发布生命周期变更。

        Args:
            state: 新状态。
        """
        if self.state != state:
            self.state = state
            self.state_changed.emit(state)

    def start(self) -> None:
        """从主进程已确认的设置启动服务，失败通过信号报告。"""
        if self.state in ("starting", "ready"):
            return
        self.stop()
        self._layout_name = settings.CURRENT_LAYOUT or self._layout_name
        self._game_name = settings.GAME_NAME
        self._generation += 1
        self._set_state("starting")
        try:
            self._cmd_queue = mp.Queue()
            self._result_queue = mp.Queue()
            self._process = mp.Process(
                target=run_controller_loop,
                args=(self._cmd_queue, self._result_queue, self._layout_name,
                      self._game_name, settings.runtime_snapshot()),
                daemon=True,
            )
            self._process.start()
            self._poll_timer.start()
        except Exception as exc:
            self.fail(f"启动推理进程失败: {exc}")

    def stop(self) -> None:
        """停止进程并丢弃会话队列，避免重启消费旧命令。"""
        self._poll_timer.stop()
        self._pending.clear()
        process = self._process
        if process is not None:
            if process.is_alive():
                try:
                    if self._cmd_queue is not None:
                        self._cmd_queue.put_nowait(None)
                except (OSError, ValueError, queue.Full):
                    pass
                process.join(timeout=2)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=1)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1)
            if not process.is_alive():
                process.close()
            self._process = None
        for channel in (self._cmd_queue, self._result_queue):
            if channel is not None:
                # 子进程可能已死，不等待 feeder 将剩余数据写入无人读取的管道。
                channel.cancel_join_thread()
                channel.close()
        self._cmd_queue = None
        self._result_queue = None
        self._set_state("stopped")

    def restart(self) -> None:
        """重建进程和队列；新的牌局状态从头初始化。"""
        self.stop()
        self.start()

    def is_alive(self) -> bool:
        """返回子进程是否存活。

        Returns:
            子进程存在且存活时为 True。
        """
        return self._process is not None and self._process.is_alive()

    def can_detect(self) -> bool:
        """只在就绪且无未完成命令时允许新检测。

        Returns:
            是否可派发一轮检测。
        """
        return self.state == "ready" and self.is_alive() and not self._pending

    def _submit(self, name: str, payload: object = None, invalidate: bool = False) -> int | None:
        """复制命令参数并入队，发送失败进入故障状态。

        Args:
            name: 命令名。
            payload: 可序列化参数。
            invalidate: 是否使之前派发的检测结果过期。

        Returns:
            请求编号；服务不可用或入队失败时返回 None。
        """
        if self.state not in ("starting", "ready") or self._cmd_queue is None:
            return None
        if not self.is_alive():
            self.fail("推理进程已退出，请点击“重启识别”恢复。")
            return None
        if invalidate:
            self._generation += 1
        self._next_id += 1
        command = Command(self._next_id, self._generation, name, deepcopy(payload))
        try:
            self._cmd_queue.put_nowait(command)
        except (OSError, ValueError, queue.Full) as exc:
            self.fail(f"发送推理命令失败: {exc}")
            return None
        self._pending[command.request_id] = command
        return command.request_id

    def request_detect(self) -> bool:
        """尝试派发检测。

        Returns:
            请求已发送时为 True，UI 据此设置 busy。
        """
        return self.can_detect() and self._submit("detect") is not None

    def request_reset(self) -> int | None:
        """重置牌局并使在途检测过期。

        Returns:
            请求编号，服务不可用时为 None。
        """
        return self._submit("reset", invalidate=True)

    def switch_layout(self, layout_name: str) -> int | None:
        """发送完整布局，支持运行中新增或编辑布局。

        Args:
            layout_name: 主进程中的布局名。

        Returns:
            请求编号，服务不可用时为 None。

        Raises:
            KeyError: 布局不存在。
            TypeError: 布局内容类型错误。
            ValueError: 布局内容无效。
        """
        config = settings.validate_layout(settings.WINDOW_LAYOUTS[layout_name])
        return self._submit("switch_layout", {"name": layout_name, "config": config}, invalidate=True)

    def switch_model(self, model_name: str) -> int | None:
        """提交模型切换。

        Args:
            model_name: 模型目录名。

        Returns:
            请求编号，服务不可用时为 None。
        """
        return self._submit("switch_model", model_name, invalidate=True)

    def switch_device(self, device_name: str) -> int | None:
        """提交设备切换。

        Args:
            device_name: cpu 或 cuda。

        Returns:
            请求编号，服务不可用时为 None。
        """
        return self._submit("switch_device", device_name, invalidate=True)

    def switch_game(self, game_name: str) -> int | None:
        """提交游戏切换。

        Args:
            game_name: 已注册游戏名。

        Returns:
            请求编号，服务不可用时为 None。
        """
        return self._submit("switch_game", game_name, invalidate=True)

    def update_settings(self, updates: Mapping[str, object]) -> int | None:
        """提交热更新设置。

        Args:
            updates: 运行时属性到新值的映射。

        Returns:
            请求编号，服务不可用时为 None。
        """
        return self._submit("update_settings", dict(updates), invalidate=True)

    def touch_time(self) -> None:
        """排入时间刷新，避免暂停或设置关闭后立即超时。"""
        self._submit("touch_time")

    def fail(self, message: str) -> None:
        """停止派发和轮询，保留故障状态供用户手动恢复。

        Args:
            message: 显示给用户的故障原因。
        """
        if self.state == "failed":
            return
        self._poll_timer.stop()
        self._pending.clear()
        self._set_state("failed")
        self.finished.emit()
        self.error.emit(message)

    @Slot()
    def _poll_result(self) -> None:
        """先消费回执再检查进程退出，保留正常退出前的启动错误。"""
        if self.state not in ("starting", "ready") or self._result_queue is None:
            return
        while True:
            try:
                response = self._result_queue.get_nowait()
            except queue.Empty:
                break
            except (OSError, EOFError, ValueError) as exc:
                self.fail(f"读取推理结果失败: {exc}")
                return
            if response.status == "fatal":
                self.fail(response.error)
                return
            if response.status == "ready":
                self._set_state("ready")
                continue
            command = self._pending.pop(response.request_id, None)
            if command is None:
                continue
            if command.name == "detect":
                if response.generation == self._generation:
                    if response.status == "ok":
                        self.result_ready.emit(response.remain_cards or {}, response.zone_cards or {}, response.yolo_ms)
                    else:
                        self.error.emit(response.error)
                self.finished.emit()
            else:
                self.command_completed.emit(
                    command.request_id, command.name, response.status == "ok", response.error,
                )
            if self.state not in ("starting", "ready"):
                return
        if self._process is not None and not self._process.is_alive():
            self.fail(f"推理进程意外退出 (code={self._process.exitcode})，请点击“重启识别”恢复。")
