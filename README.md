# Han 记牌器

基于 YOLO 深度学习的智能斗地主记牌器，**自动识别游戏窗口中的扑克牌并实时追踪剩余牌数量。**

## 主界面

![](images/1%20(1).png?raw=true)
![](images/1%20(2).png?raw=true)
## 功能特点

- 🎴 **自动识别** — 使用 YOLO 模型自动识别屏幕上的扑克牌
- 📊 **实时记牌** — 实时显示每种牌的剩余数量
- 📝 **出牌记录** — 显示上家、本家、下家所出的牌
- 🔄 **智能重置** — 长时间识别不到牌时自动重置记牌器
- ⚡ **GPU 加速** — 支持 CUDA / TensorRT / ONNX 多级加速
- 🎯 **多布局支持** — 可视化编辑适配不同斗地主软件的窗口布局
- 🎮 **多游戏支持** — 可扩展的游戏 Tracker 架构，当前支持斗地主
- ⏸️ **暂停/恢复** — 可随时暂停和恢复检测
- ⚙️ **灵活配置** — 检测间隔、重置时间、置信度等多种参数可调
- 🧪 **调试截图留存** — 可按局保存原始截图与 YOLO 标注截图，便于排查误识别

## 技术栈

| 技术 | 用途 |
|------|------|
| **PySide6** | Qt GUI 框架，构建现代化用户界面 |
| **YOLO (Ultralytics)** | 目标检测模型，高精度识别扑克牌 |
| **PyTorch** | 深度学习框架，支持 GPU/CPU 推理 |
| **TensorRT / ONNX Runtime** | 推理加速（自动导出，可选） |
| **win32gui** | Windows 窗口截图 |
| **PyYAML** | 配置管理 |
| **Pillow** | 图像处理 |

## 系统要求

- **操作系统**：Windows（依赖 win32gui 截图链路）
- **Python**：3.10+
- **GPU**：NVIDIA GPU（可选，用于加速推理）

## 安装步骤

### 1. 克隆仓库

```bash
git clone https://github.com/luckFan0519/Han_card_tracker.git
cd Han_card_tracker
```

### 2. 创建虚拟环境（推荐）

```bash
python -m venv venv
venv\Scripts\activate   # Windows
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. YOLO 模型

所有模型位于 `yolo/weights/` 下的子目录中：

```
yolo/weights/
├── yolov11n_imgsz_960/         # YOLOv11n 模型（最快）
│   └── best.pt
├── yolov11s_imgsz_960/         # YOLOv11s 模型（推荐）
│   └── best.pt
└── yolov11m_imgsz_960/         # YOLOv11m 模型（最准）
    └── best.pt
```

**更换模型**：在 设置 → 高级设置 → YOLO 模型 中直接选择。

**训练自己的模型**：作者的训练数据已发布在 Releases 中。将训练好的模型放入 `yolo/weights/` 下的新建子目录（需包含 `best.pt`），重启后即可识别。

## 使用方法

### 0. 配置游戏窗口

见下文**自定义窗口布局**
![](images/layout_debug.png?raw=true)

### 1. 启动程序

```bash
python main.py
```

### 2. 打开斗地主游戏

确保游戏窗口标题与配置中的 `window_title` 匹配（默认为"JJ斗地主"）。

### 3. 开始游戏

程序会自动识别游戏中的扑克牌并开始记牌。

### 4. 使用设置

点击"设置"按钮可调整以下参数：

**基本设置：**
- 游戏选择
- 布局配置管理（含可视化编辑 / 删除）
- 设备选择（CPU / GPU）
- 窗口置顶
- 显示出牌记录
- 显示布局状态提示（关闭后收起底部提示，故障仍会显示）

**高级设置：**
- 输出调试日志（终端输出每帧各牌区的识别结果）
- YOLO 模型选择
- 置信度阈值
- 检测间隔（0.1 秒 ~ 0.5 秒）
- 重置时间（1.0 秒 ~ 5.0 秒）
- 帧长度（1~6 帧）
- 保存游戏截图（原始截图和 YOLO 标注图）
- 实时显示当前截图
- 显示耗时（标题栏推理/整轮耗时）

## 配置说明

### 运行时配置（`config/config.yaml`）

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `detect_interval_sec` | 检测间隔（秒） | 0.2 |
| `reset_time` | 无目标重置时间（秒） | 3.0 |
| `frame_length` | 连续帧验证长度 | 3 |
| `device_choice` | 设备选择（`cpu` / `cuda`） | cuda |
| `yolo_model_name` | YOLO 模型子目录名 | yolov11s_imgsz_960 |
| `yolo_confidence_threshold` | YOLO 置信度阈值 | 0.6 |
| `yolo_iou_threshold` | YOLO IOU 阈值 | 0.45 |
| `always_on_top` | 窗口置顶 | true |
| `show_played_cards` | 显示出牌记录 | true |
| `debug_mode` | 输出调试日志 | false |
| `save_debug_images` | 保存游戏截图和 YOLO 标注图 | false |
| `show_current_screenshot` | 独立窗口实时显示当前捕获的游戏截图 | false |
| `show_layout_status` | 显示底部布局状态提示，不影响故障提示 | true |
| `show_timing` | 标题栏显示耗时 | true |
| `little_joker_shown` | 小王显示字符（出牌记录） | 🃟 |
| `big_joker_shown` | 大王显示字符（出牌记录） | 🃏 |

> **注意：** `little_joker_shown` 和 `big_joker_shown` 仅影响出牌记录区域的显示，不影响主界面牌名显示。

### 游戏配置（`config/games/`）

每个游戏一个 YAML 文件，定义牌组、出牌区域和布局区域。当前包含 `doudizhu.yaml`。

### 调试截图（`save_debug_images`）

- 保存内容为"成对帧"：原始截图 → `games_images/row/game_N/`，YOLO 标注图 → `games_images/yolo/game_N/`
- 每局最多 `1000` 张；达到上限后不再保存，仅打印一次提示
- 最多保留最近 `3` 局；新局创建时自动清理更旧目录
- `debug_mode` 仅控制终端日志输出，与图片保存分开控制
- `show_current_screenshot` 独立控制当前截图预览；开启后在独立窗口查看实时采集画面，不依赖调试日志或图片保存开关
- 预览窗口初始宽度与记牌器主窗口一致，图像按比例缩放。点叉或 Esc 会关闭实时预览，并将设置保存为“否”；从高级设置可重新开启。直接退出记牌器则保留当前显示偏好。

### 自定义窗口布局

在 **设置 → 基本设置 → 自动布局…** 中选择游戏窗口，可让程序根据检测到的牌自动定位，无需手工框选。手动布局仍可通过 **可视化编辑** 添加。

自动布局需要在开局、尚未出牌时校准：连续 `max(3, FRAME_LENGTH)` 帧识别到本家完整的 17/20 张手牌和三张底牌，并且本家手牌之外只出现这组三张底牌。若两家手牌在当前界面也可见，或其他牌组被 YOLO 检出，程序会继续显示等待校准状态，不会将其当作已定位。校准成功后会显示“自动布局：已定位，正在跟踪出牌”。

如果牌桌在同一窗口内重新排布，而截图宽高比没有明显改变，旧的自动布局参照可能无法识别新位置。请在下一局开始且尚未出牌时点击“重置记牌器”，再等待自动布局完成校准。宽高比相对校准时变化超过 5% 或连续确认底牌变化时，程序也会清除旧参照并重新校准。自动布局面向本家在下、两家在左右的常见界面；详细机制和限制见[自动布局设计说明](docs/自动布局设计说明.md)。

手动布局可通过 **设置 → 基本设置 → 可视化编辑** 快速添加。

也可在 `config.yaml` 中手动添加：

```yaml
window_layouts:
  你的斗地主:
    window_title: "你的斗地主窗口标题"
    layout:
      player_hand: [x1, y1, x2, y2]      # 玩家手牌区域
      player_played: [x1, y1, x2, y2]    # 本家出牌区域
      opponent_left: [x1, y1, x2, y2]    # 上家出牌区域
      opponent_right: [x1, y1, x2, y2]   # 下家出牌区域
      landlord_cards: [x1, y1, x2, y2]   # 地主底牌区域
```

区域坐标为归一化坐标（0.0~1.0），表示相对于窗口的位置。

> **注意：** 使用可视化编辑时，请将目标游戏窗口置于屏幕最前方，确保完全可见、不被遮挡。同一软件不同模式下的扑克牌布局可能不同，需按实际调整。

## 项目结构

```
han-card-tracker/
├── main.py                          # 程序入口
├── requirements.txt                 # 依赖包
├── config/
│   ├── settings.py                  # 配置管理（运行时常量 + 持久化函数）
│   ├── config.yaml                 # 运行时配置文件
│   └── games/
│       └── doudizhu.yaml            # 斗地主游戏配置
├── core/
│   ├── game_controller.py          # 游戏控制器（编排截图→推理→状态更新）
│   ├── inference_process.py        # 多进程推理 Worker（主进程侧）
│   ├── yolo_inferencer.py          # YOLO 推理器（纯视觉推理）
│   ├── layout_analyzer.py          # 布局几何分析（分区 + 二维排序）
│   ├── base_tracker.py             # 牌局状态跟踪器基类（三阶段状态机）
│   ├── screen_capture.py           # Windows 窗口截图
│   ├── image_saver.py              # 游戏图片按局保存与自动清理
│   └── games/
│       ├── __init__.py             # Tracker 工厂 + 注册表
│       └── doudizhu.py             # 斗地主 Tracker 子类
├── ui/
│   ├── main_window.py              # 主窗口（CardUI）
│   ├── settings_dialog.py         # 设置对话框
│   ├── layout_editor_dialog.py     # 可视化布局编辑器
│   ├── styles.py                  # QSS 样式加载
│   └── ui.qss                     # QSS 样式表
├── utils/
│   ├── trans_yolo_names_to_string.py  # 牌名转换工具
│   ├── add_layout/                    # 命令行布局工具
│   │   ├── draw_layout.py
│   │   └── list_windows.py
│   └── layout_editor/                 # 布局编辑器核心逻辑
│       ├── coord.py
│       ├── service.py
│       └── validator.py
├── games_images/                   # 游戏截图输出目录（运行时生成）
│   ├── row/game_N/                 # 原始截图
│   └── yolo/game_N/               # YOLO 标注图
├── yolo/
│   └── weights/                    # 所有模型均在子目录中
│       ├── yolov11n_imgsz_960/
│       ├── yolov11s_imgsz_960/
│       └── yolov11m_imgsz_960/
├── AI开发标准/                      # AI 辅助开发规范
│   ├── 开发总原则.md
│   ├── Python 代码注释规范.md
│   └── Python 类型注解规范.md
└── docs/                           # 架构文档
    ├── class-diagram-overview.md
    ├── class-diagram.md
    └── sequence-diagram.md
```

## 工作原理

### 架构概览

程序采用**多进程架构**，将 YOLO 推理放到独立子进程中运行，彻底绕过 GIL，保证 UI 流畅：

```
主进程 (UI)                              子进程 (推理)
┌────────────────┐                      ┌──────────────────────┐
│ CardUI          │  ──cmd_queue──►     │  GameController      │
│  (QMainWindow)  │                      │   ├─ ScreenCapture   │
│  InferenceWorker│  ◄─result_queue──   │   ├─ YoloInferencer  │
│  (QObject)      │                      │   ├─ LayoutAnalyzer  │
└────────────────┘                      │   ├─ ImageSaver      │
                                        │   └─ Tracker 子类    │
                                        └──────────────────────┘
```

### 检测流水线

1. **窗口截图** — `ScreenCapture` 使用 Win32 API 截取游戏窗口
2. **YOLO 检测** — `YoloInferencer` 识别窗口中所有扑克牌（仅纯视觉推理）
3. **几何分区** — `LayoutAnalyzer` 按布局配置将检测框分区并做二维排序
4. **业务映射** — `Tracker` 将 YOLO 标签转换为牌点名称
5. **状态机处理** — 三阶段状态机管理游戏流程：
   - `WAIT_BEGIN`：等待地主底牌稳定
   - `HAS_STARTED`：检测玩家手牌稳定
   - `STARTED_RECORD_CARD`：记录出牌
6. **连续帧验证** — 连续 N 帧相同才确认出牌，避免误识别
7. **更新 UI** — 通过信号通知主窗口更新剩余牌数量和出牌记录

### 模型加载策略

- **GPU**：TensorRT（FP16）> PyTorch CUDA + FP16
- **CPU**：ONNX Runtime > PyTorch CPU

ONNX / TensorRT 引擎会在首次使用时自动从 `best.pt` 导出。

## 常见问题

### Q: 识别不准确怎么办？

A: 可以尝试以下方法：
1. 调整置信度阈值（降低阈值会识别更多但可能误检）
2. 增加帧长度（提高连续帧确认数）
3. 检查窗口布局配置是否正确
4. 确保游戏窗口完全可见，不被遮挡
5. 尝试使用更高精度的模型（如 yolov11m）

### Q: 程序卡顿怎么办？

A: 可以尝试：
1. 增加检测间隔时间
2. 使用 GPU 加速（如有 NVIDIA 显卡）
3. 使用更小的模型（如 yolov11n）

### Q: 如何适配其他斗地主软件？

A: 可先通过 **设置 → 基本设置 → 自动布局…** 选择游戏窗口；若界面不符合自动布局的适用条件，可在 **可视化编辑** 中手动添加窗口布局。

### Q: 自动布局一直在等待校准怎么办？

A: 请在开局、尚未出牌时启动或重置记牌器，并确认本家完整手牌和三张底牌可见。自动校准要求本家之外没有其他被检出的牌组；如果两家手牌在该界面也可见，当前校准条件无法通过。状态提示“等待三张底牌及尚未出牌的开局画面”表示开局条件未满足，不一定代表底牌本身没有被检测到。

### Q: 如何更换 YOLO 模型？

A: 在 设置 → 高级设置 → YOLO 模型 中直接选择。也可将自定义模型放入 `yolo/weights/` 下的新建子目录（需包含 `best.pt`），重启后即可识别。

## 许可证

本项目采用 MIT 许可证 — 详见 [LICENSE](LICENSE) 文件。

## 免责声明

本工具仅供学习和娱乐使用，请勿用于任何商业用途或违反游戏服务条款的行为。


## 开发回归验证

当前维护范围为斗地主。配置保存由主进程统一处理；布局、模型、设备等切换等待推理进程成功回执后确认。识别进程异常时，窗口状态栏显示原因及“重启识别”入口；重启会重新初始化牌局。

```bash
python -B -m unittest discover -s tests -v
```

测试依赖项目中的 PyYAML、Pillow、PySide6 和 Windows 截图模块，不加载真实 YOLO 模型。覆盖扣牌规则、帧长度热更新、配置保存失败、布局同步、组件回退、旧结果丢弃、故障恢复、Qt 控件和 Windows spawn。配置写入测试只操作 `tests/.tmp/` 下的临时文件，Qt 测试采用 offscreen 平台。

真实游戏截图、模型推理和打包版本仍需实机检查。测试前后的截图及牌局输出可用于对照验证。设计说明见 [斗地主记牌设计说明](docs/斗地主记牌设计说明.md)，进程交互见 [顺序图](docs/sequence-diagram.md)。
