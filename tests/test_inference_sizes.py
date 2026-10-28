"""无需 torch 或 Ultralytics 的输入尺寸及 GPU 计时契约回归。"""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from core.model_input import ModelInput


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
        self.module = module
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
        self.image = Image.new('RGB', (640, 640))
        self.synchronize = torch_stub.cuda.synchronize

    def test_full_size_is_restored_after_packed_input(self) -> None:
        """矩形整图→方形对照→矩形整图明确传参，不能被上次调用污染。"""
        self.detector.detect(self.image)
        self.detector.detect_packed(self.image)
        self.detector.model.predictor.args.imgsz = 640
        self.detector.detect(self.image)
        calls = self.detector.model.call_args_list
        self.assertEqual([c.kwargs['imgsz'] for c in calls], [(480, 768), 640, (480, 768)])
        self.assertEqual([c.kwargs['rect'] for c in calls], [True, False, True])

    def test_static_backend_keeps_exported_shape(self) -> None:
        """静态 ONNX/TensorRT 沿用原导出尺寸，不强传 640。"""
        self.detector.model.predictor.model.pt = False
        self.detector.detect(self.image)
        self.detector.detect_packed(self.image)
        self.assertEqual(self.detector.model.call_args.kwargs['imgsz'], 960)

    def test_rectangle_shape_and_full_fallback_are_explicit(self) -> None:
        """原图和矩形拼图都实际请求 (H,W)=(480,768)。"""
        self.detector.detect_packed(Image.new('RGB', (768, 480)))
        self.assertEqual(self.detector.model.call_args.kwargs['imgsz'], (480, 768))
        self.detector.model.predictor.args.imgsz = (480, 768)
        self.detector.detect(self.image)
        self.assertEqual(self.detector.model.call_args.kwargs['imgsz'], (480, 768))

    def test_cold_pytorch_packing_uses_rectangle_before_predictor_exists(self) -> None:
        """冷启动也能从 .pt 元数据判断动态输入，不先跑一次方图预热。"""
        self.detector.model.overrides['model'] = 'best.pt'
        self.detector.model.predictor = None
        self.detector.detect_packed(Image.new('RGB', (768, 480)))
        self.assertEqual(self.detector.model.call_args.kwargs['imgsz'], (480, 768))

    def test_gpu_is_synchronized_before_and_after_measured_call(self) -> None:
        """CUDA 异步执行不会被误计为仅模型提交耗时。"""
        self.detector.device = 'cuda'
        self.detector.detect(self.image)
        self.assertEqual(self.synchronize.call_count, 2)

    def test_static_rectangle_metadata_wins_over_old_square_default(self) -> None:
        """导出480×768的静态模型不能被旧960默认值或方图对照覆盖。"""
        self.detector.model.predictor.model.pt = False
        self.detector.model.predictor.model.imgsz = [480, 768]
        self.detector.detect_packed(self.image)
        self.assertEqual(self.detector.model.call_args.kwargs['imgsz'], (480, 768))
        self.detector.detect(self.image)
        self.assertEqual(self.detector.model.call_args.kwargs['imgsz'], (480, 768))

    def test_capture_failure_does_not_invoke_model(self) -> None:
        """没有截图时不执行模型、不伪造耗时。"""
        self.assertEqual(self.detector.detect(None), (None, 0.))
        self.detector.model.assert_not_called()

    def test_configured_shape_is_shared_by_full_packed_and_fallback(self) -> None:
        """配置尺寸优先于训练标量和上次画布参数，整图/拼图一致且关闭最小补边。"""
        for width, height in ((960, 608), (960, 960)):
            with self.subTest(size=(width, height)):
                self.detector.input_spec = ModelInput(width, height, Path('model_config.yaml'))
                self.detector.model.reset_mock()
                self.detector.detect_packed(self.image)
                self.detector.detect(self.image)
                self.detector.detect_packed(Image.new('RGB', (width, height)))
                self.detector.detect(self.image)
                self.assertEqual(self.detector.input_canvas_size, (width, height))
                for call in self.detector.model.call_args_list:
                    self.assertEqual(call.kwargs['imgsz'], (height, width))
                    self.assertFalse(call.kwargs['rect'])

    def test_configured_static_mismatch_is_not_used(self) -> None:
        """配置矩形却加载方形静态模型时，在业务推理前失败。"""
        self.detector.input_spec = ModelInput(960, 608, Path('model_config.yaml'))
        self.detector.model.predictor.model = SimpleNamespace(pt=False, dynamic=False, imgsz=[960, 960])
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.detector.detect_packed(self.image)
        self.detector.model.assert_not_called()

    def test_configured_backend_initializes_without_image_inference(self) -> None:
        """首帧拼图之前只建立后端，不额外识别整张截图。"""
        self.detector.input_spec = ModelInput(960, 608, Path('model_config.yaml'))
        predictor = Mock()
        predictor.model = SimpleNamespace(pt=False, dynamic=False, imgsz=[608, 960], stride=32)
        factory = self.detector.model._smart_load.return_value
        factory.return_value = predictor
        self.detector._prepare_configured_model(self.detector.model, 'cpu')
        self.detector.model.assert_not_called()
        predictor.setup_model.assert_called_once()
        self.assertEqual(factory.call_args.kwargs['overrides']['imgsz'], (608, 960))

    def test_onnx_python_native_mismatch_is_rejected_early(self) -> None:
        """复现本机Python1.31.1/原生1.26.0混装，不等首帧触发缺少原生方法。"""
        bad = SimpleNamespace(__version__='1.31.1', get_version_string=Mock(return_value='1.26.0'))
        with self.assertRaisesRegex(RuntimeError, '不一致'):
            self.detector._check_onnx_runtime(bad)
        good = SimpleNamespace(__version__='1.26.0+cpu', get_version_string=Mock(return_value='1.26.0'))
        self.detector._check_onnx_runtime(good)

    def test_incompatible_onnx_runtime_skips_export_and_allows_pt_fallback(self) -> None:
        """坏依赖在选择后端时退出ONNX路线，不修改或重新生成缓存。"""
        module = ModuleType('onnxruntime')
        module.__version__ = '1.31.1'
        module.get_version_string = Mock(return_value='1.26.0')
        self.detector.input_spec = ModelInput(960, 608, Path('model_config.yaml'))
        with patch.dict('sys.modules', {'onnxruntime': module}), \
                patch.object(self.detector, '_load_configured_export') as export:
            self.assertIsNone(self.detector._YoloInferencer__load_onnx('best.onnx'))
        export.assert_not_called()

    def test_export_uses_explicit_shape_and_reuses_only_matching_cache(self) -> None:
        """模拟一次导出，明确高宽/批次/精度且下一次加载不再导出。"""
        from tempfile import TemporaryDirectory
        root = Path(__file__).parent / '.tmp'
        root.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=root) as directory:
            path = Path(directory)
            weight = path / 'best.pt'
            weight.write_bytes(b'fake PT')
            self.detector.weight_path = str(weight)
            self.detector.input_spec = ModelInput(960, 608, path / 'model_config.yaml')
            model = Mock()

            def export_stub(**kwargs: object) -> str:
                """只创建占位导出文件，不运行任何真实模型。

                Args:
                    kwargs: 被测代码传给导出器的关键字参数。
                Returns:
                    本次隔离缓存中生成的 ONNX 路径。
                """
                source = Path(factory.call_args.args[0])
                artifact = source.with_suffix('.onnx')
                artifact.write_bytes(b'fake ONNX')
                return str(artifact)

            model.export.side_effect = export_stub
            with patch.object(self.module, 'YOLO', return_value=model) as factory, \
                    patch.object(self.module, 'version', return_value='test'), \
                    patch.object(self.module.settings, 'BASE_DIR', str(path)), \
                    patch.object(self.detector, '_check_export_dependencies'), \
                    patch.object(self.detector, '_simplify_onnx') as simplify, \
                    patch.object(self.detector, '_prepare_configured_model', return_value=model):
                self.detector._load_configured_export('onnx', 'cpu')
                self.detector._load_configured_export('onnx', 'cpu')
            model.export.assert_called_once_with(format='onnx', imgsz=(608, 960), batch=1,
                                                 dynamic=False, half=False, device='cpu', simplify=False)
            simplify.assert_called_once()
            self.assertEqual(weight.read_bytes(), b'fake PT')


if __name__ == '__main__':
    unittest.main()
