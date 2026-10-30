"""首次启动及旧模型目录移除后的默认选择回归，不加载真实权重。"""

from pathlib import Path
import runpy
import shutil
import tempfile
import unittest
from unittest.mock import patch

from config import settings


class StartupModelTests(unittest.TestCase):
    """仅在tests/.tmp建立模型占位文件，验证路径与主/子进程快照一致。"""

    def setUp(self) -> None:
        """建立独立权重目录，保留用户配置与真实权重原样。"""
        directory = tempfile.TemporaryDirectory(dir='tests/.tmp')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.weights = self.root/'yolo'/'weights'
        self.weights.mkdir(parents=True)
        self.enterContext(patch.object(settings, 'YOLO_WEIGHTS_DIR', str(self.weights)))

    def add_model(self, name: str) -> str:
        """建立仅用于目录发现的权重占位文件。

        Args:
            name: 模型子目录名。
        Returns:
            建立的模型名，不表示权重内容通过加载验收。
        """
        folder = self.weights/name
        folder.mkdir()
        (folder/'best.pt').touch()
        return name

    def test_existing_choice_is_kept_even_when_another_sorts_first(self) -> None:
        """已有有效选择保持原样，不因新增更靠前的目录切模型。"""
        self.add_model('a_model')
        self.add_model('z_model')
        self.assertEqual(settings._select_startup_model('z_model'), 'z_model')

    def test_missing_or_unset_choice_uses_first_directory_with_weight(self) -> None:
        """旧目录移除或best.pt缺失，与未选模型一样回退首个有效目录。"""
        self.add_model('z_model')
        self.add_model('b_model')
        (self.weights/'a_incomplete').mkdir()
        (self.weights/'a_file').touch()
        for choice in ('deleted', 'a_incomplete', '', None):
            with self.subTest(choice=choice), patch('builtins.print'):
                self.assertEqual(settings._select_startup_model(choice), 'b_model')

    def test_no_weights_does_not_invent_a_working_model(self) -> None:
        """无权重时保留明确失败路径，不把空目录当成加载成功。"""
        self.assertEqual(settings._select_startup_model('deleted'), 'deleted')
        self.assertEqual(settings._select_startup_model(None), '')
        self.assertFalse(Path(settings._resolve_model_path(settings._select_startup_model('deleted'))).exists())
        with patch.object(settings, 'YOLO_WEIGHTS_DIR', str(self.root/'absent')):
            self.assertEqual(settings._select_startup_model('deleted'), 'deleted')

    def test_invalid_name_type_is_not_silently_defaulted(self) -> None:
        """损坏的名称类型仍明确报错。"""
        for value in (True, 123, []):
            with self.subTest(value=value), self.assertRaises(TypeError):
                settings._select_startup_model(value)

    def test_actual_startup_publishes_same_model_name_path_and_snapshot(self) -> None:
        """隔离执行完整设置模块，确认UI和子进程快照使用实际回退名且不写盘。"""
        self.add_model('z_model')
        self.add_model('a_model')
        config_dir = self.root/'config'
        config_dir.mkdir()
        source = config_dir/'settings.py'
        shutil.copyfile(settings.__file__, source)
        config = config_dir/'config.yaml'
        original = b'yolo_model_name: deleted\ncustom_key: retained\n'
        config.write_bytes(original)
        with patch('builtins.print'):
            namespace = runpy.run_path(str(source))
        self.assertEqual(namespace['YOLO_MODEL_NAME'], 'a_model')
        self.assertEqual(namespace['YOLO_MODEL_PATH'], str(self.weights/'a_model'/'best.pt'))
        self.assertEqual(namespace['runtime_snapshot']()['YOLO_MODEL_NAME'], 'a_model')
        self.assertEqual(config.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
