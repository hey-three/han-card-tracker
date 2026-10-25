"""验证子进程组件切换的完整参数和失败回退。"""

from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from config import settings
from core.game_controller import GameController


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
        self.controller.debug_manager = Mock()

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

    def test_game_switch_does_not_write_configuration(self) -> None:
        """子进程切换游戏只改内存，磁盘由主进程管理。"""
        with patch.object(settings, "save_game_choice") as save:
            self.controller.switch_game("doudizhu")
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
