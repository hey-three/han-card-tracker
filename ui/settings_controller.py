"""协调设置应用：子进程确认成功后，主进程才保存并刷新 UI。"""

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from core.inference_process import InferenceWorker


class SettingsController(QObject):
    """统一设置操作的成功、失败和持久化流程。

    Attributes:
        failed: 设置操作失败的说明。
        worker: 推理服务。
        _pending: 请求编号到保存、刷新回调的映射。
    """

    failed = Signal(str)
    applied = Signal()

    def __init__(self, worker: InferenceWorker, parent: QObject | None = None) -> None:
        """绑定推理回执。

        Args:
            worker: 推理服务。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self.worker = worker
        self._pending: dict[int, tuple[Callable[[], None], Callable[[], None] | None]] = {}
        worker.command_completed.connect(self._complete)
        worker.state_changed.connect(self._on_state_changed)

    def apply(
        self, save: Callable[[], None], request: Callable[[], int | None] | None = None,
        after: Callable[[], None] | None = None,
    ) -> bool:
        """应用设置；服务故障时允许先修正保存值，供手动重启使用。

        Args:
            save: 仅在后端成功后执行的主进程保存操作。
            request: 后端请求，纯 UI 设置可省略。
            after: 保存成功后的 UI 刷新。

        Returns:
            已保存或已排入请求时为 True，失败时为 False。
        """
        try:
            if request is None or self.worker.state in ("stopped", "failed"):
                save()
                if after is not None:
                    after()
                self.applied.emit()
                return True
            request_id = request()
            if request_id is None:
                self.failed.emit("推理服务不可用，设置未应用；请重启识别后再试。")
                return False
            self._pending[request_id] = (save, after)
            return True
        except Exception as exc:
            self.failed.emit(f"设置未应用: {exc}")
            return False

    def _complete(self, request_id: int, name: str, success: bool, error: str) -> None:
        """处理对应请求回执，失败时保留原持久化设置。

        Args:
            request_id: 请求编号。
            name: 命令名称。
            success: 后端执行是否成功。
            error: 后端错误详情。
        """
        action = self._pending.pop(request_id, None)
        if action is None:
            return
        if not success:
            self.failed.emit(f"{name} 未生效，保留原设置。\n{error}")
            return
        save, after = action
        try:
            save()
            if after is not None:
                after()
            self.applied.emit()
        except Exception as exc:
            # 后端已切换但磁盘保存失败时停止新检测，防止悄悄使用不一致配置。
            self.worker.fail(f"设置保存或界面刷新失败: {exc}。请检查配置后重启识别。")

    def _on_state_changed(self, state: str) -> None:
        """结束会话时取消旧请求的保存回调。

        Args:
            state: 推理生命周期状态。
        """
        if state in ("stopped", "failed"):
            self._pending.clear()
