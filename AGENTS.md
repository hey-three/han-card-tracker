# AGENTS 指南 · Han记牌器

每次修改代码、结构或配置后都检查本文件和相关专题文档是否需要同步。默认简体中文，正确性优先，复用已验证上下文；优先API/CLI，避免重复工具调用。高成本浏览器自动化先评估；用户已明确授权的操作不反复确认。

## 1. 先读与文档职责

- [文档目录](docs/README.md)是入口，README中英文负责项目介绍与上手，不重复详细契约。
- [架构](docs/架构与数据流.md)、[记牌规则](docs/斗地主记牌设计说明.md)、[自动布局](docs/自动布局设计说明.md)、[拼图](docs/拼图自适应与检测方式.md)是当前实现说明。
- [设计决策](docs/设计决策.md)保留长期取舍，[排查与验证](docs/排查与验证.md)区分真实证据/未测项，[CHANGELOG](CHANGELOG.md)记版本变化；不通过层叠“以上废弃”维护当前规则。
- [配置参考](docs/配置参考.md)、[模型训练](docs/模型训练与拼图约定.md)、[维护与发布](docs/维护与发布.md)随对应变更同步。

## 2. 编码要求

必须阅读AI开发标准/开发总原则.md、Python 代码注释规范.md、Python 类型注解规范.md。每个def、class和__init__均有中文Google风格Docstring；参数/返回注解完整，异常触发条件写入Raises，可运行示例使用>>>。错误类型抛TypeError，非法值抛ValueError。优先X | None、list、dict及只读Sequence/Mapping，不滥用Any。优先清晰分层与有意义回归，不为低影响文档变更添加镜像实现的测试。

## 3. 范围与入口

当前仅维护Windows斗地主，不合并够级逻辑；TRACKER_REGISTRY只启用doudizhu，新增YAML不等于新增游戏支持。Android和第三方APK分析是独立工程，不把第三方代码/权重/图片复制进来。

入口main.py建立QApplication、样式和CardUI，必须保留multiprocessing.freeze_support。主链：ui/main_window.py → inference_process.py → controller_process.py → game_controller.py → 布局/Tracker/YOLO/截图。设置由ui/settings_controller.py协调，协议在core/inference_protocol.py。

## 4. 进程与协议硬约束

- UI主进程、推理子进程独立；子进程不创建或操作Qt对象。Worker轮询20ms，UI仅在成功发送检测后置busy。
- Command(request_id,generation,name,payload)，None终止。reset/配置切换递增generation，旧结果丢弃。switch_layout必须携带完整标题与区域，不能只传名称。
- ready/fatal负责启动，ok/error回执指定请求，finished只负责检测/故障结束，不能解锁其他设置请求。结果计数、历史和performance入队前复制。
- Worker状态stopped→starting→ready，错误进入failed；恢复新建进程/队列、初始化牌局并保留用户暂停偏好。关闭回收轮询和队列，不等待向已死进程发送残留命令。
- 业务结果remain_cards、zone_cards、yolo_ms；可选screenshot_jpeg为实际YOLO输入，screenshot_regions当前空字典保留兼容。performance仅发布当前代次。diagnostic_status是独立的运行状态，不能混入牌局代次或检测busy回执。

## 5. 记牌契约

- WAIT_BEGIN见上方底牌任意一张，当前帧进HAS_STARTED；非空合法手牌按FRAME_LENGTH连续一致，扣一次后进STARTED_RECORD_CARD。无17/20硬门槛，无17→20补扣，空手牌不能通过。
- remain_cards排除本家首次手牌及已确认对手出牌。本家出牌通常只记历史；累计确认本家牌面超过原始首扣+已补扣时，只补可证明缺口。原始_initial_hand不改，initial_hand_corrections单列；越界或剩余不足记counting_conflicts不盲扣。底牌不另扣。has_found_empty维护连续两手相同牌的空区过渡。
- frame_caches只保留最新阈值帧；阈值变小时下一帧即可确认，变大需等足够帧。
- 底牌缺失暂停并清未确认序列；已知为空连续max(3,FRAME_LENGTH)帧且持续RESET_TIME秒才内部结束。同帧模型复查只算一次。未知、截图失败、暂停/恢复中断累计。
- begin_waiting保留remain_cards/show_cards，新底牌出现开局才刷新。显式reset立即清空。不按某家零张、全图无牌、底牌牌面变化额外结束。
- 缺区域键是未知，有键空列表才是已知为空。未知清该区确认缓存、保留出牌与空帧历史；未观测区不能补成空区。

## 6. 布局、拼图与尺度

- 自动mode:auto仅需窗口标题和空layout，始终整图。按行组成完整牌组、合并相邻行后归属；短末行不能因初始评分被拆给本家，多重集排序防换行重复扣。回归fixture为tests/fixtures/right_wrapped_play.json。
- 自动几何initial是定位通知，Controller不得直接初始化扣牌。底牌是业务门禁，不要求三张，也不作必需几何锚点。中央翻牌不等于上方底牌到位。
- 自动长宽比变化超过5%只重学几何，保留本局历史；未知/歧义暂停，不保证中途恢复或所有皮肤兼容。
- 旧布局缺mode视manual。PACKED_INFERENCE/packed_inference是所有手动布局共用的布尔偏好，缺省true；自动不受影响，切手动原图/拼图不重扣牌。
- 手动拼图WAIT_BEGIN/HAS_STARTED只底牌+手牌；首扣后的下一帧底牌+三家出牌，不含手牌。开局无底牌、全空或未知也不跑整图校准；记牌中可同帧复查原图。
- 用户框就是源边界，向内取整，只用1e-7像素误差容差；不向四周或下沿扩框，不为框外牌补救。内部保护重叠也只在原框内。
- Tile.source_region区分人工边界与内部切口。内部残片须完整副本覆盖85%自身面积；人工边缘可接受但有完整副本时仍优先去重。按原位置/类别去重，不能按牌点去重。
- config/packing_profile.py定义固定画布(W,H)=(768,480)、阶段区域及INITIAL_REGION_SCALES。初值底牌1.652110、手牌0.703958、上下家约1、本家出牌0.836045；不在无底牌时试探放大。
- core/packing_scales.py复用当前检测框学习普通单牌高度，排除大小王长框。三帧最高/最低≤1.2确认，10%迟滞、权重0.25～8；测准底牌和参照后优先出牌、其次手牌，底牌目标多12%。未知/空区中断未确认测量。
- 布局、截图尺寸、模型、显式重置和检测方式切换隔离学习，二区→四区保留参照。规划缓存只存几何和画布，每帧重新识别；不旋转、不拉伸裁片。make_square_plan是历史/对照接口，正式用加权make_rect_plan。

## 7. 配置、UI与资源

- 仅主进程通过settings.save_*及config/store.py写盘。同目录唯一临时文件+os.replace；成功后更新内存，读取损坏必须报错，不能空配置覆盖。
- 后端成功回执后保存并刷新；后端失败保留旧组件，保存失败停止检测并提示重启。故障时允许修正持久化参数供新进程使用。
- 预览偏好先保存/更新UI，再同步传图。点叉/Esc持久化关闭，退出主程序仅隐藏。实际预览、终端日志和完整诊断三项功能独立，完整诊断为唯一图片保存入口。
- 编辑布局先保存内容，后端确认切换再保存current_layout。删除当前布局先确认替代布局；失败不删除原记录。
- 模型/设备/布局切换重建相应组件并重置牌局。控制设置有白名单，子进程只改内存，不写YAML。
- BASE_DIR是可写运行目录，RESOURCE_DIR负责冻结资源/_internal解析；首次冻结运行复制配置到可写目录。
- UI增量更新计数/历史；出牌标签保留布局归属仅切可见性。depleted/count动态属性更新需unpolish/polish。
- 窗口置顶仅标志变化时设置并保留几何，不反复adjustSize；设置框打开暂停检测，关闭touch_time中断超时连续性。
- 截图下沉/恢复统一Qt逻辑坐标，防止重入，失败也恢复。延迟定时器随窗口销毁。show_layout_status仅隐藏正常提示，故障和恢复按钮始终可见。

## 8. 图像、计时与模型

- full_diagnostics/FULL_DIAGNOSTICS缺省false，经设置回执/原子保存启用。run_controller_loop注入DiagnosticRecorder，GameController单独使用默认不落诊断文件；进程退出finally有界收尾。
- core/diagnostics.py只在完整诊断开启时建立一个后台线程；主识别线程先预留预算、复制RGB图和CPU结果/状态再提交。每次模型调用输入必须在调用前冻结，不能把复用画布、GPU张量或Qt对象入队；异常及同帧整图回退分别留证。
- 帧数上限8（含正在写的一帧），待保存RGB+JSON预算64MiB，单帧JSON256KiB；后台编码、绘制标注、SHA、配额清理。队列/总内存压力进入backpressure跳过采样，降到最多4帧且有足够字节后自动恢复；区间由后台写入events.jsonl，缺帧使complete为false。单帧过大/容量/写入错误仍暂停并报告，不能阻塞或改变识别业务，也不能隐性丢帧称完整。
- core/diagnostic_storage.py负责diagnostics/session_*/的manifest/逐帧证据/索引，容量1GiB，只清已结束的已知旧会话，保护当前/未封口/未知目录；日常logs/runtime.jsonl只记关键状态/错误，10MiB×5轮转。两目录均gitignore。
- diagnostic_status含当前预算、待写字节与跳过数；独立Response状态diagnostic携带diagnostic_status，不回执或解锁检测；异步状态含单调时间戳，UI拒绝旧通知。暂停警告不随show_layout_status隐藏，后台实际状态不能与保存的开关混淆。
- 详见[诊断记录](docs/诊断记录.md)。关闭最多等待250ms，退出最多1秒；超时不宣称完整；暂未实现一键导出/自动回放；会话跨多局，清理按1GiB总量触发，只删已结束旧会话，不按局删除。
- 已移除同步图片保存模块及其构造参数、开关、白名单与旧YAML键；YoloInferencer只推理，Tracker只记牌。已有本地历史图片不自动删除。
- 动态/PT明确请求(H,W)=(480,768)；静态ONNX/TensorRT尊重导出尺寸，首帧直接拼图也不能污染后续输入。CPU优先ONNX，CUDA优先TensorRT FP16，再回退PyTorch；不自动因拼图改权重或重新导出形状。
- yolo_ms含本轮全部Ultralytics调用及内部前后处理/回退，GPU同步；主进程整轮另含截图/队列/轮询，子进程performance也不等于物理显示延迟。
- 权重仅在yolo/weights/<目录>/best.pt；训练仓库hey-three/yolo-for-han-card-tracker。一个8:5模型混合原图与两阶段图，按牌局先划分，未启动训练或承诺200ms。

## 9. 验证与提交边界

- 回归：python -X faulthandler -B -m unittest discover -s tests -v；doctest见排查文档。常规测试不加载真实模型，Qt offscreen；配置测试仅tests/.tmp，主动销毁窗口/DeferredDelete防跨测试遗留事件。
- 模型、真实截图、GPU/静态后端、打包与Android需分别验收；不要把单测、真实spawn测试或静态图调用说成完整实战。依赖锁定环境与既有本机验证环境不同。
- 个人config/config.yaml的本地修改、tmp参考图、tests/.tmp中间产物、games_images、.vscode不得混入提交；已存在权重不因文档工作改动。
- 当前任务已授权在V4.3.2移除旧截图链路、按确认本家出牌修复首扣漏K、加入压力自动恢复；推送该分支、合并main并本地同步。最终预算保留64MiB/8帧，磁盘1GiB，不能把曾讨论的1GiB写成RAM预算。
