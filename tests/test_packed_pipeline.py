"""手动五区拼图、规划缓存、同帧回退和缓冲复用测试。"""

import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from core.frame_packing import Detection
from core.packed_pipeline import PackedPipeline
from core.square_packing import make_square_plan


def raw_result(cards: list[Detection]) -> Mock:
    """建立同时支持整图解析和拼图恢复的模型输出替身。

    Args:
        cards: 像素坐标检测。
    Returns:
        具备 xyxy、cls、conf 和 names 的原始结果。
    """
    result = Mock()
    names = list(dict.fromkeys(c.name for c in cards))
    result.names = dict(enumerate(names))
    boxes = np.array([c.bbox for c in cards], dtype=float).reshape((-1, 4))
    classes = np.array([names.index(c.name) for c in cards], dtype=float)
    scores = np.array([c.confidence for c in cards], dtype=float)
    for tensor, values in ((result.boxes.xyxy, boxes), (result.boxes.cls, classes), (result.boxes.conf, scores)):
        tensor.cpu.return_value.tolist.return_value = values.tolist()
        tensor.cpu.return_value.numpy.return_value = values
    return result


class PipelineTests(unittest.TestCase):
    """不使用权重，验证布局路线、完整性与当前帧语义。"""

    def setUp(self) -> None:
        """建立覆盖五区的几何规划和安全的单个目标。"""
        self.pipeline = PackedPipeline()
        self.image = Image.new("RGB", (1000, 800), (80, 80, 80))
        self.regions = [(.04, .7, .96, .85), (.04, .46, .96, .57), (.2, .25, .47, .45),
                        (.48, .25, .85, .45), (.37, 0., .55, .08)]
        self.plan = make_square_plan(self.image.size, self.regions)
        self.detector = Mock()
        self.detector.detect.return_value = ("full", 10.)
        self.detector.restore_result.return_value = "restored"
        tile = self.plan.tiles[0]
        a, b, _, _ = tile.source
        self.card = Detection("A", (a+20., b+20., a+40., b+50.))
        self.detector.detect_packed.return_value = (raw_result([Detection("A", tile.project(self.card.bbox))]), 5.)

    def test_auto_always_uses_full_and_does_not_build_a_plan(self) -> None:
        """自动模式始终整图，哪怕已曾运行过手动拼图。"""
        self.pipeline.detect(self.image, self.detector, self.regions)
        self.detector.reset_mock()
        with patch("core.packed_pipeline.make_square_plan") as build:
            for _ in range(8):
                self.pipeline.detect(self.image, self.detector, None)
        build.assert_not_called()
        self.detector.detect_packed.assert_not_called()
        self.assertEqual(self.detector.detect.call_count, 8)

    def test_manual_first_frame_packs_and_restores_original_coordinates(self) -> None:
        """手动首帧无需整图预热，返回原图坐标而非画布坐标。"""
        result, elapsed = self.pipeline.detect(self.image, self.detector, self.regions)
        self.assertEqual((result, elapsed), ("restored", 5.))
        self.assertEqual(self.detector.detect_packed.call_args.args[0].size, (640, 640))
        self.detector.detect.assert_not_called()
        cards = self.detector.restore_result.call_args.args[2]
        self.assertEqual(len(cards), 1)
        for actual, expected in zip(cards[0].bbox, self.card.bbox):
            self.assertAlmostEqual(actual, expected)

    def test_geometry_and_canvas_are_reused_but_model_is_called_each_frame(self) -> None:
        """固定布局只规划一次，下一帧仍重新采图和推理。"""
        with patch("core.packed_pipeline.make_square_plan", wraps=make_square_plan) as build:
            self.pipeline.detect(self.image, self.detector, self.regions)
            canvas = self.detector.detect_packed.call_args.args[0]
            self.pipeline.detect(self.image.copy(), self.detector, self.regions)
        self.assertEqual(build.call_count, 1)
        self.assertIs(canvas, self.detector.detect_packed.call_args.args[0])
        self.assertEqual(self.pipeline.timings["plan_ms"], 0.)
        self.assertEqual(self.detector.detect_packed.call_count, 2)

    def test_size_or_region_changes_rebuild_geometry(self) -> None:
        """窗口尺寸或用户框变化时不能套用旧坐标。"""
        self.detector.detect_packed.return_value = (raw_result([]), 5.)
        with patch("core.packed_pipeline.make_square_plan", wraps=make_square_plan) as build:
            self.pipeline.detect(self.image, self.detector, self.regions)
            self.pipeline.detect(self.image.resize((900, 700)), self.detector, self.regions)
            self.pipeline.detect(self.image, self.detector, self.regions[:-1])
        self.assertEqual(build.call_count, 3)

    def test_empty_checks_same_whole_image_and_accumulates_both_calls(self) -> None:
        """空拼图复查当前整图；不能将五区之外仍有牌误判为全图无牌。"""
        self.detector.detect_packed.return_value = (raw_result([]), 5.)
        result, elapsed = self.pipeline.detect(self.image, self.detector, self.regions)
        self.assertEqual((result, elapsed), ("full", 15.))
        self.detector.detect.assert_called_once_with(self.image)
        self.assertEqual(self.pipeline.timings["model_calls"], 2.)
        self.detector.restore_result.assert_not_called()

    def test_cross_tile_or_padding_result_falls_back(self) -> None:
        """灰边内的伪目标不作为可靠牌面发布。"""
        self.detector.detect_packed.return_value = (raw_result([Detection("A", (0., 0., 2., 2.))]), 5.)
        self.assertEqual(self.pipeline.detect(self.image, self.detector, self.regions), ("full", 15.))
        self.detector.restore_result.assert_not_called()

    def test_runtime_failure_falls_back_and_full_failure_propagates(self) -> None:
        """拼图后端失败可补查，整图失败向上传播真实故障。"""
        self.detector.detect_packed.side_effect = RuntimeError("bad shape")
        self.assertEqual(self.pipeline.detect(self.image, self.detector, self.regions)[0], "full")
        self.assertEqual(self.pipeline.timings["model_calls"], 2.)
        self.detector.detect.side_effect = RuntimeError("device failed")
        with self.assertRaisesRegex(RuntimeError, "device failed"):
            self.pipeline.detect(None, self.detector, self.regions)


if __name__ == "__main__":
    unittest.main()
