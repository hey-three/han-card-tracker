"""显式验收冻结包的Qt界面、真实推理子进程及各模型调用。"""

from __future__ import annotations

import json
import multiprocessing as mp
from multiprocessing.queues import Queue
from pathlib import Path
import sys
import traceback


def model_probe(device: str, results: Queue) -> None:
    """在真实spawn子进程逐个加载模型并调用，结果仅回传普通字典。

    Args:
        device: cpu或cuda。
        results: 父进程接收验证结果的队列。
    """
    try:
        import torch
        from PIL import Image
        import onnxruntime as ort
        from config import settings
        from core.yolo_inferencer import YoloInferencer
        if device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('本机CUDA不可用，不能将CPU回退当作GPU验收')
        if settings.CPU_ONLY_BUILD and torch.version.cuda is not None:
            raise RuntimeError('CPU版意外包含CUDA版PyTorch')
        torch.set_num_threads(2)
        records = []
        for name in settings._scan_model_dirs():
            settings.apply_runtime_settings({'DEVICE_CHOICE': device, 'YOLO_MODEL_NAME': name})
            detector = YoloInferencer()
            raw, elapsed = detector.detect(Image.new('RGB', (853, 479), (90, 90, 90)))
            if raw is None or detector.device != device:
                raise RuntimeError(f'{name}未返回预期设备的检测结果')
            backend = detector.model.predictor.model
            records.append({'model': name, 'device': detector.device,
                            'canvas_wh': list(detector.input_canvas_size),
                            'pt': bool(getattr(backend, 'pt', False)),
                            'onnx': bool(getattr(backend, 'onnx', False)),
                            'call_ms': elapsed})
        results.put({'ok': True, 'models': records, 'torch': torch.__version__,
                     'torch_cuda': torch.version.cuda, 'cuda_available': torch.cuda.is_available(),
                     'ort_python': ort.__version__, 'ort_native': ort.get_version_string()})
    except Exception:
        results.put({'ok': False, 'error': traceback.format_exc()})


def verify_bundle(report: Path, device: str) -> int:
    """启动真实主界面等待Worker就绪，再验证资源、联系人和模型调用。

    Args:
        report: 验收JSON保存路径。
        device: 待验证的真实推理设备。
    Returns:
        所有检查通过为0，否则为1；结果和错误均写入report。
    """
    result: dict[str, object] = {'frozen': bool(getattr(sys, 'frozen', False)), 'requested_device': device}
    window = None
    process = None
    try:
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtGui import QFont, QPixmap
        from PySide6.QtWidgets import QApplication, QLabel
        from config import settings
        from ui.main_window import CardUI
        from ui.settings_dialog import SettingsDialog
        settings.apply_runtime_settings({'DEVICE_CHOICE': device})
        app = QApplication.instance() or QApplication([])
        font = QFont('Microsoft YaHei', 9)
        font.setPixelSize(14)
        app.setFont(font)
        window = CardUI()
        worker_errors: list[str] = []
        window.worker.error.connect(worker_errors.append)
        window.is_paused = True
        window.timer.stop()
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        window.worker.state_changed.connect(lambda state: loop.quit() if state in ('ready', 'failed') else None)
        timer.start(120000)
        window.show()
        loop.exec()
        timer.stop()
        result['worker_state'] = window.worker.state
        if window.worker.state != 'ready':
            raise RuntimeError(f'冻结推理子进程未就绪：{window.worker.state}；{worker_errors}')
        dialog = SettingsDialog(window)
        guide = dialog.findChild(QLabel, 'AboutUsageGuideLink')
        if guide is None or not guide.openExternalLinks():
            raise RuntimeError('缺少使用说明入口')
        if QPixmap(str(Path(settings.RESOURCE_DIR)/'images/QR.png')).isNull():
            raise RuntimeError('打包后二维码资源缺失')
        result['cpu_only'] = settings.CPU_ONLY_BUILD
        result['device_options'] = [dialog.combo_device.itemText(i) for i in range(dialog.combo_device.count())]
        expected = ['CPU'] if settings.CPU_ONLY_BUILD else ['CPU', 'GPU']
        if result['device_options'] != expected:
            raise RuntimeError('设备选项与打包类型不符')
        result['guide_url'] = guide.toolTip()
        dialog.close()
        worker_process = window.worker._process
        window.close()
        app.processEvents()
        if worker_process is not None and worker_process.exitcode != 0:
            raise RuntimeError(f'冻结Worker未正常退出：{worker_process.exitcode}')
        context = mp.get_context('spawn')
        channel = context.Queue()
        process = context.Process(target=model_probe, args=(device, channel))
        process.start()
        probe = channel.get(timeout=240)
        process.join(timeout=15)
        if process.is_alive() or process.exitcode != 0 or not probe['ok']:
            raise RuntimeError(f'冻结模型验收失败：{probe}')
        result.update(probe)
        channel.close()
        channel.join_thread()
        result['ok'] = True
    except Exception:
        result.update(ok=False, error=traceback.format_exc())
    finally:
        if window is not None:
            window.close()
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=5)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result.get('ok') else 1
