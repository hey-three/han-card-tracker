"""固定输入旁置文件与隔离导出缓存契约，不加载真实权重。"""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import yaml

from core.model_export_cache import ModelExportCache
from core.model_input import ModelInput, load_model_input, static_backend_shape


class ModelInputTests(unittest.TestCase):
    """校验方形/矩形、宽高顺序、损坏配置与真实静态绑定优先级。"""

    def setUp(self) -> None:
        """只在 tests/.tmp 下准备隔离目录。"""
        root = Path(__file__).parent / ".tmp"
        root.mkdir(exist_ok=True)
        temporary = TemporaryDirectory(dir=root)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.weight = self.root / "best.pt"
        self.weight.write_bytes(b"test weight, not an actual model")
        self.config = self.root / "model_config.yaml"

    def write_input(self, width: object = 960, height: object = 608,
                    **extra: object) -> None:
        """写入待验证字段，不创建任何真实模型。

        Args:
            width: 输入宽度或故意构造的非法值。
            height: 输入高度或故意构造的非法值。
            extra: input 下的额外字段。
        """
        data = {"model": "仅说明名称", "weights": ["best.pt", "last.pt"],
                "input": {"width": width, "height": height, "resize": "letterbox", "rect": False, **extra}}
        self.config.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    def test_square_rectangle_and_wh_hw_order(self) -> None:
        """宽高不会被目录名、描述名或缺少 last.pt 干扰。"""
        for width, height in ((960, 608), (960, 960), (640, 960)):
            with self.subTest(width=width, height=height):
                self.write_input(width, height)
                spec = load_model_input(self.weight)
                self.assertIsNotNone(spec)
                self.assertEqual(spec.canvas_size, (width, height))
                self.assertEqual(spec.imgsz, (height, width))

    def test_only_missing_sidecar_uses_legacy(self) -> None:
        """缺文件可兼容；空文件和损坏 YAML 不能悄悄换成默认尺寸。"""
        self.assertIsNone(load_model_input(self.weight))
        for contents in ("", "input: [", "[]"):
            with self.subTest(contents=contents):
                self.config.write_text(contents, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_model_input(self.weight)

    def test_invalid_dimensions_and_preprocessing_are_rejected(self) -> None:
        """布尔/字符串/小数、未对齐尺寸和拉伸都不能进入模型。"""
        for width in (True, "960", 960.0, None):
            with self.subTest(width=width):
                self.write_input(width)
                with self.assertRaises(TypeError):
                    load_model_input(self.weight)
        for width in (0, -32, 961):
            with self.subTest(width=width):
                self.write_input(width)
                with self.assertRaises(ValueError):
                    load_model_input(self.weight)
        for extra, error in (({"rect": True}, ValueError), ({"rect": "false"}, TypeError),
                             ({"resize": "stretch"}, ValueError)):
            with self.subTest(extra=extra):
                self.write_input(**extra)
                with self.assertRaises(error):
                    load_model_input(self.weight)

    def test_unlisted_weight_and_missing_input_fail(self) -> None:
        """已有旁置文件必须描述实际选择的权重和输入。"""
        self.config.write_text("weights: [last.pt]\ninput: {width: 960, height: 608}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "best.pt"):
            load_model_input(self.weight)
        self.config.write_text("input: []", encoding="utf-8")
        with self.assertRaises(TypeError):
            load_model_input(self.weight)

    def test_static_bindings_win_over_conflicting_metadata(self) -> None:
        """ONNX/engine 实际绑定优先于旧 imgsz 标签，动态轴不伪装固定尺寸。"""
        from unittest.mock import Mock
        session = Mock()
        session.get_inputs.return_value = [SimpleNamespace(shape=[1, 3, 960, 960])]
        backend = SimpleNamespace(pt=False, dynamic=False, imgsz=[608, 960], session=session)
        self.assertEqual(static_backend_shape(backend), (1, 3, 960, 960))
        backend.session = None
        backend.bindings = {"images": SimpleNamespace(shape=(1, 3, 608, 960))}
        self.assertEqual(static_backend_shape(backend), (1, 3, 608, 960))
        backend.dynamic = True
        self.assertIsNone(static_backend_shape(backend))

    def test_cache_identity_and_incomplete_or_corrupt_exports(self) -> None:
        """换权重/尺寸/环境隔离缓存，未完成及被改写的导出不可复用。"""
        spec = ModelInput(960, 608, self.config)
        cache = ModelExportCache(self.weight, spec, "onnx", {"version": "a"})
        self.assertFalse(cache.ready())
        self.assertEqual(cache.artifact, self.weight.with_suffix('.onnx'))
        with cache.export_source() as copied:
            self.assertNotEqual(copied, self.weight.resolve())
            self.assertEqual(copied.read_bytes(), self.weight.read_bytes())
            candidate = copied.with_suffix('.onnx')
            candidate.write_bytes(b"fake export")
            self.assertFalse(cache.ready())
            cache.publish(candidate)
        self.assertTrue(cache.ready())
        self.assertFalse(copied.parent.exists())
        same = ModelExportCache(self.weight, spec, "onnx", {"version": "a"})
        self.assertTrue(same.ready())
        square = ModelExportCache(self.weight,
                                  ModelInput(960, 960, self.config), "onnx", {"version": "a"})
        environment = ModelExportCache(self.weight, spec, "onnx", {"version": "b"})
        self.assertFalse(square.ready())
        self.assertFalse(environment.ready())
        self.weight.write_bytes(b"replaced model")
        replaced = ModelExportCache(self.weight, spec, "onnx", {"version": "a"})
        self.assertFalse(replaced.ready())
        cache.artifact.write_bytes(b"corrupt")
        self.assertFalse(cache.ready())
        self.assertEqual(self.weight.read_bytes(), b"replaced model")

    def test_failed_export_preserves_source_and_existing_artifact(self) -> None:
        """临时导出失败不覆盖原PT或旧ONNX，工作区会清理。"""
        cache = ModelExportCache(self.weight, ModelInput(960, 608, self.config), 'onnx', {})
        before = self.weight.read_bytes()
        cache.artifact.write_bytes(b'old ONNX')
        with self.assertRaisesRegex(RuntimeError, 'export failed'):
            with cache.export_source() as source:
                source.with_suffix('.onnx').write_bytes(b'partial ONNX')
                raise RuntimeError('export failed')
        self.assertEqual(cache.artifact.read_bytes(), b'old ONNX')
        self.assertEqual(self.weight.read_bytes(), before)
        self.assertFalse(list(self.root.glob('.model-export-*')))

    def test_source_change_and_metadata_failure_do_not_replace_old_artifact(self) -> None:
        """导出中途替换PT或校验记录创建失败时保留旧ONNX。"""
        from unittest.mock import patch
        cache = ModelExportCache(self.weight, ModelInput(960, 608, self.config), 'onnx', {})
        cache.artifact.write_bytes(b'old ONNX')
        with cache.export_source() as source:
            candidate = source.with_suffix('.onnx')
            candidate.write_bytes(b'new ONNX')
            with patch('core.model_export_cache.tempfile.NamedTemporaryFile', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    cache.publish(candidate)
            self.assertEqual(cache.artifact.read_bytes(), b'old ONNX')
            self.weight.write_bytes(b'changed PT')
            with self.assertRaisesRegex(OSError, '权重发生变化'):
                cache.publish(candidate)
            self.assertEqual(cache.artifact.read_bytes(), b'old ONNX')

    def test_onnx_and_engine_have_independent_sidecars(self) -> None:
        """同目录两个静态后端互不覆盖校验记录，也不能把原PT当作导出发布。"""
        spec = ModelInput(960, 608, self.config)
        stores = [ModelExportCache(self.weight, spec, kind, {}) for kind in ('onnx', 'engine')]
        for cache in stores:
            with cache.export_source() as source:
                candidate = source.with_suffix(cache.artifact.suffix)
                candidate.write_bytes(cache.artifact.suffix.encode())
                cache.publish(candidate)
            self.assertTrue(cache.ready())
            with self.assertRaises(ValueError):
                cache.publish(self.weight)
        self.assertNotEqual(stores[0].manifest, stores[1].manifest)
        self.assertTrue(all(cache.ready() for cache in stores))


if __name__ == "__main__":
    unittest.main()
