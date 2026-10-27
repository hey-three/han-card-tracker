# Han 记牌器 · 斗地主记牌器 / Dou Dizhu Card Tracker

[English](README.en.md) · [使用指南](docs/使用指南.md) · [文档目录](docs/README.md) · [问题反馈](https://github.com/hey-three/han-card-tracker/issues) · [模型训练仓库](https://github.com/hey-three/yolo-for-han-card-tracker)

**Han Card Tracker 是 Windows 桌面的开源斗地主记牌器。** 使用 YOLO 扑克牌目标检测识别游戏窗口，统计本家初始手牌以外尚未打出的牌，并显示上家、本家、下家的出牌历史。项目使用 Python、PySide6 和 PyTorch，支持 CPU/CUDA 推理及自动布局、手动布局。

A Windows Dou Dizhu card counter and playing-card detection application built with Python, YOLO and PySide6. It tracks remaining cards and play history from a visible game window, with automatic layout detection or user-defined regions and packed inference.

![Han记牌器Windows界面：剩余牌数量与出牌历史，界面示意可能早于当前源码](images/1%20%281%29.png)

## 能做什么

| 功能 | 当前实现 |
|---|---|
| 扑克牌识别 | YOLO 检测牌面与坐标，经布局分析分配到对应区域 |
| 剩余牌统计 | 初始手牌扣一次；对手确认出牌扣除，本家出牌只记历史 |
| 自动布局 | 根据牌组相对位置学习常见三人斗地主界面，使用整图推理 |
| 手动布局 | 自己框选五区，可选择原图检测或两阶段拼图检测 |
| 拼图推理 | 开局前底牌＋手牌，扣手牌后底牌＋三家出牌；严格框内裁剪 |
| 尺度调整 | 初始固定比例，之后按当前单牌检测框学习；画布固定宽768×高480 |
| 运行反馈 | 暂停、置顶、出牌历史、耗时、实际 YOLO 输入预览及原图/标注图保存 |
| 推理后端 | CPU 优先 ONNX Runtime；CUDA 优先 TensorRT，失败可回退 PyTorch |

当前维护范围是**Windows 斗地主**。本仓库不提供 Android APK、OCR 文字识别方案或其他牌类游戏的完整支持；也不自动操作游戏。不同软件、皮肤和模式需要实际验证。

## 下载与版本

- 当前功能以 `main` 源码与 [更新记录](CHANGELOG.md) 为准。
- [Releases](https://github.com/hey-three/han-card-tracker/releases) 中目前可见的是历史 `v2.3` 压缩包；它不代表当前 V4.3.2 源码已打包。此状态于2026-10-27核对。
- YOLO 权重位于仓库 `yolo/weights/<模型目录>/best.pt`。训练代码在独立的 [yolo-for-han-card-tracker](https://github.com/hey-three/yolo-for-han-card-tracker) 仓库；不把旧版发布包描述成已提供训练数据集。

## 从源码启动

需要 Windows。源码采用 Python 3.10+ 语法；安装还需满足固定依赖的 Python 版本与平台要求。建议先用 Python 3.11/3.12 建立独立环境，完整步骤和限制见[安装说明](docs/使用指南.md#安装依赖)。

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

`requirements.txt` 固定了 CUDA 12.1 版 PyTorch，不能假定任意 Python 或仅用默认包索引都可安装。上述安装路径遵循 [PyTorch 官方旧版本说明](https://pytorch.org/get-started/previous-versions/)，本次文档维护没有重做全新环境安装或升级依赖。

首次使用：

1. 打开目标游戏窗口，保持可见且未被遮挡。
2. 在“设置 → 基本设置”选“自动布局…”；或通过“可视化编辑”框选手牌、本家出牌、上家出牌、下家出牌、底牌五区。
3. 手动布局可在“高级设置 → 手动布局检测方式”选择**拼图检测／原图检测**。该选择为所有手动布局共用；自动布局始终整图。
4. 建议从开局前启用，观察底部状态及“实时显示 YOLO 输入”，核对第一次扣牌与后续出牌。

## 开局与计数规则

- 底牌区检测到**任意一张牌**即进入已开局；非空合法手牌连续稳定后只扣一次，不强制17/20张，也不对后来17→20自动补扣。
- 剩余牌数 = 整副牌 − 本家首次确认手牌 − 已确认的对手出牌；不是三位玩家各自剩余张数。
- 底牌缺失立即暂停记录，连续至少 `max(3, FRAME_LENGTH)` 帧且达到 `RESET_TIME` 秒后内部结束。旧牌数与历史保留，下一次底牌出现开局才刷新。
- 开局前拼图只推理两区，即使底牌未检出也不整图校准。玩家负责框选完整；算法不向框外扩展。

完整业务规则见[记牌设计](docs/斗地主记牌设计说明.md)，拼图与自适应见[拼图设计](docs/拼图自适应与检测方式.md)。

## 常见问题

**适配 JJ 斗地主或其他窗口吗？** 仓库含 JJ 斗地主样例布局，并验证过少量参考截图；手动五区可用于适配其他窗口，但不是任何版本都可直接使用。自动布局目前面向本家在下、两家在左右的常见画面。

**为什么还没开局？** 先检查上方底牌是否落在正确区域并至少识别到一张；非空手牌还需稳定确认。中部翻牌动画不等于上方底牌已到位。

**能否保证200毫秒或一直不漏牌？** 不能。采样间隔、模型耗时、整轮耗时不同；截图、模型、皮肤、设备和温度都影响结果。已有验证范围见[排查与验证](docs/排查与验证.md)。

**如何保存排查材料？** 日常关键事件日志自动按大小轮转；高级设置可开启[完整诊断](docs/诊断记录.md)，按局建立目录，后台保存原图、每次YOLO调用输入与标注、牌数/历史和状态前后快照。高级设置可调整全部诊断材料的磁盘额度（默认1 GiB）；空间不足清理已封口旧局，保护当前局。队列/内存拥堵自动暂缓并恢复，缺帧明确记录；磁盘故障暂停并提示，识别继续；自动回放和一键导出尚未实现。

## 文档与贡献

- 用户：[使用指南](docs/使用指南.md) · [配置参考](docs/配置参考.md) · [排查与验证](docs/排查与验证.md)
- 开发者：[架构与数据流](docs/架构与数据流.md) · [自动布局](docs/自动布局设计说明.md) · [设计决策](docs/设计决策.md)
- 模型：[训练与拼图约定](docs/模型训练与拼图约定.md)
- 贡献：[CONTRIBUTING.md](CONTRIBUTING.md) · [AGENTS.md](AGENTS.md) · [维护与发布](docs/维护与发布.md)

仓库许可证为 [MIT](LICENSE)；第三方组件、模型及数据使用各自许可证。使用时遵守目标软件的适用规则。
