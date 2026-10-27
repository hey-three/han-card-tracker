# Han Card Tracker — Dou Dizhu Card Counter for Windows

[简体中文](README.md) · [Documentation](docs/README.md) · [Issues](https://github.com/hey-three/han-card-tracker/issues) · [YOLO training repository](https://github.com/hey-three/yolo-for-han-card-tracker) · [❤ Support](SUPPORT.md)

**Han Card Tracker is a Windows desktop application for Dou Dizhu (斗地主) card counting.** It uses YOLO playing-card detection to recognize cards in a visible game window, track remaining cards outside the user's initial hand, and display play history. The application is built with Python, PySide6 and PyTorch.

## Features

- YOLO object detection with CPU/CUDA execution and optional ONNX Runtime/TensorRT backends.
- Automatic layout learning for common three-player interfaces, or five user-defined regions.
- Original-image or packed inference for manual layouts; automatic layouts always use the full image.
- Two-stage packing: bottom cards and initial hand first, then bottom cards and the three play areas after the initial hand has been deducted.
- Strict user-region crops with no added margins, fixed initial scaling, and online scale estimates from individual card detections.
- A fixed 768×480 canvas, overlap-aware coordinate restoration and duplicate removal.
- Pause/resume, always-on-top UI, play history, timing, actual YOLO input preview, and paired original/annotated screenshots.

This repository currently maintains the **Windows Dou Dizhu application**. It does not ship an Android APK, use OCR for rank recognition, or implement other card games. It does not control the game. Compatibility and accuracy must be checked for each interface and skin.

## Source installation

Windows is required. Source syntax requires Python 3.10+, but the pinned binary packages impose additional version constraints. Start with a separate Python 3.11/3.12 environment; the pinned dependency combination has not been reinstalled from scratch during this documentation update.

```powershell
git clone https://github.com/hey-three/han-card-tracker.git
cd han-card-tracker
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
python -m pip install -r requirements.txt
python main.py
```

The dependency file pins CUDA 12.1 PyTorch wheels. Follow the [official PyTorch instructions](https://pytorch.org/get-started/previous-versions/) rather than assuming the default index or any Python version will work. [Installation details](docs/使用指南.md) are maintained in Chinese.

Weights are stored in `yolo/weights/<model-name>/best.pt`. Custom models must preserve the configured class mapping. The [training project](https://github.com/hey-three/yolo-for-han-card-tracker) is maintained separately.

## First use

1. Open the game window and keep it visible.
2. Select automatic layout in settings, or define the hand, own play, left play, right play and bottom-card regions in the visual editor.
3. For a manual layout, choose packed or original inference in advanced settings. This preference applies to all manual layouts.
4. Start before the round begins and inspect the actual YOLO input preview.

Any detected bottom card opens a round. A stable, nonempty legal hand is deducted once; an exact 17/20-card hand is not required. The count excludes the user's initial hand and confirmed opponent plays; the user's plays only add history and never trigger another deduction or retroactive initial-hand correction.

Missing bottom cards pause updates. A consecutive-frame and elapsed-time threshold ends the round internally while preserving the displayed history until the next round starts. See the [counting contract](docs/斗地主记牌设计说明.md).

## Releases and verification

As checked on 2026-10-27, [Releases](https://github.com/hey-three/han-card-tracker/releases) contains a historical `v2.3` archive. It is not a packaged build of the current V4.3.2 source. See [CHANGELOG](CHANGELOG.md).

The diagnostic update passed 203 unit tests, including a real Windows spawn/worker-shutdown test, and compared recorder modes on repeated static reference images. This does not establish complete live-game accuracy, GPU/backend compatibility, or a 200 ms latency target. Optional [full diagnostics](docs/诊断记录.md) groups evidence into per-round folders under a recording session. Its total disk budget defaults to 1 GiB and is adjustable in advanced settings (0.1–1024 GiB); retention removes sealed old rounds while protecting the current round. It records each model input/output and before/after tracker state through one bounded background writer; daily key-event logs rotate automatically. Queue/memory backpressure recovers automatically with explicit gap records. Disk/write failures still pause diagnostics. Replay and one-click export are not implemented. Details: [verification and troubleshooting](docs/排查与验证.md).

## Support this project

If this project has helped you, optional donations via the [WeChat Pay or Alipay QR codes](SUPPORT.md) support its maintenance. Stars, issue reports and code contributions are welcome too.

## Contributing and license

Read [CONTRIBUTING](CONTRIBUTING.md) and [AGENTS](AGENTS.md). Please include the source revision, model/backend, layout mode, reproduction steps and relevant sanitized screenshots in bug reports.

The repository license is [MIT](LICENSE). Third-party packages, models and datasets retain their own licenses.
