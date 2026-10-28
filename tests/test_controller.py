"""验证子进程组件切换的完整参数和失败回退。"""

from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from config import settings
from core.game_controller import GameController
from core.packed_pipeline import PackedPipeline


class ControllerTests(unittest.TestCase):
    """不加载实际模型，直接验证组件替换边界。"""

    def setUp(self) -> None:
        """准备旧组件和独立运行配置。"""
        snapshot = settings.runtime_snapshot()
        self.addCleanup(settings.apply_runtime_settings, snapshot)
        self.addCleanup(setattr, settings, "CURRENT_LAYOUT", settings.CURRENT_LAYOUT)
        self.controller = GameController.__new__(GameController)
        self.controller.game_name = "doudizhu"
        self.controller.layout_name = "old"
        self.controller.layout_config = deepcopy(next(iter(settings.WINDOW_LAYOUTS.values())))
        self.controller.tracker = Mock()
        self.controller.screen_capture = Mock()
        self.controller.layout_analyzer = Mock()
        self.controller.yolo_inferencer = Mock()
        self.controller.packed_pipeline = PackedPipeline()

    def test_model_shape_is_forwarded_to_manual_packing(self) -> None:
        """控制器使用所选模型的实际像素画布，而不是继续硬编码 768×480。"""
        from PIL import Image
        settings.PACKED_INFERENCE = True
        settings.SHOW_CURRENT_SCREENSHOT = False
        self.controller.tracker.state = settings.WAIT_BEGIN
        self.controller.screen_capture.capture_window.return_value = Image.new('RGB', (1200, 800))
        self.controller.tracker.get_cards_number.return_value = ({}, {}, 0.)
        self.controller.layout_config = {'window_title': 'test', 'mode': 'manual', 'layout': {
            'landlord_cards': [.35, .02, .65, .12], 'player_hand': [.05, .7, .95, .95],
            'player_played': [.3, .4, .7, .6], 'opponent_left': [.05, .2, .3, .4],
            'opponent_right': [.7, .2, .95, .4]}}
        self.controller.packed_pipeline = Mock()
        self.controller.packed_pipeline.timings = {}
        self.controller.packed_pipeline.detect.return_value = (None, 0.)
        for size in ((960, 608), (960, 960)):
            self.controller.yolo_inferencer.input_canvas_size = size
            self.controller._detect_frame()
            self.assertEqual(self.controller.packed_pipeline.detect.call_args.kwargs['canvas_size'], size)
            self.assertEqual(len(self.controller.packed_pipeline.detect.call_args.args[2]), 2)

    def test_failed_enable_rolls_back_quota_and_settings_together(self) -> None:
        """同批修改额度后启动线程失败，后端内存也须恢复旧额度。"""
        from core.diagnostics import DiagnosticLimits
        recorder = Mock()
        recorder.limits = DiagnosticLimits()
        recorder.set_enabled.side_effect = RuntimeError('writer failed')
        self.controller.diagnostics = recorder
        with patch.multiple(settings, FULL_DIAGNOSTICS=False, DIAGNOSTIC_DISK_GIB=1.):
            with self.assertRaisesRegex(RuntimeError, 'writer failed'):
                self.controller.update_settings({'FULL_DIAGNOSTICS': True, 'DIAGNOSTIC_DISK_GIB': 5.})
            self.assertEqual(settings.DIAGNOSTIC_DISK_GIB, 1.)
            self.assertFalse(settings.FULL_DIAGNOSTICS)
            self.assertEqual(recorder.set_disk_limit.call_args_list[0].args, (5*1024**3,))
            self.assertEqual(recorder.set_disk_limit.call_args_list[-1].args, (1024**3,))

    def test_new_layout_does_not_depend_on_child_snapshot(self) -> None:
        """子进程从未见过的布局也能立即应用。"""
        layout = deepcopy(self.controller.layout_config)
        layout["window_title"] = "New Window"
        with patch("core.game_controller.ScreenCapture") as capture:
            self.controller.switch_layout("new", layout)
        self.assertEqual(self.controller.layout_name, "new")
        self.assertEqual(self.controller.layout_config, layout)
        capture.assert_called_once_with("New Window")
        layout["layout"]["player_hand"] = [0, 0, 1, 1]
        self.assertNotEqual(self.controller.layout_config, layout)

    def test_same_name_layout_uses_updated_coordinates(self) -> None:
        """同名布局编辑后使用新坐标，而不是启动时快照。"""
        layout = deepcopy(self.controller.layout_config)
        layout["layout"]["player_hand"] = [0.1, 0.6, 0.9, 0.9]
        with patch("core.game_controller.ScreenCapture"):
            self.controller.switch_layout("old", layout)
        self.assertEqual(self.controller.layout_analyzer.layout_config, layout)

    def test_layout_construction_failure_keeps_old_components(self) -> None:
        """组件构造失败后名称、坐标和 Tracker 一起保留。"""
        previous = (self.controller.layout_name, self.controller.layout_config,
                    self.controller.tracker, self.controller.screen_capture)
        with patch("core.game_controller.ScreenCapture", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                self.controller.switch_layout("new", self.controller.layout_config)
        self.assertEqual(previous, (self.controller.layout_name, self.controller.layout_config,
                                    self.controller.tracker, self.controller.screen_capture))

    def test_model_failure_restores_settings_and_components(self) -> None:
        """新模型加载失败不会改变旧模型配置或 Tracker。"""
        previous = (settings.YOLO_MODEL_NAME, settings.YOLO_MODEL_PATH,
                    self.controller.yolo_inferencer, self.controller.tracker)
        with patch.object(self.controller, "_create_inferencer", side_effect=RuntimeError("bad model")):
            with self.assertRaises(RuntimeError):
                self.controller.switch_model("bad")
        self.assertEqual(previous, (settings.YOLO_MODEL_NAME, settings.YOLO_MODEL_PATH,
                                    self.controller.yolo_inferencer, self.controller.tracker))

    def test_device_failure_restores_previous_device(self) -> None:
        """设备加载失败时恢复运行配置。"""
        previous = settings.DEVICE_CHOICE
        with patch.object(self.controller, "_create_inferencer", side_effect=RuntimeError("device")):
            with self.assertRaises(RuntimeError):
                self.controller.switch_device("cpu")
        self.assertEqual(settings.DEVICE_CHOICE, previous)

    def test_legacy_packing_update_clears_geometry_without_resetting_round(self) -> None:
        """兼容旧命令只使已有规划失效，不清除牌局或改变布局路线。"""
        previous = self.controller.tracker
        pipeline = self.controller.packed_pipeline
        pipeline._plan = Mock()
        self.controller.update_settings({"PACKED_INFERENCE": True})
        self.assertIsNot(self.controller.packed_pipeline, pipeline)
        self.assertIsNone(self.controller.packed_pipeline._plan)
        self.assertIs(self.controller.tracker, previous)
        previous.reset.assert_not_called()


if __name__ == "__main__":
    unittest.main()
