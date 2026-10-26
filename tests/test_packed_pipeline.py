"""拼图执行、画面变化、同帧回退和缓冲复用测试。"""

import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from core.frame_packing import Detection
from core.packed_pipeline import PackedPipeline


def raw_result(cards: list[Detection]) -> Mock:
    """建立模型输出边界替身。

    Args:
        cards: 像素坐标检测。
    Returns:
        具备 xyxy、cls、conf 和 names 的原始结果。
    """
    result = Mock()
    names = list(dict.fromkeys(c.name for c in cards))
    result.names = dict(enumerate(names))
    result.boxes.xyxy.cpu.return_value.tolist.return_value = [list(c.bbox) for c in cards]
    result.boxes.cls.cpu.return_value.tolist.return_value = [float(names.index(c.name)) for c in cards]
    result.boxes.conf.cpu.return_value.tolist.return_value = [c.confidence for c in cards]
    return result


class PipelineTests(unittest.TestCase):
    """不使用权重，验证任何回退都只重新识别当前原图。"""

    def setUp(self) -> None:
        """准备同一原图和确定的双块重叠输出。"""
        self.pipeline = PackedPipeline()
        self.image = Image.new('RGB', (1000, 200), (80, 80, 80))
        self.regions = [(0., 0., 1., 1.)]
        self.detector = Mock()
        self.detector.detect.return_value = ('full', 10.)
        self.detector.detect_packed.return_value = (raw_result([
            Detection('A', (480., 30., 500., 80.)),
            Detection('A', (20., 234., 40., 284.)),
        ]), 5.)
        self.detector.restore_result.return_value = 'restored'

    def prime(self) -> None:
        """首帧整图建立参照；不复制上一帧检测结果。"""
        self.pipeline.detect(self.image, self.detector, self.regions, True, 3)
        self.detector.reset_mock()

    def test_disabled_and_uncalibrated_only_use_full(self) -> None:
        """默认和未校准模式均不尝试裁剪。"""
        for enabled, regions in ((False, self.regions), (True, None)):
            self.pipeline.detect(self.image, self.detector, regions, enabled, 3)
        self.detector.detect_packed.assert_not_called()

    def test_packed_restores_deduplicated_source_boxes_and_reuses_canvas(self) -> None:
        """复用画布但不复用模型结果，最终输出只有一个原图 A。"""
        self.prime()
        result, elapsed = self.pipeline.detect(self.image, self.detector, self.regions, True, 3)
        canvas = self.detector.detect_packed.call_args.args[0]
        self.assertEqual((result, elapsed), ('restored', 5.))
        cards = self.detector.restore_result.call_args.args[2]
        self.assertEqual([c.bbox for c in cards], [(480., 30., 500., 80.)])
        self.pipeline.detect(self.image, self.detector, self.regions, True, 3)
        self.assertIs(canvas, self.detector.detect_packed.call_args.args[0])
        self.detector.detect.assert_not_called()

    def test_conflict_rechecks_same_image_and_accumulates_model_time(self) -> None:
        """冲突后完整推理当前原图，计入两次调用成本。"""
        self.prime()
        self.detector.detect_packed.return_value = (raw_result([
            Detection('A', (480., 30., 500., 80.)), Detection('K', (20., 234., 40., 284.)),
        ]), 5.)
        result, elapsed = self.pipeline.detect(self.image, self.detector, self.regions, True, 3)
        self.assertEqual((result, elapsed), ('full', 15.))
        self.detector.detect.assert_called_once_with(self.image)
        self.detector.restore_result.assert_not_called()
        self.assertEqual(self.pipeline.timings['model_calls'], 2.)

    def test_changed_outside_region_triggers_full(self) -> None:
        """候选区外变化会在运行拼图模型前触发整图复查。"""
        self.prime()
        changed = self.image.copy()
        ImageDraw.Draw(changed).rectangle((0, 0, 150, 150), fill='white')
        self.pipeline.detect(changed, self.detector, [(.3, .1, .9, .9)], True, 3)
        self.detector.detect.assert_called_once_with(changed)
        self.detector.detect_packed.assert_not_called()
        self.assertIn('区外', self.pipeline.route)

    def test_periodic_scan_and_geometry_change_do_not_reuse_old_reference(self) -> None:
        """满复查间隔及窗口变形时回到整图。"""
        self.prime()
        self.pipeline._packed_count = 6
        self.pipeline.detect(self.image, self.detector, self.regions, True, 3)
        self.assertIn('定期', self.pipeline.route)
        self.detector.reset_mock()
        resized = self.image.resize((900, 200))
        self.pipeline.detect(resized, self.detector, self.regions, True, 3)
        self.detector.detect.assert_called_once_with(resized)
        self.detector.detect_packed.assert_not_called()

    def test_empty_or_runtime_failure_never_publishes_fake_empty(self) -> None:
        """空拼图和后端错误均回到原图，原图错误仍向上报告。"""
        self.prime()
        self.detector.detect_packed.return_value = (raw_result([]), 5.)
        self.assertEqual(self.pipeline.detect(self.image, self.detector, self.regions, True, 3)[0], 'full')
        self.detector.detect_packed.side_effect = RuntimeError('bad shape')
        self.assertEqual(self.pipeline.detect(self.image, self.detector, self.regions, True, 3)[0], 'full')
        self.detector.detect.side_effect = RuntimeError('device failed')
        with self.assertRaisesRegex(RuntimeError, 'device failed'):
            self.pipeline.detect(None, self.detector, self.regions, True, 3)


if __name__ == '__main__':
    unittest.main()
