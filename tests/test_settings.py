"""配置原子保存、验证和内存发布的回归测试。"""

from copy import deepcopy
from pathlib import Path
import shutil
from uuid import uuid4
import unittest
from unittest.mock import patch


from config import settings
from config.store import read_config


class SettingsTests(unittest.TestCase):
    """仅操作临时配置，不改变项目配置。"""

    def setUp(self) -> None:
        """备份内存配置并使用临时文件。"""
        root = Path(__file__).resolve().parent / ".tmp"
        self.directory = root / uuid4().hex
        self.directory.mkdir(parents=True)
        assert self.directory.resolve().is_relative_to(root.resolve())
        self.addCleanup(shutil.rmtree, self.directory)
        self.path = self.directory / "config.yaml"
        self.path.write_text("frame_length: 3\ncustom_key: retained\n", encoding="utf-8")
        snapshot = settings.runtime_snapshot()
        original_config = deepcopy(settings.config)
        original_layout = settings.CURRENT_LAYOUT
        self.addCleanup(settings.apply_runtime_settings, snapshot)
        self.addCleanup(setattr, settings, "config", original_config)
        self.addCleanup(setattr, settings, "CURRENT_LAYOUT", original_layout)
        patcher = patch.object(settings, "CONFIG_PATH", str(self.path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_save_preserves_unknown_fields_and_updates_memory(self) -> None:
        """单项保存保留其他配置，并同步当前进程。"""
        settings.save_frame_length(1)
        self.assertEqual(read_config(str(self.path)), {"frame_length": 1, "custom_key": "retained"})
        self.assertEqual(settings.FRAME_LENGTH, 1)

    def test_corrupt_config_is_not_overwritten(self) -> None:
        """损坏配置必须报错，不能降级为空字典再覆盖。"""
        original = "broken: [\n"
        self.path.write_text(original, encoding="utf-8")
        with self.assertRaises(ValueError):
            settings.save_frame_length(1)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_replace_failure_preserves_file_and_memory(self) -> None:
        """磁盘替换失败时，旧配置与运行值都保留。"""
        original = self.path.read_bytes()
        previous = settings.FRAME_LENGTH
        with patch("config.store.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                settings.save_frame_length(1)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(settings.FRAME_LENGTH, previous)
        self.assertEqual(list(self.path.parent.glob(".config-*.tmp")), [])

    def test_invalid_frame_length_is_rejected(self) -> None:
        """拒绝零、负数、浮点数和 bool 帧长度。"""
        for value, error in ((0, ValueError), (-1, ValueError), (1.5, TypeError), (True, TypeError)):
            with self.subTest(value=value), self.assertRaises(error):
                settings.save_setting("frame_length", value)

    def test_runtime_update_validates_whole_batch(self) -> None:
        """后续字段无效时不发布前面的有效修改。"""
        previous = settings.FRAME_LENGTH
        with self.assertRaises(ValueError):
            settings.apply_runtime_settings({"FRAME_LENGTH": 1, "BASE_DIR": "unexpected"})
        self.assertEqual(settings.FRAME_LENGTH, previous)

    def test_layout_save_failure_is_visible_to_caller(self) -> None:
        """布局写入失败会抛异常，编辑器不会报告成功。"""
        existing = next(iter(settings.WINDOW_LAYOUTS.values()))
        with patch("config.store.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                settings.save_window_layout("new", "Window", existing["layout"])
        self.assertNotIn("new", settings.WINDOW_LAYOUTS)

    def test_new_layout_roundtrip_and_delete(self) -> None:
        """保存完整布局后，删除它会选择仍存在的布局。"""
        existing = next(iter(settings.WINDOW_LAYOUTS.values()))
        settings.save_window_layout("new", "Window", existing["layout"])
        saved = read_config(str(self.path))
        self.assertEqual(saved["current_layout"], "new")
        self.assertEqual(settings.WINDOW_LAYOUTS["new"]["window_title"], "Window")
        current = settings.delete_window_layout("new")
        self.assertIn(current, settings.WINDOW_LAYOUTS)
        self.assertNotIn("new", settings.WINDOW_LAYOUTS)


if __name__ == "__main__":
    unittest.main()
