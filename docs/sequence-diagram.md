# 顺序图 - Han记牌器

当前范围为斗地主。进程协议见 `core/inference_protocol.py`，子进程入口见 `core/controller_process.py`。

## 1. 启动与检测

```mermaid
sequenceDiagram
    participant UI as CardUI
    participant W as InferenceWorker
    participant P as run_controller_loop
    participant G as GameController
    UI->>W: start()
    W->>P: 新进程、新队列、已确认配置快照
    P->>P: 导入推理依赖、应用内存配置
    P->>G: 创建截图器、推理器、分析器和 Tracker
    alt 启动成功
        P-->>W: Response(ready)
        W-->>UI: state_changed(ready)
        UI->>UI: 未暂停且设置关闭时启动定时器
    else 启动失败
        P-->>W: Response(fatal, error)
        W-->>UI: failed 状态和具体错误
        UI->>UI: 停止检测，显示重启识别按钮
    end
    UI->>W: request_detect()
    Note over W: 仅 ready 且没有未完成命令时允许检测
    W->>P: Command(id, generation, detect)
    P->>G: detect()
    Note over G: 截图 → YOLO → 几何分区 → 标签映射 → 状态机
    G-->>P: remain_cards, zone_cards, yolo_ms
    P->>P: 复制可变结果，避免延迟序列化串帧
    P-->>W: Response(ok/error, id, generation, ...)
    W->>W: 按 id 匹配请求，检查 generation
    W-->>UI: 有效结果才发送 result_ready
    W-->>UI: finished 释放本次检测 busy
```

## 2. 设置和布局切换

```mermaid
sequenceDiagram
    participant UI as CardUI / LayoutEditorDialog
    participant S as SettingsController
    participant W as InferenceWorker
    participant G as GameController
    participant F as settings / store
    opt 新增或编辑布局
        UI->>F: 校验并保存布局内容，暂不切换当前布局
        F-->>UI: 成功；失败则抛异常并保留旧文件
    end
    UI->>S: apply(save, request, after)
    S->>W: switch_layout(name)
    W->>W: 递增 generation，旧检测结果过期
    W->>G: Command(..., switch_layout, name + 完整布局)
    G->>G: 创建新 LayoutAnalyzer、ScreenCapture、Tracker
    alt 创建成功
        G->>G: 一起替换新组件和子进程内存配置
        G-->>W: 成功回执
        W-->>S: command_completed(id, true)
        S->>F: 主进程保存当前布局选择
        F->>F: 严格读取、临时文件、原子替换、更新内存
        S-->>UI: 更新已确认选择并清空旧牌局显示
    else 创建失败
        G-->>W: 失败回执，原组件保留
        W-->>S: command_completed(id, false, error)
        S-->>UI: 保留原设置并显示错误
    end
```

设备、模型和热更新设置使用相同的确认与保存流程。模型切换同时重建 Tracker，设备切换保留 Tracker。后端已成功但主进程保存失败时进入故障状态，停止新检测；修正配置后手动重启。

## 3. 重置和恢复

- 重置通过带请求编号的命令执行；提交时递增 generation，旧检测结果不能覆盖重置后的显示。
- 重置成功后立即清空牌数与出牌文本，包括暂停状态。
- 检测进程意外退出（包括退出码为零）或通信失败时，Worker 进入 failed，UI 停止定时器。
- “重启识别”关闭旧进程及旧队列，清空未完成命令，使用主进程已确认配置创建全新会话。
- 恢复后重新初始化牌局；若用户此前暂停，服务就绪后仍保持暂停。
- 服务故障时可先在设置中修正设备、模型、布局等持久化配置，再点击重启。

## 4. 斗地主状态机

```mermaid
stateDiagram-v2
    [*] --> WAIT_BEGIN
    WAIT_BEGIN --> HAS_STARTED: 地主底牌连续帧稳定
    HAS_STARTED --> STARTED_RECORD_CARD: 本家手牌连续帧稳定，扣减一次初始手牌
    STARTED_RECORD_CARD --> STARTED_RECORD_CARD: 对手确认出牌后扣减，本家出牌只记录
    STARTED_RECORD_CARD --> WAIT_BEGIN: 无有效目标超时或手动重置
    HAS_STARTED --> WAIT_BEGIN: 无有效目标超时或手动重置
```

本家手牌统计口径及识别时序边界见 [斗地主记牌设计说明](斗地主记牌设计说明.md)。
