"""无需 torch 或 Ultralytics 的输入尺寸及 GPU 计时契约回归。"""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image


class InferenceSizeTests(unittest.TestCase):
    """替换模型边界，防止拼图尺寸污染后续整图调用。"""

    def setUp(self) -> None:
        """从源文件独立加载推理器，不改变应用正常模块缓存。"""
        torch_stub = ModuleType('torch')
        torch_stub.cuda = SimpleNamespace(synchronize=Mock())
        yolo_stub = ModuleType('ultralytics')
        yolo_stub.YOLO = object
        spec = importlib.util.spec_from_file_location('_v43_yolo_contract', Path(__file__).parents[1]/'core/yolo_inferencer.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', {'torch': torch_stub, 'ultralytics': yolo_stub}):
            spec.loader.exec_module(module)
        self.detector = module.YoloInferencer.__new__(module.YoloInferencer)
        self.detector.model = Mock()
        self.detector.model.overrides = {'imgsz': 960}
        self.detector.model.predictor = SimpleNamespace(
            model=SimpleNamespace(pt=True, dynamic=False), args=SimpleNamespace(imgsz=960),
        )
        self.detector.model.return_value = [object()]
        self.detector.device = 'cpu'
        self.detector.yolo_conf, self.detector.yolo_iou = .6, .45
        self.detector._full_input_size = None
        self.image = Image.new('RGB', (100, 100))
        self.synchronize = torch_stub.cuda.synchronize

    def test_full_size_is_restored_after_packed_input(self) -> None:
        """960→640→960 明确传参，不能被持久化 predictor.args 污染。"""
        self.detector.detect(self.image)
        self.detector.detect_packed(self.image)
        self.detector.model.predictor.args.imgsz = 640
        self.detector.detect(self.image)
        calls = self.detector.model.call_args_list
        self.assertEqual([c.kwargs['imgsz'] for c in calls], [960, 640, 960])
        self.assertEqual([c.kwargs['rect'] for c in calls], [True, False, True])

    def test_static_backend_keeps_exported_shape(self) -> None:
        """静态 ONNX/TensorRT 沿用原导出尺寸，不强传 640。"""
        self.detector.model.predictor.model.pt = False
        self.detector.detect(self.image)
        self.detector.detect_packed(self.image)
        self.assertEqual(self.detector.model.call_args.kwargs['imgsz'], 960)

    def test_gpu_is_synchronized_before_and_after_measured_call(self) -> None:
        """CUDA 异步执行不会被误计为仅模型提交耗时。"""
        self.detector.device = 'cuda'
        self.detector.detect(self.image)
        self.assertEqual(self.synchronize.call_count, 2)

    def test_capture_failure_does_not_invoke_model(self) -> None:
        """没有截图时不执行模型、不伪造耗时。"""
        self.assertEqual(self.detector.detect(None), (None, 0.))
        self.detector.model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
