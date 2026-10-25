"""推理子进程入口；重型依赖在子进程的异常边界内加载。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from multiprocessing.queues import Queue
import queue
import traceback

from core.inference_protocol import Command, Response


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
    try:
        from config import settings
        from core.game_controller import GameController

        settings.apply_game_choice(game_name)
        settings.apply_runtime_settings(snapshot)
        controller = GameController(game_name, layout_name)
    except Exception:
        result_queue.put(Response("fatal", error=traceback.format_exc()))
        return

    result_queue.put(Response("ready"))
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
                remain, zones, elapsed = controller.detect()
                # multiprocessing.Queue 延迟序列化；复制以免下一命令修改同一对象。
                response = Response(
                    "ok", command.request_id, command.generation,
                    deepcopy(remain), deepcopy(zones), elapsed,
                )
            else:
                controller.execute_command(command.name, command.payload)
                response = Response("ok", command.request_id, command.generation)
        except Exception:
            response = Response(
                "error", command.request_id, command.generation,
                error=traceback.format_exc(),
            )
        result_queue.put(response)
