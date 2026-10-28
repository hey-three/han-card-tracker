"""本机布局参考图的隔离存储、身份校验和原子替换测试。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from config import settings
from utils.layout_editor.reference_store import load_reference_image, save_reference_image


class LayoutReferenceStoreTests(unittest.TestCase):
    """仅使用工作区临时目录和合成图，不访问个人参考截图。"""

    def setUp(self) -> None:
        """将参考图存储定位到当前测试独立的临时配置目录。"""
        directory = self.enterContext(tempfile.TemporaryDirectory(dir='tests/.tmp'))
        self.directory = Path(directory)
        self.enterContext(patch.object(settings, 'CONFIG_PATH', str(self.directory / 'config.yaml')))

    def test_reference_images_are_isolated_and_replaced_per_layout(self) -> None:
        """含路径符号的名称仍安全隔离，同名再次截图只替换自己的图。"""
        save_reference_image('../布局甲', 'Game', Image.new('RGB', (30, 20), 'red'))
        save_reference_image('布局乙', 'Game', Image.new('RGB', (40, 25), 'green'))
        save_reference_image('../布局甲', 'Game', Image.new('RGB', (60, 35), 'blue'))
        first = load_reference_image('../布局甲', 'Game')
        second = load_reference_image('布局乙', 'Game')
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertEqual(first.size, (60, 35))
        self.assertEqual(first.getpixel((0, 0)), (0, 0, 255))
        self.assertEqual(second.size, (40, 25))
        self.assertEqual(len(list((self.directory / 'layout_previews').glob('*.png'))), 2)

    def test_missing_or_mismatched_target_does_not_load_another_image(self) -> None:
        """缺少缓存或窗口标题变化时不错误加载旧窗口画面。"""
        self.assertIsNone(load_reference_image('甲', 'Game'))
        save_reference_image('甲', 'Game', Image.new('RGB', (30, 20)))
        self.assertIsNone(load_reference_image('甲', 'Other'))

    def test_failed_replace_keeps_old_reference_and_removes_temporary_file(self) -> None:
        """最终替换失败时保留上一张可用图，不留下半张PNG或临时文件。"""
        save_reference_image('甲', 'Game', Image.new('RGB', (30, 20), 'red'))
        with patch('utils.layout_editor.reference_store.os.replace', side_effect=OSError('read only')):
            with self.assertRaises(OSError):
                save_reference_image('甲', 'Game', Image.new('RGB', (60, 35), 'blue'))
        image = load_reference_image('甲', 'Game')
        assert image is not None
        self.assertEqual(image.size, (30, 20))
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(len(list((self.directory / 'layout_previews').iterdir())), 1)

    def test_corrupted_cache_is_reported(self) -> None:
        """损坏图片可定位为读取错误，调用方可以提示用户重新截图。"""
        save_reference_image('甲', 'Game', Image.new('RGB', (30, 20)))
        target = next((self.directory / 'layout_previews').glob('*.png'))
        target.write_bytes(b'not an image')
        with self.assertRaises(OSError):
            load_reference_image('甲', 'Game')
