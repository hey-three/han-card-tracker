"""冻结入口：先处理multiprocessing分流，再加载Qt或验证工具。"""

import argparse
import multiprocessing
import os
from pathlib import Path
import sys


def run() -> int:
    """启动桌面程序或执行显式请求的打包验收。

    Returns:
        正常退出为0，验收失败为1。
    """
    if getattr(sys, 'frozen', False):
        settings_directory = Path(sys.executable).parent/'config/ultralytics'
        settings_directory.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault('YOLO_CONFIG_DIR', str(settings_directory))
        os.environ.setdefault('YOLO_AUTOINSTALL', 'false')
    if sys.stdout is None:
        sys.stdout = sys.__stdout__ = open(os.devnull, 'w', encoding='utf-8')
    if sys.stderr is None:
        sys.stderr = sys.__stderr__ = open(os.devnull, 'w', encoding='utf-8')
    if '--verify-bundle' in sys.argv:
        from scripts.packaging.bundle_smoke import verify_bundle
        parser = argparse.ArgumentParser()
        parser.add_argument('--verify-bundle', required=True, type=Path)
        parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
        args = parser.parse_args()
        return verify_bundle(args.verify_bundle, args.device)
    from main import main
    main()
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(run())
