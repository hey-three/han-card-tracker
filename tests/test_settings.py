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

    def test_per_mode_layout_memory_survives_reload_and_deletion(self) -> None:
        """旧配置切换时记住原模式，重载后仍可恢复，删除清理失效历史。"""
        keys = ('player_hand', 'player_played', 'opponent_left', 'opponent_right', 'landlord_cards')
        manual = {'window_title': 'Manual', 'layout': {key: [.1, .1, .4, .3] for key in keys}}
        # 两模式均可用，但配置最初没有last_layouts字段。
        layouts = {'M1': manual, 'M2': manual, 'A': {'window_title': 'Auto', 'mode': 'auto', 'layout': {}}}
        with patch.object(settings, 'WINDOW_LAYOUTS', layouts), patch.object(settings, 'CURRENT_LAYOUT', 'M2'):
            settings.save_current_layout('A')
            saved = read_config(str(self.path))
            self.assertEqual(saved['last_layouts'], {'manual': 'M2', 'auto': 'A'})
            with patch.object(settings, 'config', saved):
                self.assertEqual(settings.preferred_layout('manual'), 'M2')
                self.assertEqual(settings.preferred_layout('auto'), 'A')
            settings.delete_window_layout('M2')
            self.assertEqual(settings.preferred_layout('manual'), 'M1')
            self.assertNotIn('manual', read_config(str(self.path))['last_layouts'])

    def test_layout_memory_save_failure_preserves_selection_and_history(self) -> None:
        """原子保存失败时当前选择和分模式历史一起保留。"""
        name = next(iter(settings.WINDOW_LAYOUTS))
        before = deepcopy(settings.config)
        old = settings.CURRENT_LAYOUT
        with patch('config.settings.write_config', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                settings.save_current_layout(name)
        self.assertEqual(settings.CURRENT_LAYOUT, old)
        self.assertEqual(settings.config, before)

    def test_panel_arrangement_persists_and_rejects_invalid_values(self) -> None:
        """排列偏好原子保存并保留其他配置，拒绝非法枚举及类型。"""
        for value in ('horizontal', 'vertical'):
            settings.save_setting('panel_arrangement', value)
            self.assertEqual(read_config(str(self.path))['panel_arrangement'], value)
            self.assertEqual(settings.PANEL_ARRANGEMENT, value)
        for value in ('auto', '', 'left'):
            with self.assertRaises(ValueError):
                settings.save_setting('panel_arrangement', value)
        with self.assertRaises(TypeError):
            settings.save_setting('panel_arrangement', True)
        original = self.path.read_bytes()
        with patch('config.store.os.replace', side_effect=OSError('read only')):
            with self.assertRaises(OSError):
                settings.save_setting('panel_arrangement', 'horizontal')
        self.assertEqual(settings.PANEL_ARRANGEMENT, 'vertical')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(read_config(str(self.path))['custom_key'], 'retained')

    def test_diagnostic_disk_quota_validates_and_persists(self) -> None:
        """磁盘额度严格校验并原子持久化，支持两位小数和旧配置缺省值。"""
        self.assertEqual(settings._SETTING_FIELDS['diagnostic_disk_gib'][0], 'DIAGNOSTIC_DISK_GIB')
        for invalid in (True, '2', None):
            with self.assertRaises(TypeError):
                settings.save_setting('diagnostic_disk_gib', invalid)
        for invalid in (0, -.1, .09, 1024.01, float('nan'), float('inf'), 1.234):
            with self.assertRaises(ValueError):
                settings.save_setting('diagnostic_disk_gib', invalid)
        settings.save_setting('diagnostic_disk_gib', 2.25)
        self.assertEqual(read_config(str(self.path))['diagnostic_disk_gib'], 2.25)
        self.assertEqual(settings.diagnostic_disk_bytes(), int(2.25*1024**3))
        self.assertEqual(settings.runtime_snapshot()['DIAGNOSTIC_DISK_GIB'], 2.25)

    def test_diagnostic_disk_save_failure_keeps_old_value(self) -> None:
        """替换文件失败时磁盘和内存保留旧额度。"""
        previous = settings.DIAGNOSTIC_DISK_GIB
        original = self.path.read_bytes()
        with patch('config.store.os.replace', side_effect=OSError('read only')):
            with self.assertRaises(OSError):
                settings.save_setting('diagnostic_disk_gib', 5.)
        self.assertEqual(settings.DIAGNOSTIC_DISK_GIB, previous)
        self.assertEqual(self.path.read_bytes(), original)

    def test_save_preserves_unknown_fields_and_updates_memory(self) -> None:
        """单项保存保留其他配置，并同步当前进程。"""
        settings.save_frame_length(1)
        self.assertEqual(read_config(str(self.path)), {"frame_length": 1, "custom_key": "retained"})
        self.assertEqual(settings.FRAME_LENGTH, 1)

    def test_packed_route_persists_and_rejects_fake_booleans(self) -> None:
        """试验路线通过原子保存和快照传递，拒绝字符串或数字冒充开关。"""
        settings.save_setting("packed_inference", True)
        self.assertIs(read_config(str(self.path))["packed_inference"], True)
        self.assertIs(settings.runtime_snapshot()["PACKED_INFERENCE"], True)
        for value in ('true', 1, None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                settings.save_setting("packed_inference", value)

    def test_packed_route_save_failure_retains_previous_value(self) -> None:
        """磁盘保存失败不发布新的拼图偏好。"""
        previous = settings.PACKED_INFERENCE
        with patch("config.store.os.replace", side_effect=PermissionError("locked")), self.assertRaises(PermissionError):
            settings.save_setting("packed_inference", not previous)
        self.assertEqual(settings.PACKED_INFERENCE, previous)

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

    def test_auto_layout_saves_without_any_region_coordinates(self) -> None:
        """自动模式仅保存窗口标题，进程快照仍能完整校验。"""
        settings.save_window_layout("Auto", "Game", {}, mode="auto")
        saved = read_config(str(self.path))["window_layouts"]["Auto"]
        self.assertEqual(saved, {"window_title": "Game", "layout": {}, "mode": "auto"})
        settings.apply_runtime_settings(settings.runtime_snapshot())
        self.assertEqual(settings.WINDOW_LAYOUTS["Auto"], saved)

    def test_layout_mode_is_validated(self) -> None:
        """自动模式不放松手动模式校验，且拒绝未知模式。"""
        for mode, error in (("other", ValueError), (True, TypeError), ("manual", ValueError)):
            with self.subTest(mode=mode), self.assertRaises(error):
                settings.validate_layout({"window_title": "Game", "layout": {}, "mode": mode})

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

    def test_cpu_bundle_rejects_gpu_without_changing_saved_device(self) -> None:
        """CPU发布版本拒绝GPU配置，持久化和运行时都不留下伪GPU选择。"""
        settings.save_setting('device_choice', 'cpu')
        before = self.path.read_bytes()
        with patch.object(settings, 'CPU_ONLY_BUILD', True):
            with self.assertRaisesRegex(ValueError, 'CPU版'):
                settings.save_setting('device_choice', 'cuda')
            with self.assertRaisesRegex(ValueError, 'CPU版'):
                settings.apply_runtime_settings({'DEVICE_CHOICE': 'cuda'})
        self.assertEqual(settings.DEVICE_CHOICE, 'cpu')
        self.assertEqual(self.path.read_bytes(), before)

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
