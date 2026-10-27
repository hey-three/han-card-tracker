"""推理子进程入口；重型依赖在子进程的异常边界内加载。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from multiprocessing.queues import Queue
import queue
import traceback

from core.inference_protocol import Command, Response
from core.diagnostics import DiagnosticRecorder


def run_controller_loop(
    cmd_queue: Queue[Command | None], result_queue: Queue[Response],
    layout_name: str, game_name: str, snapshot: Mapping[str, object],
) -> None:
    """执行命令并为每条命令回传独立结果，禁止操作 Qt 对象。

    Args:
        cmd_queue: 输入命令队列；None 表示正常退出。
        result_queue: 输出结果队列。
        layout_name: 初始布局名。
        game_name: 初始游戏名。
        snapshot: 主进程确认过的配置快照。
    """
    recorder: DiagnosticRecorder | None = None

    def notify(status: dict[str, object]) -> None:
        """后台仅向队列发送纯数据，不能触碰Qt。

        Args:
            status: 诊断状态副本。
        """
        try:
            result_queue.put(Response('diagnostic', diagnostic_status=status))
        except (OSError, ValueError):
            pass

    try:
        from config import settings
        from core.game_controller import GameController

        settings.apply_game_choice(game_name)
        settings.apply_runtime_settings(snapshot)
        recorder = DiagnosticRecorder(settings.BASE_DIR, notify)
        controller = GameController(game_name, layout_name)
        controller.attach_diagnostics(recorder)
    except Exception:
        if recorder is not None:
            recorder.daily.write('startup_failed', error=traceback.format_exc())
            recorder.close()
        result_queue.put(Response("fatal", error=traceback.format_exc()))
        return

    result_queue.put(Response("ready", diagnostic_status=recorder.status()))
    try:
        while True:
            try:
                command = cmd_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            except (OSError, EOFError):
                return
            if command is None:
                return
            try:
                if command.name == "detect":
                    controller.request_context = (command.request_id, command.generation)
                    remain, zones, elapsed = controller.detect()
                    performance = getattr(controller, "last_performance", None)
                    # multiprocessing.Queue 延迟序列化；复制以免下一命令修改同一对象。
                    response = Response(
                        "ok", command.request_id, command.generation,
                        deepcopy(remain), deepcopy(zones), elapsed,
                        layout_status=controller.layout_status,
                        screenshot_jpeg=controller.last_screenshot_jpeg,
                        screenshot_regions=deepcopy(controller.last_screenshot_regions),
                        performance=deepcopy(performance) if isinstance(performance, dict) else None,
                        diagnostic_status=recorder.status(),
                    )
                else:
                    controller.execute_command(command.name, command.payload)
                    response = Response("ok", command.request_id, command.generation)
            except Exception:
                response = Response(
                    "error", command.request_id, command.generation,
                    error=traceback.format_exc(),
                )
            if response.status == 'error':
                recorder.daily.write('command_failed', name=command.name, request_id=command.request_id,
                                     generation=command.generation, error=response.error)
            elif command.name != 'detect':
                recorder.daily.write('command_applied', name=command.name, request_id=command.request_id,
                                     generation=command.generation)
            # 诊断通知独立，不解锁任何未完成的设置或检测请求。
            result_queue.put(response)
            if command.name != 'detect' or response.status == 'error':
                notify(recorder.status())

    finally:
        recorder.close()
