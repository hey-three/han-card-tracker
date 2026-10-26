"""主进程与推理进程共用的纯 Python 消息协议。"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Command:
    """带请求编号和牌局代次的命令。

    Attributes:
        request_id: Worker 会话内单调递增的编号。
        generation: 重置或切换设置时递增的代次，用于丢弃旧检测结果。
        name: 命令名称。
        payload: 可序列化的参数。
    """

    request_id: int
    generation: int
    name: str
    payload: object = None


@dataclass(frozen=True)
class Response:
    """启动、检测、控制命令共用的执行回执。

    Attributes:
        status: ready 表示启动成功，ok/error 表示命令结果，fatal 表示启动失败。
        request_id: 对应的命令编号，启动消息为零。
        generation: 对应的牌局代次。
        remain_cards: 检测得到的剩余牌数。
        zone_cards: 各区出牌记录。
        yolo_ms: 推理耗时。
        error: 失败原因。
        layout_status: 自动布局的校准、跟踪或歧义说明；手动模式为空。
        screenshot_jpeg: 可选的实时预览截图 JPEG 数据。
        screenshot_regions: 自动布局当前确认的牌区边界，使用归一化坐标。
    """

    status: Literal["ready", "ok", "error", "fatal"]
    request_id: int = 0
    generation: int = 0
    remain_cards: dict[str, int] | None = None
    zone_cards: dict[str, list[list[str]]] | None = None
    yolo_ms: float = 0.0
    error: str = ""
    layout_status: str = ""
    screenshot_jpeg: bytes | None = None
    screenshot_regions: dict[str, tuple[float, float, float, float]] | None = None
