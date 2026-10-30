# Han Card Tracker — Dou Dizhu Card Counter for Windows

[简体中文](README.md) · [Documentation](docs/README.md) · [Issues](https://github.com/hey-three/han-card-tracker/issues) · [YOLO training repository](https://github.com/hey-three/yolo-for-han-card-tracker) · [❤ Support](SUPPORT.md)

**Han Card Tracker is a Windows desktop application for Dou Dizhu (斗地主) card counting.** It uses YOLO playing-card detection to recognize cards in a visible game window, track remaining cards outside the user's initial hand, and display play history. The application is built with Python, PySide6 and PyTorch.

V4.3.3 uses a centered 70% horizontal bottom-card region with a learned, round-stable height. Preview labels are 上/下/本/底/手 and show the actual bottom-card decision region. Settings remain open while background recognition continues.

![Han Card Tracker with vertically arranged panels](images/interface1.png)

![Han Card Tracker with horizontally arranged panels](images/interface2.png)

## Usage notes

- **Prefer original-image inference with the bundled models.** Their training data consists entirely of original game screenshots. Packed inputs are not yet adequately represented, and currently have lower recognition accuracy and stability. Manual layouts can switch to original inference; automatic layouts always use the full image.
- **Include all three J, O and K letters when selecting joker regions.** The training annotations cover those three letters. Selecting only J or J/O can cause missed or incorrect detections.

## Features

- YOLO object detection with CPU/CUDA execution and optional ONNX Runtime/TensorRT backends.
- Automatic layout learning for common three-player interfaces, or five user-defined regions.
- Original-image or packed inference for manual layouts; automatic layouts always use the full image.
- Two-stage packing: bottom cards and initial hand first, then bottom cards and the three play areas after the initial hand has been deducted.
- Strict user-region crops with no added margins, fixed initial scaling, and online scale estimates from individual card detections.
- A fixed pixel canvas per model's `model_config.yaml`, overlap-aware coordinate restoration and duplicate removal. New weights must include this sidecar; square and rectangular inputs use their declared dimensions.
- Pause/resume, always-on-top UI, play history, timing, actual YOLO input preview, and paired original/annotated screenshots.
- Separate remaining-card and play-history panels, with stacked or side-by-side arrangements in Basic Settings.

This repository currently maintains the **Windows Dou Dizhu application**. It does not ship an Android APK, use OCR for rank recognition, or implement other card games. It does not control the game. Compatibility and accuracy must be checked for each interface and skin.

## Source installation

Use Windows and 64-bit Python 3.11. Alternatively, download the CPU or CPU/GPU Windows ZIP from [Releases](https://github.com/hey-three/han-card-tracker/releases/tag/v4.3.3). The [usage guide](docs/使用指南.md) covers EXE operation and settings first, then source installation.

```powershell
git clone https://github.com/hey-three/han-card-tracker.git
cd han-card-tracker
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu121
.\.venv\Scripts\python.exe main.py
```

The dependency file pins CUDA 12.1 PyTorch wheels. Follow the [official PyTorch instructions](https://pytorch.org/get-started/previous-versions/) rather than assuming the default index or any Python version will work. [Installation details](docs/使用指南.md) are maintained in Chinese.

Weights are stored in `yolo/weights/<model-name>/best.pt`. Custom models must preserve the configured class mapping. The [training project](https://github.com/hey-three/yolo-for-han-card-tracker) is maintained separately.

## First use

1. Open the game window and keep it visible.
2. Create a named automatic preset in basic settings and select its game window, or define the five regions in a manual preset. Each mode remembers its last successfully used preset; clicking a mode applies it.
3. For a manual layout, choose packed or original inference under Basic settings → Manual layout. This preference applies to all manual layouts.
4. Start before the round begins and inspect the actual YOLO input preview.

Any bottom card in two consecutive screenshots opens a round; card identities and counts need not match. The second screenshot begins initial-hand confirmation using the configured stable-frame threshold. A stable, nonempty legal hand is deducted once; an exact 17/20-card hand is not required. The count excludes the user's initial hand and confirmed opponent plays; the user's plays only add history and never trigger another deduction or retroactive initial-hand correction.

Missing bottom cards pause updates. A consecutive-frame and elapsed-time threshold ends the round internally while preserving the displayed history until the next round starts. See the [counting contract](docs/斗地主记牌设计说明.md).

## Releases and verification

[Releases](https://github.com/hey-three/han-card-tracker/releases/tag/v4.3.3) provides a CPU-only ZIP without CUDA libraries and a larger CPU/GPU ZIP supporting CPU and compatible NVIDIA CUDA GPUs. Extract the entire archive before launching HanCardTracker.exe. See [CHANGELOG](CHANGELOG.md).

The diagnostic update passed 203 unit tests, including a real Windows spawn/worker-shutdown test, and compared recorder modes on repeated static reference images. This does not establish complete live-game accuracy, GPU/backend compatibility, or a 200 ms latency target. Optional [full diagnostics](docs/诊断记录.md) groups evidence into per-round folders under a recording session. Its total disk budget defaults to 1 GiB and is adjustable in advanced settings (0.1–1024 GiB); retention removes sealed old rounds while protecting the current round. It records each model input/output and before/after tracker state through one bounded background writer; daily key-event logs rotate automatically. Queue/memory backpressure recovers automatically with explicit gap records. Disk/write failures still pause diagnostics. Replay and one-click export are not implemented. Details: [verification and troubleshooting](docs/排查与验证.md).

The included configuration is the maintainer's current setup. Choose your own game window/layout, model and device in basic settings, and enable or disable full diagnostics as needed in advanced settings.

## Support this project

If this project has helped you, optional donations via the [WeChat Pay or Alipay QR codes](SUPPORT.md) support its maintenance. You can also view the local QR code in the app under Settings → Support. Stars, issue reports and code contributions are welcome too.

![WeChat Pay and Alipay donation QR codes](images/QR.png)

## Contributing and license

Read [CONTRIBUTING](CONTRIBUTING.md) and [AGENTS](AGENTS.md). Please include the source revision, model/backend, layout mode, reproduction steps and relevant sanitized screenshots in bug reports.

The repository license is [MIT](LICENSE). Third-party packages, models and datasets retain their own licenses.

## Development roadmap

- Improve packed inference with actual packed training examples and evaluate resizing and slicing effects.
- Prioritize bottom-card detection: current recognition problems are concentrated in this area, where targets are small in full screenshots and fade after the landlord plays the corresponding cards. Add representative training examples and improve stability.
- Improve joker recognition and prevent changing partial detections from creating duplicate play-history entries.
- Port capture, overlay and inference workflows to Android. No Android APK is released yet.

## Star history

[![Star History Chart](https://api.star-history.com/svg?repos=hey-three/han-card-tracker&type=Date)](https://www.star-history.com/#hey-three/han-card-tracker&Date)
