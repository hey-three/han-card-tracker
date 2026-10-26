# AGENTS 指南（han_card_tracker）

**重要：每次修改代码/文件结构/配置项后，必须主动检查本文件（AGENTS.md）是否需要同步更新。** 若架构、数据流、配置项、文件路径等发生了变化，应及时修正本文件中的对应描述，确保文档始终与代码一致。

## 0) AI 开发标准（写代码前必读）

项目在 `AI开发标准/` 目录下维护了代码风格规范，**所有新增/修改代码必须遵守**：

- **`AI开发标准/开发总原则.md`**：效率与优美优先、变更策略、输出要求。
- **`AI开发标准/Python 代码注释规范.md`**：
  - 所有函数/方法/类必须写 Google 风格 Docstring（中文说明 + 英文关键字：`Args / Returns / Raises / Examples / Attributes / TODO`）。
  - 类型注解必须完整（函数参数、返回值；类属性在 `Attributes` 写明）。
  - 异常必须在 `Raises` 中逐条写清触发条件。
  - 示例必须可运行（`>>>` 形式）。
  - 输入校验：类型不对抛 `TypeError`，取值/业务约束不满足抛 `ValueError`。
- **`AI开发标准/Python 类型注解规范.md`**：
  - 优先使用现代写法（Python 3.10+）：`X | Y`、`list[str]`、`dict[str, int]`。
  - 每个函数必须标注参数类型 + 返回值类型；无返回值用 `-> None`。
  - 可选值必须显式写出 `None`：`str | None`。
  - 不要滥用 `Any`；能用 `TypedDict` / `Protocol` / `Mapping` 表达就不用 `Any`。
  - 集合/序列参数优先用抽象类型：只读用 `Sequence[T]` / `Mapping[K, V]`，会修改用 `list[T]` / `dict[K, V]`。

**硬性约束**：输出的每一个 `def` 都必须带 Docstring，每一个 `class` 都必须带类 Docstring 并为 `__init__` 写 Docstring。

## 1) 先看什么（最快进入状态）

- 入口是 `main.py`：创建 `QApplication`、加载 `ui/ui.qss`、启动 `CardUI`。
- 业务主链路：`ui/main_window.py` → `core/inference_process.py` → `core/controller_process.py` → `core/game_controller.py` → `core/games/<game>.py` → `core/base_tracker.py` → `core/yolo_inferencer.py` → `core/screen_capture.py`。
- 配置集中在 `config/settings.py`（运行时常量 + 校验/保存入口）与 `config/config.yaml`（可编辑参数）；`config/store.py` 提供严格读取和原子写入。
- 设置操作由 `ui/settings_controller.py` 协调，进程协议定义在 `core/inference_protocol.py`。
- 游戏配置在 `config/games/` 目录下，每个游戏一个 YAML 文件（如 `doudizhu.yaml`）。
- AI 开发规范详见 `AI开发标准/` 目录（开发总原则 + 代码注释规范 + 类型注解规范），已在第 0 节概述。

## 2) 架构与数据流（改逻辑前必须理解）

### 多进程架构

- **主进程**（UI）：`CardUI`（`ui/main_window.py`）+ `InferenceWorker`（`core/inference_process.py`）
- **子进程**（推理）：`GameController`（`core/game_controller.py`）+ 五个核心组件
- 通信方式：`multiprocessing.Queue`（命令队列 `cmd_queue` + 结果队列 `result_queue`）
- UI 不因推理耗时而卡顿；子进程不操作任何 Qt 对象

### 命令与回执协议

协议定义在 `core/inference_protocol.py`，由 `core/controller_process.py` 执行。

- 命令为 `Command(request_id, generation, name, payload)`；`None` 终止进程。
- `request_id` 唯一标识请求；重置及配置切换递增 `generation`，旧代次检测结果丢弃。
- `detect` / `reset` / `touch_time` 不需要参数。
- `switch_layout` 携带 `{"name": 布局名, "config": 完整布局}`，含窗口标题和全部区域坐标；不能只传名称。
- `switch_model` / `switch_device` / `switch_game` 携带名称；`update_settings` 携带受白名单限制的属性字典。
- 回执为 `Response`：`ready` 为启动成功，`fatal` 为启动失败，`ok` / `error` 为指定请求的执行结果。
- 检测结果字段为 `remain_cards`、`zone_cards` 和 `yolo_ms`；实时截图预览启用时还包含 `screenshot_jpeg` 与自动布局确认的 `screenshot_regions`；控制命令也必须回执。
- `finished` 仅用于检测结束或进程故障，不用设置命令的回执解锁其他检测请求。
- 回传的牌数和出牌记录在入队前复制，避免队列延迟序列化时被后续命令修改。

### 检测流水线

`GameController.detect()` 执行以下五步：

1. **截图**：`ScreenCapture.capture_window()` → `PIL.Image | None`
2. **YOLO 纯视觉推理**：`YoloInferencer.detect(img)` → `(raw_result, yolo_ms)`
3. **几何分区 + 二维排序**：`LayoutAnalyzer.parse_and_sort(raw_result, img.size)` → `dict[str, list[dict]]`
4. **业务映射**：`Tracker.translate_boxes_to_cards(region_boxes)` → `dict[str, list[str]]`
5. **状态机更新**：`Tracker.get_cards_number(frame_data, yolo_ms)` → `(remain_cards, show_cards, yolo_ms)`

### 分区与排序

- 分区依据检测框中心点是否落在 `window_layouts[*].layout` 区域内；区域坐标为归一化（0~1）
- 区域键名从游戏配置的 `layout_regions` 动态获取
- 排序不是置信度顺序：`LayoutAnalyzer.sort_cards_by_topright_rowwise()` 先按行（top-y 聚类）再按列（right-x 排序），避免出牌串顺序错乱
- 自动布局 `mode: auto` 时，第 3 步改为 `core/auto_layout.py` 的 `read_yolo_boxes()` + `AutoLayoutAnalyzer.observe()`；只需窗口标题和空 `layout: {}`，不使用手工矩形。旧配置缺省 `mode: manual`，完全保留手动方式。
- 自动模式按行组成牌组、合并相邻行，再对完整牌组确定归属；左右家使用对应边缘、本家使用中心和第一行顶边锚点，支持单张、长牌组和两行出牌。多重集排序避免同牌换行重复扣减。
- 短末行不能因初始位置评分落在本家就被拆走：相邻行同侧、同列对齐且牌框尺度一致时，先合并再按完整牌组归属；近期手牌减少或此前单独稳定的本家出牌可阻止误合并。实测框回归位于 `tests/fixtures/right_wrapped_play.json` 与 `tests/test_wrapped_ownership.py`。
- 校准需要无出牌的完整 17/20 张手牌和三张底牌，连续 `max(3, FRAME_LENGTH)` 帧稳定；`DoudizhuTracker.initialize_from_layout()` 只发布一次初始扣牌。当前支持本家在下、两家在左右的常见界面，不宣称覆盖所有软件。
- `LayoutObservation.regions` 缺失键代表未知，有键空列表才表示空区。自动模式传入 `known_regions`，未知区域清确认缓存但保留出牌/空帧历史；不得把未知补成空列表。校准后底牌隐藏可继续跟踪。
- 布局状态通过 `Response.layout_status` → `InferenceWorker.layout_status_changed` 显示在状态栏。重置、模型切换、长宽比显著变化或超时会清除自动布局参照。详见 `docs/自动布局设计说明.md`。

### 游戏边界与现有分层

- 当前开发范围仅为斗地主，不合入够级分支；保留现有 Tracker 分层。
- `BaseCardTracker`（`core/base_tracker.py`）为抽象基类
- 子类在 `core/games/` 目录下（如 `DoudizhuTracker`）
- 工厂函数 `create_tracker()` 根据 `TRACKER_REGISTRY` 动态创建
- 注册表只启用 `doudizhu`；当前 UI 不支持切换到其他游戏，不能仅添加 YAML 就视为完成新游戏支持。

## 3) 状态机约定（核心业务规则）

### 三阶段状态机

状态常量在 `config/settings.py`：

```
WAIT_BEGIN (0) → HAS_STARTED (1) → STARTED_RECORD_CARD (2)
                                       ↓ (超时无目标)
                                    WAIT_BEGIN (0)
```

### 帧有效性门禁

- `BaseCardTracker._presses_one_frame()` 以 `_get_validity_region()` 返回的区域是否为空作为"本帧是否有效"的门禁
- 空则直接丢弃，不更新状态也不重置 `no_target_time`
- 子类实现 `_get_validity_region()` 指定判断区域（斗地主用 `"landlord_cards"`）
- 上述门禁适用于手动模式。自动模式显式提供 `known_regions`，只更新已确认区域；全帧无可靠目标不推进状态机。

### 连续帧确认

- 由 `FRAME_LENGTH` 决定，`_check_card()` 要求缓存帧长度达到阈值且所有帧完全一致才算稳定
- 每次追加后将缓存裁剪到最新 `FRAME_LENGTH` 帧；运行中调小阈值，下一帧立即按新长度确认，调大则等待足够帧。

### 扣牌规则

- 扣牌只在确认时发生：`_delete_played_cards()` 直接减少 `remain_cards`
- 为支持"连续两次出相同牌"，使用 `has_found_empty` 字典处理"不出牌空帧"过渡

### 超时重置

- `get_cards_number()` 中若超过 `RESET_TIME` 未见有效目标则 `reset()`

### 子类必须实现的抽象方法

| 方法 | 说明 |
|------|------|
| `_get_validity_region()` | 返回帧有效性判断区域键名 |
| `should_start_game()` | 判断是否满足开始游戏条件 |
| `should_start_recording()` | 判断是否满足开始记牌条件 |
| `on_game_started()` | 游戏开始时的回调 |
| `on_start_recording()` | 开始记牌时的回调 |
| `process_played_cards(zone_key, cards)` | 处理确认的出牌 |

### 斗地主特有逻辑（`DoudizhuTracker`）

- 开始游戏条件：地主底牌连续帧稳定
- 开始记牌条件：玩家手牌连续帧稳定；开始记牌时扣减手牌
- 出牌处理：上家/下家出牌扣减剩余牌数；本家出牌仅记录不扣减（已在 `on_start_recording` 中扣减）
- 统计口径：`remain_cards` 排除本家初始手牌及已确认的对手出牌；本家手牌持续可见不应重复扣减，本家出牌也不应再次扣减。
- 设计依据与识别时序的验证边界见 [`docs/斗地主记牌设计说明.md`](docs/斗地主记牌设计说明.md)；审查时应区分扣牌设计与初始手牌识别条件。

## 4) 配置与持久化模式

### 配置层级

| 配置来源 | 文件/模块 | 说明 |
|----------|-----------|------|
| `config/config.yaml` | 运行时可编辑参数 | 用户可直接修改 |
| `config/games/*.yaml` | 游戏配置 | 牌组、出牌区域、布局区域 |
| `config/settings.py` | 运行时常量 + 加载/保存函数 | 不直接编辑 YAML |

### 关键约定

- 配置文件只由主进程写入，入口为 `config/settings.py` 的 `save_*` / `save_setting()`；子进程只调用内存应用函数。
- `config/store.py` 统一使用同目录唯一临时文件 + `os.replace` 原子保存；保存成功后才更新主进程内存。
- 已有 YAML 损坏或读取失败时必须报错，不得以空字典覆盖；保存失败向调用者传播，编辑器不能误报成功。
- `SettingsController` 等待后端成功回执后保存设置并刷新 UI；后端切换失败保留旧配置和组件。后端已成功但保存失败时停止检测，提示用户检查后重启。
- 实时预览属于主界面偏好：先保存 `show_current_screenshot` 并更新窗口，再同步子进程的截图传输开关；因此关闭预览后，即使进程故障或立即退出，也不会丢失关闭选择。
- 服务故障时允许在设置界面修正持久化配置，点击“重启识别”后由新进程使用最新快照。
- 可视化编辑先保存布局内容（不切换 current_layout）；主窗口在布局切换确认后保存当前布局名。
- 删除当前布局时先等待替代布局切换成功，再删除持久化记录；切换失败保留原布局。
- `TOTAL_CARDS`、`PLAYED_ZONES`、`LAYOUT_REGIONS` 从游戏配置加载；`YOLO_TO_CARD_MAPPING` 从 `config.yaml` 加载
- 设备切换通过 `switch_device` 命令即时生效，子进程重建 `YoloInferencer`
- 底层保留 `switch_game` 命令，子进程只加载内存配置并重建 `Tracker`；当前 UI 仅支持斗地主。
- 模型切换通过 `switch_model` 命令即时生效，子进程重建 `YoloInferencer` + `Tracker`
- 布局切换通过 `switch_layout` 命令即时生效，子进程重建 `LayoutAnalyzer` + `ScreenCapture` + `Tracker`

### 资源目录解析

- `BASE_DIR`：可写运行目录（项目根目录或 exe 所在目录）
- `RESOURCE_DIR`：资源读取目录（兼容 PyInstaller 的 `_internal` 布局）
- `_resolve_resource_dir()`：智能解析，支持打包后从 `_internal` 读取资源
- `_ensure_runtime_config_exists()`：冻结环境下首次运行时复制配置到可写目录

## 5) UI 交互与进程边界

### 进程隔离

- 推理在独立子进程中运行，不与 UI 主进程共享 GIL
- `InferenceWorker` 是主进程中的 `QObject`，通过轮询定时器（20ms）从结果队列读取数据，以信号形式通知 UI
- **不要在子进程中操作任何 Qt 对象**；子进程只能通过 `result_queue` 回传纯 Python 数据

### UI 更新机制

- 生命周期为 `stopped → starting → ready`，启动失败、进程退出或通信失败进入 `failed`。
- 定时触发：UI 用 `QTimer` + `_busy` 防抖；Worker 仅在 ready 且无未完成命令时接受检测，UI 只在发送成功后设置 busy。
- 启动错误通过 fatal 回执显示在状态栏；推理依赖延迟到子进程异常边界内导入。
- 故障停止检测并显示“重启识别”按钮；恢复创建新进程和新队列、重置牌局，保留用户暂停状态。
- 关闭时停止轮询并回收队列；不等待向已死亡进程输送残留命令。
- 增量刷新：通过 `_last_count_values` / `_last_depleted_values` / `_last_played_signature` 缓存，仅在状态变化时更新文本/样式
- 样式依赖动态属性：`depleted` 与 `count`，更新后需 `unpolish/polish` 触发 QSS 刷新

### 窗口置顶

- 调整窗口置顶仅在标志变化时调用 `setWindowFlag`，保留窗口几何和可见性，不重复 `adjustSize()`；截图不改变置顶标志。
- 主窗口按整个 `QMainWindow` 的布局计算最小高度，包含状态栏；出牌标签始终保留布局归属，仅切换可见性，避免反复开关后丢失标签。
- `show_layout_status`（默认 true）在“基本设置 → 显示布局状态提示”控制底部正常布局提示；隐藏时收起状态栏，故障信息及恢复按钮始终可见。
- 设置对话框打开期间暂停定时器，关闭后调用 `_touch_no_target_time()` 防止立即超时重置

### 出牌记录显示

- 由 `_show_played_cards` 控制，通过 `_update_played_cards_visibility()` 切换标签可见性，不从布局移除标签
- 出牌区域标签从游戏配置的 `played_zones` 动态生成
- `played_cards_labels` 为 `dict[str, QLabel]`，key 为区域键名

## 6) 扩展工作流（布局/调试）

### 可视化布局编辑（推荐）

“设置 → 基本设置 → 自动布局…”可选择目标窗口，无需框选；选择后等待后端成功回执再保存一个 `mode: auto` 布局。关闭设置后开始校准，请在开局前启用。自动生成的参照不写入 YAML，重新启动/重置时重新学习。切到“可视化编辑”时为自动模式创建新的手动布局，不覆盖自动配置。

在主界面"设置"→"基本设置"页中点击"可视化编辑"，打开 `LayoutEditorDialog`：

1. 选择目标窗口标题
2. 点击"截图"（自动下沉本程序窗口避免遮挡）
3. 在画布上框选五个牌区：玩家手牌 / 本家出牌 / 上家出牌 / 下家出牌 / 地主底牌
4. 点击"预览校验"确认区域标注正确
5. "保存"写入 `config.yaml`

截图时窗口下沉/恢复通过 `_send_app_windows_to_bottom()` / `_restore_app_windows_after_capture()` 完成；位置和客户区尺寸统一使用 Qt 逻辑坐标，不混用 Windows 原生像素或边框尺寸。截图期间防止重复进入，失败也恢复窗口。画布及预览的延迟刷新使用随窗口销毁的子定时器。

设置页面提供 ↻ 刷新按钮手动刷新布局列表，支持在布局下拉项中直接删除布局。

### 命令行工具（高级/调试用）

- `utils/add_layout/list_windows.py`：列出窗口标题
- `utils/add_layout/draw_layout.py`：手动绘制区域

### 运行主程序

```bash
python main.py
```

## 7) 调试截图资产约定

- 统一由 `core/image_saver.py` 管理，不要在 `YoloInferencer` / `BaseCardTracker` 外部手写落盘逻辑
- 保存内容是"成对帧"：原始截图到 `games_images/row/`，YOLO 标注图到 `games_images/yolo/`，同局同帧同名
- 每局目录命名固定为 `game_N`（`game_1`, `game_2`, ...），帧命名固定为 `1.png` 递增
- 单局最多 `1000` 张；达到上限后该局后续帧不保存，并且仅打印一次提示
- 全局只保留最近 `3` 局；新局创建后会清理更旧目录
- **保存图片与调试模式分开控制**：`SAVE_DEBUG_IMAGES` 控制是否保存游戏图片，`DEBUG_MODE` 仅控制终端日志输出
- `SHOW_CURRENT_SCREENSHOT` 独立控制实时截图预览；开启时子进程压缩当前截图，并将自动布局当前确认的各牌组整体边界随检测回执发送，主进程叠加绘制区域框与名称；校准或归属不确定的区域不画框，关闭时不传截图数据
- “高级设置”分别提供“输出调试日志”“保存游戏截图”“实时显示截图”，三者互不依赖。实时预览初始宽度与主窗口一致，等比例缩放图片；点叉或 Esc 会保存关闭偏好并同步设置项，不因后续截图重新弹出。退出主程序只隐藏预览，不修改该偏好。
- `ImageSaver` 由 `GameController` 统一管理，传入 `YoloInferencer` 和 `Tracker` 子类
- 运行时热切换：`GameController.update_settings()` 中通过 `set_enabled()` 切换保存开关，不需要重建 `ImageSaver` 实例
- 启动时执行 `ImageSaver.bootstrap_static(BASE_DIR, SAVE_DEBUG_IMAGES)` 初始化目录

## 8) 耗时统计约定

- **YOLO 推理耗时**：仅包含 `self.model(...)` 调用，不含截图和保存图片时间；在 `YoloInferencer.detect()` 中用 `time.perf_counter()` 计时
- **整轮耗时**：从 `request_one_update()` 发出检测请求到 `on_result_ready()` 收到结果，包含截图+推理+队列通信+轮询延迟
- 标题栏显示格式：`Han记牌器  ·  推理 XXms  ·  整轮 XXms`，由 `SHOW_TIMING` 配置项控制是否显示

## 9) 环境与集成边界

- `.vscode/` 与 `games_images/` 是本地目录，由 `.gitignore` 排除，不加入 Git 提交；取消跟踪时仅移除索引，保留本地文件。

- 当前实现依赖 Windows 截图链路（`win32gui/win32ui/win32con` + DPI Aware），默认是 Windows 场景
- YOLO 推理在独立子进程中运行（`multiprocessing`），主进程（UI）与子进程不共享 GIL；`main.py` 必须调用 `multiprocessing.freeze_support()` 以支持 PyInstaller 打包
- YOLO 权重在 `yolo/weights/` 目录下的子目录中（如 `yolo/weights/yolov11s_imgsz_960/best.pt`），不支持直接放在 `weights/` 下；通过设置界面切换模型
- 模型加载策略：
  - GPU（`device_choice == "cuda"`）：TensorRT（FP16）> PyTorch CUDA + FP16
  - CPU（`device_choice == "cpu"`）：ONNX Runtime > PyTorch CPU
  - ONNX / TensorRT 引擎在首次使用时自动从 `best.pt` 导出
- 关键三方：`ultralytics`、`torch`、`PySide6`、`opencv-python`、`PyYAML`、`pillow`
- 回归测试位于 `tests/`，运行 `python -B -m unittest discover -s tests -v`。配置测试仅使用 `tests/.tmp/` 下的独立临时文件；Qt 测试使用 offscreen 平台。
- 自动测试覆盖斗地主扣牌、帧阈值、配置原子保存、布局同步、切换回退、请求隔离、故障恢复及真实 spawn 启动错误回传，不加载真实 YOLO 模型。
- 实际游戏截图、CUDA/ONNX/TensorRT 推理及打包版本仍需在具备模型依赖的环境中回归。
