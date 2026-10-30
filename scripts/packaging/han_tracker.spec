# -*- mode: python ; coding: utf-8 -*-
"""使用构建环境中的真实CPU/CUDA轮子，资源来自干净的发布暂存区。"""

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).resolve().parents[1]
inputs = Path(os.environ['HAN_BUNDLE_INPUTS']).resolve()
cpu_only = os.environ['HAN_BUNDLE_FLAVOR'] == 'cpu'
datas = [(str(inputs / name), name) for name in ('config', 'ui', 'images', 'yolo')]
datas += collect_data_files('ultralytics')
for package in ('ultralytics', 'torch', 'torchvision', 'onnx', 'onnxruntime', 'onnxslim',
                'numpy', 'pillow', 'PyYAML', 'PySide6', 'scipy', 'matplotlib', 'ultralytics-thop'):
    datas += copy_metadata(package)

a = Analysis(
    [str(root/'scripts/packaging/entry.py')], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=collect_submodules('ultralytics.nn') + ['win32timezone'],
    runtime_hooks=[str(root/'scripts/packaging/runtime_cpu.py')] if cpu_only else [],
    excludes=['IPython', 'jupyter', 'pytest', 'PyQt5', 'PyQt6', 'tensorflow', 'keras',
              'jax', 'tensorrt', 'tkinter', 'torchaudio', 'torch.testing._internal', 'torch.utils.tensorboard'],
    noarchive=False,
)
# Qt使用Windows自带的无版本后缀ICU API，不能误收PATH中Poppler等工具的ICU。
a.binaries = [item for item in a.binaries if Path(item[0]).name.lower()
              not in {'icuuc.dll', 'icuin.dll', 'icudt.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='HanCardTracker',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='HanCardTracker')
