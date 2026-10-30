"""在项目内创建Windows CPU或CPU/GPU发布目录及可复核的压缩包。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    """流式计算文件摘要。

    Args:
        path: 待读取文件。
    Returns:
        SHA-256十六进制值。
    """
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()


def prepare_inputs(directory: Path) -> list[dict[str, object]]:
    """复制发布资源，生成独立默认配置，不读取或修改个人配置。

    Args:
        directory: 当前版本及类型专属暂存目录。
    Returns:
        打包模型文件的相对路径和摘要。
    Raises:
        ValueError: 没有可发布模型，或模型缺少尺寸配置。
    """
    models = sorted((ROOT/'yolo/weights').glob('*/best.pt'))
    if not models:
        raise ValueError('没有可发布的best.pt')
    for name in ('config/games', 'ui', 'images', 'yolo/weights'):
        (directory/name).mkdir(parents=True, exist_ok=True)
    for path in (ROOT/'images').glob('*.png'):
        shutil.copy2(path, directory/'images'/path.name)
    shutil.copy2(ROOT/'ui/ui.qss', directory/'ui/ui.qss')
    shutil.copy2(ROOT/'config/games/doudizhu.yaml', directory/'config/games/doudizhu.yaml')
    config = {'device_choice': 'cpu', 'yolo_model_name': models[0].parent.name,
              'current_layout': '请新建游戏布局', 'last_layouts': {'auto': '请新建游戏布局'},
              'window_layouts': {'请新建游戏布局': {'mode': 'auto', 'layout': {},
                                                  'window_title': '请在设置中选择游戏窗口'}},
              'packed_inference': False, 'frame_length': 3, 'detect_interval_sec': .25,
              'full_diagnostics': False, 'show_current_screenshot': False, 'debug_mode': False,
              'show_timing': True, 'show_layout_status': True, 'diagnostic_disk_gib': 1.0,
              'panel_arrangement': 'vertical'}
    (directory/'config/config.yaml').write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
    recorded = []
    for weight in models:
        target = directory/'yolo/weights'/weight.parent.name
        target.mkdir(exist_ok=True)
        if not (weight.parent/'model_config.yaml').is_file():
            raise ValueError(f'{weight.parent}缺少model_config.yaml')
        for name in ('best.pt', 'model_config.yaml', 'best.onnx'):
            source = weight.parent/name
            if source.is_file():
                shutil.copy2(source, target/name)
                recorded.append({'file': str(source.relative_to(ROOT)), 'sha256': digest(source)})
    return recorded


def archive_bundle(bundle: Path, version: str, flavor: str, sevenzip: Path | None = None) -> Path:
    """压缩干净发布目录，排除验收产生的日志、用户配置及机器缓存。

    Args:
        bundle: 含EXE及_internal的已构建目录。
        version: 发布版本。
        flavor: cpu或cpu-gpu。
        sevenzip: GPU版使用的7zr或7z程序路径。
    Returns:
        可上传Release的ZIP或7z路径。
    Raises:
        ValueError: 压缩包超过GitHub单资产限制。
    """
    for name in ('README.md', 'README.en.md', 'LICENSE', 'SUPPORT.md'):
        shutil.copy2(ROOT/name, bundle/name)
    shutil.copytree(ROOT/'docs', bundle/'docs', dirs_exist_ok=True)
    shutil.copytree(ROOT/'images', bundle/'images', dirs_exist_ok=True)
    (bundle/'启动说明.txt').write_text(
        '完整解压后双击HanCardTracker.exe，请勿只复制EXE；7z包可用7-Zip解压。\n'
        'CPU版仅使用CPU；CPU-GPU版可在基本设置选择CPU或兼容CUDA的NVIDIA GPU。\n'
        '默认关闭完整诊断和预览，首次使用请在基本设置新建自己的游戏布局。\n'
        '当前模型优先使用原图；大小王区域必须包含完整J、O、K三个字母。\n'
        '模型在_internal/yolo/weights/；使用说明见docs/使用指南.md或设置→关于。\n', encoding='utf-8-sig')
    info_path = bundle/'build-info.json'
    info = json.loads(info_path.read_text(encoding='utf-8'))
    changed = subprocess.check_output(['git', 'diff', '--name-only', str(info['source_head']), 'HEAD',
        '--', 'main.py', 'core', 'ui', 'config', 'utils', 'scripts/packaging'], cwd=ROOT, text=True).strip()
    if changed:
        raise ValueError('应用源码自构建后已改变，须重建再压缩：'+changed)
    info['release_source_head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    info['source_models'] = info.pop('models', info.get('source_models', []))
    info['bundled_models'] = [
        {'file': path.relative_to(bundle).as_posix(), 'sha256': digest(path)}
        for path in sorted((bundle/'_internal/yolo/weights').glob('*/*'))
        if path.name in ('best.pt', 'model_config.yaml', 'best.onnx')]
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    label = 'CPU' if flavor == 'cpu' else 'CPU-GPU'
    suffix = '.zip' if flavor == 'cpu' else '.7z'
    archive = bundle.parents[1]/f'HanCardTracker-{version}-Windows-{label}{suffix}'
    prefix = f'HanCardTracker-{version}-{label}'
    files = [path for path in sorted(bundle.rglob('*')) if path.is_file()
             and path.relative_to(bundle).parts[0] not in ('config', 'logs', 'diagnostics', 'Ultralytics')
             and not path.name.endswith('.meta.json') and '.model-export-' not in str(path)]
    if flavor == 'cpu':
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
            for path in files:
                relative = path.relative_to(bundle)
                zipped.write(path, f'{prefix}/{relative.as_posix()}')
    else:
        if sevenzip is None or not sevenzip.is_file():
            raise ValueError('CPU/GPU包需要--sevenzip指定官网7zr.exe或7z.exe')
        # 用明确文件清单避免把验收时生成的本机材料带入7z。
        listing = bundle.parents[1]/'7z-files.txt'
        listing.write_text('\n'.join(f'{bundle.name}/{p.relative_to(bundle).as_posix()}' for p in files)+'\n', encoding='utf-8')
        if archive.exists():
            archive.unlink()  # 只替换本版本专属的自动生成压缩包。
        with (bundle.parents[1]/'7z.log').open('w', encoding='utf-8') as log:
            subprocess.run([str(sevenzip.resolve()), 'a', '-t7z', '-mx=7', '-md=128m', '-mmt=4',
                            '-ms=256m', '-scsUTF-8', '-bsp0', str(archive), '@'+str(listing)],
                           cwd=bundle.parent, stdout=log, stderr=subprocess.STDOUT, check=True)
    if archive.stat().st_size >= 2*1024**3:
        raise ValueError('ZIP达到2GiB，不能作为单个GitHub资产上传')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size,
                      'sha256': digest(archive)}, ensure_ascii=False), flush=True)
    return archive


def main() -> None:
    """在当前Python环境构建对应版本；可单独重新生成验收后的压缩包。

    Raises:
        ValueError: 版本、环境类型或输出路径不匹配。
        subprocess.CalledProcessError: PyInstaller构建失败。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flavor', choices=('cpu', 'cpu-gpu'), required=True)
    parser.add_argument('--version', default='v4.3.3')
    parser.add_argument('--archive-only', action='store_true')
    parser.add_argument('--sevenzip', type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'v\d+\.\d+\.\d+', args.version):
        raise ValueError('版本须为v数字.数字.数字')
    output = (ROOT/'artifacts/packaging'/args.version/args.flavor).resolve()
    if not output.is_relative_to((ROOT/'artifacts/packaging').resolve()):
        raise ValueError('构建路径越界')
    output.mkdir(parents=True, exist_ok=True)
    bundle = output/'dist/HanCardTracker'
    if args.archive_only:
        archive_bundle(bundle, args.version, args.flavor, args.sevenzip)
        return
    import torch
    if (torch.version.cuda is None) != (args.flavor == 'cpu'):
        raise ValueError('CPU版必须用CPU轮子，CPU/GPU版必须用CUDA轮子')
    inputs = output/'inputs'
    model_files = prepare_inputs(inputs)
    environment = dict(os.environ, HAN_BUNDLE_INPUTS=str(inputs), HAN_BUNDLE_FLAVOR=args.flavor)
    environment.update(PYINSTALLER_CONFIG_DIR=str(output/'pyinstaller-cache'),
                       YOLO_CONFIG_DIR=str(output/'ultralytics-config'), YOLO_AUTOINSTALL='false')
    (output/'ultralytics-config').mkdir(exist_ok=True)
    temporary = output/'temp'
    temporary.mkdir(exist_ok=True)
    environment.update(TEMP=str(temporary), TMP=str(temporary))
    with (output/'build.log').open('w', encoding='utf-8') as log:
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
                        '--distpath', str(output/'dist'), '--workpath', str(output/'work'),
                        str(ROOT/'scripts/packaging/han_tracker.spec')], cwd=ROOT, env=environment,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    packages = {name: importlib.metadata.version(name) for name in
                ('torch', 'torchvision', 'ultralytics', 'onnxruntime', 'PySide6', 'pyinstaller')}
    (bundle/'build-info.json').write_text(json.dumps({'version': args.version, 'flavor': args.flavor,
        'source_head': revision, 'python': sys.version, 'packages': packages, 'models': model_files},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'构建完成：{bundle}', flush=True)


if __name__ == '__main__':
    main()
