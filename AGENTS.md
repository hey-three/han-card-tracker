# AGENTS 指南 · Han记牌器

每次修改代码、结构或配置后都检查本文件和相关专题文档是否需要同步。默认简体中文，正确性优先，复用已验证上下文；优先API/CLI，避免重复工具调用。高成本浏览器自动化先评估；用户已明确授权的操作不反复确认。

## 1. 先读与文档职责

- [文档目录](docs/README.md)是入口，README中英文负责项目介绍与上手，不重复详细契约。
- [架构](docs/架构与数据流.md)、[记牌规则](docs/斗地主记牌设计说明.md)、[自动布局](docs/自动布局设计说明.md)、[拼图](docs/拼图自适应与检测方式.md)是当前实现说明。
- [设计决策](docs/设计决策.md)保留长期取舍，[排查与验证](docs/排查与验证.md)区分真实证据/未测项，[CHANGELOG](CHANGELOG.md)记版本变化；不通过层叠“以上废弃”维护当前规则。
- [配置参考](docs/配置参考.md)、[模型训练](docs/模型训练与拼图约定.md)、[维护与发布](docs/维护与发布.md)随对应变更同步。
- 仓库赞赏按钮由.github/FUNDING.yml的custom链接指向SUPPORT.md；桌面程序设置页也从images/QR.png展示微信/支付宝二维码。更新入口或图片时同步中英文README和维护文档，不自动改写收款二维码。

## 2. 编码要求

开发约束统一在本文件维护，删除的重复AI开发标准文档不再作为必读入口。正确性优先，兼顾运行效率、清晰分层和可维护性；复用既有机制，小规模重构应降低重复和复杂度，不引入无关依赖。每个def、class和__init__均有中文Google风格Docstring；参数/返回注解完整，异常触发条件写入Raises，可运行示例使用>>>。错误类型抛TypeError，非法值抛ValueError。优先X | None、list、dict及只读Sequence/Mapping，不滥用Any。优先清晰分层与有意义回归，不为低影响文档变更添加镜像实现的测试。

## 3. 范围与入口

当前仅维护Windows斗地主，不合并够级逻辑；设置页不提供游戏选择，推理链固定使用doudizhu；TRACKER_REGISTRY只启用doudizhu，新增YAML不等于新增游戏支持。Android和第三方APK分析是独立工程，不把第三方代码/权重/图片复制进来。

入口main.py建立QApplication、样式和CardUI，必须保留multiprocessing.freeze_support。主链：ui/main_window.py → inference_process.py → controller_process.py → game_controller.py → 布局/Tracker/YOLO/截图。设置由ui/settings_controller.py协调，协议在core/inference_protocol.py。

## 4. 进程与协议硬约束

- UI主进程、推理子进程独立；子进程不创建或操作Qt对象。Worker轮询20ms，UI仅在成功发送检测后置busy。
- Command(request_id,generation,name,payload)，None终止。reset/配置切换递增generation，旧结果丢弃。switch_layout必须携带完整标题与区域，不能只传名称。
- ready/fatal负责启动，ok/error回执指定请求，finished只负责检测/故障结束，不能解锁其他设置请求。结果计数、历史和performance入队前复制。
- Worker状态stopped→starting→ready，错误进入failed；恢复新建进程/队列、初始化牌局并保留用户暂停偏好。关闭回收轮询和队列，不等待向已死进程发送残留命令。
- 业务结果remain_cards、zone_cards、yolo_ms；可选screenshot_jpeg为实际YOLO输入，screenshot_regions使用实际输入的归一化坐标：普通区域键画原图框，packed:裁片序号:区域键只显示拼图名称；拆片重复标签允许，帧间清空，整图回退必须清last_input_plan，不能沿用旧拼图坐标。performance仅发布当前代次。diagnostic_status是独立的运行状态，不能混入牌局代次或检测busy回执。

## 5. 记牌契约

- WAIT_BEGIN要求底牌区连续两帧有任意牌，BOTTOM_START_FRAMES硬编码为2且独立于FRAME_LENGTH；不比较牌面或张数。第一帧不入手牌缓存，第二帧才进HAS_STARTED并作为手牌第1帧；非空合法手牌按设置FRAME_LENGTH连续一致后首扣，进STARTED_RECORD_CARD。空/未知/暂停中断底牌开局计数。无17/20硬门槛，无17→20补扣，空手牌不能通过。
- remain_cards只排除本家首次确认手牌及已确认的对手出牌。本家后续任何出牌只记历史，不补扣、不重扣、不反推初始手牌；用户已明确撤回累计出牌补扣。底牌不另扣。has_found_empty维护连续两手相同牌的空区过渡。
- frame_caches只保留最新阈值帧；阈值变小时下一帧即可确认，变大需等足够帧。
- 连续确认与已记录牌组比较使用Counter，比较牌点及张数而非排列；显示保持画面阅读顺序，先上行再下行、每行从左到右。自动布局as_boxes复用group_rows，不能再按类别名排序输出；手动布局按框左沿排序。空区过渡仍控制连续相同牌组的再次确认。
- 底牌缺失暂停并清未确认序列；已知为空连续max(3,FRAME_LENGTH)帧且持续RESET_TIME秒才内部结束。同帧模型复查只算一次。未知、截图失败、暂停/恢复中断累计。
- begin_waiting保留remain_cards/show_cards，新底牌连续两帧确认开局才刷新；手牌从第二帧累计。显式reset立即清空。不按某家零张、全图无牌、底牌牌面变化额外结束。
- 缺区域键是未知，有键空列表才是已知为空。未知清该区确认缓存、保留出牌与空帧历史；未观测区不能补成空区。

## 6. 布局、拼图与尺度

- 自动mode:auto仅需窗口标题和空layout，始终整图。按行组成完整牌组、合并相邻行后归属；短末行不能因初始评分被拆给本家，多重集排序防换行重复扣。回归fixture为tests/fixtures/right_wrapped_play.json。
- core/bottom_region.py独立判断底牌：检测框中心在横向15%～85%、纵向初搜0～25%内即为候选，不依赖手牌中心、宽高或牌面。高度提议=各候选下沿加半框高的最大值，封顶25%；连续两帧提议差≤屏高3%时取较大值锁定，本局不因少牌/空帧/未知而收缩。学习不增加推理或阻塞既有两帧业务门禁；同帧校准转跟踪只能推进一次。截图尺寸改变仅重学底牌框，长宽比>5%仍按原契约重学全局几何并保留牌账；新局/布局/显式重置也清学习。顶部计数栏误检与搜索范围外布局仍不保证。
- 自动几何initial是定位通知，Controller不得直接初始化扣牌。底牌是业务门禁，不要求三张，也不作必需几何锚点。中央翻牌不等于上方底牌到位。
- 自动长宽比变化超过5%只重学几何，保留本局历史；未知/歧义暂停，不保证中途恢复或所有皮肤兼容。
- 旧布局缺mode视manual。PACKED_INFERENCE/packed_inference是所有手动布局共用的布尔偏好，缺省true；自动不受影响，切手动原图/拼图不重扣牌。
- 手动拼图WAIT_BEGIN/HAS_STARTED只底牌+手牌；首扣后的下一帧底牌+三家出牌，不含手牌。开局无底牌、全空或未知也不跑整图校准；记牌中可同帧复查原图。
- 用户框就是源边界，向内取整，只用1e-7像素误差容差；不向四周或下沿扩框，不为框外牌补救。内部保护重叠也只在原框内。
- Tile.source_region区分人工边界与内部切口。内部残片须完整副本覆盖85%自身面积；人工边缘可接受但有完整副本时仍优先去重。按原位置/类别去重，不能按牌点去重。
- core/model_input.py在加载所选best.pt时读取同目录model_config.yaml，width/height为正的32倍数、resize:letterbox、rect:false；新增模型必须附带该文件，已有非法文件报错，仅缺文件旧模型兼容768×480画布。正式拼图使用所选模型的实际像素(W,H)，不是仅比例固定；config/packing_profile.py保留后备尺寸、阶段区域及INITIAL_REGION_SCALES。初值底牌1.652110、手牌0.703958、上下家约1、本家出牌0.836045；不在无底牌时试探放大。
- core/packing_scales.py复用当前检测框学习普通单牌高度，排除大小王长框。三帧最高/最低≤1.2确认，10%迟滞、权重0.25～8；测准底牌和参照后优先出牌、其次手牌，底牌目标多12%。未知/空区中断未确认测量。
- 布局、截图尺寸、模型、显式重置和检测方式切换隔离学习，二区→四区保留参照。规划缓存只存几何和画布，每帧重新识别；不旋转、不拉伸裁片。make_square_plan是历史/对照接口，正式用加权make_rect_plan。

## 7. 配置、UI与资源

- 仅主进程通过settings.save_*及config/store.py写盘。同目录唯一临时文件+os.replace；成功后更新内存，读取损坏必须报错，不能空配置覆盖。
- 后端成功回执后保存并刷新；后端失败保留旧组件，保存失败停止检测并提示重启。故障时允许修正持久化参数供新进程使用。
- 设置页所有布尔项使用滑动开关，持久化配置键保持不变；其他有多个取值的设置继续使用下拉框。预览偏好先保存/更新UI，再同步传图。点叉/Esc持久化关闭，退出主程序仅隐藏。实际预览、终端日志和完整诊断三项功能独立，完整诊断为推理运行证据的唯一图片保存入口；手动布局编辑参考图为独立本机缓存。
- 设置标签顺序为基本、高级、赞赏、关于；基本设置中的YOLO模型选择、设备、布局和显示耗时位于基本页。布局先选择“自动布局/手动框选”，下方缩进的无标题QGroupBox以系统窗口底色和中性边框区分二级配置：自动/手动分别列出各自已保存的命名布局。自动布局新建时在紧凑AutoLayoutDialog选择完整窗口标题并命名，支持编辑/删除，始终整图；手动仍可选拼图/原图。用户点模式立即提交对应模式上次使用项，配置刷新只同步显示不发请求；没有布局明确提示新建，不提交占位项。last_layouts按auto/manual记录上次成功使用名，与current_layout同次原子保存；后台失败恢复实际模式，删除清理失效历史，旧配置回退同模式当前项/首项。高级设置中“输出调试日志”位于“实时显示YOLO输入”之后；数值下拉控件在设置行右侧对齐，诊断磁盘上限输入框保持紧凑宽度。赞赏页只显示images/QR.png，响应页面尺寸等比例缩放并保留二维码可扫描性。
- 自动和手动布局下拉项右侧依次为细线编辑/垃圾桶图标，悬停提示“编辑/删除”，支持F2/Delete；视图showEvent在Qt弹出容器安装过滤器后重新安装自己的视口事件过滤器，优先接管图标按下/松开，避免QComboBox提前消费松开事件；普通条目仍由Qt选择。图标松开后收起列表，再由子QTimer运行操作，不误切换布局。移除手动刷新按钮；新建与按名称编辑分开，保存后自动刷新，用户再次选择同一项也会重交完整配置。
- 编辑器参考图由utils/layout_editor/reference_store.py保存在CONFIG_PATH同目录layout_previews/，布局名哈希PNG内嵌名称/窗口身份，每个名称仅保留最新一张，原子替换并Git忽略，不进入诊断配额或推理链。重开直接加载原图并叠加YAML已保存的五区；旧布局无缓存需补截一次。同窗重截立即更新原布局缓存，新建/改目标/另存名称在保存成功后建立关联；缓存失败保留草稿并提示。编辑器不按图像尺寸resize或居中，画布直接按当前视口缩放，用户仍可手动改窗口大小。编辑器绑定明确的布局名称和截图目标；同一窗口重截图保留并等比映射未保存框选，换窗口必须重截图且清旧框。同名编辑直接覆盖，另存为已存在名称需确认；保存失败保留草稿，取消保护未保存修改。五区完整后启用保存，保存时直接校验五区齐全、坐标范围和截图目标；已移除重复的“预览校验”按钮及独立预览窗口，不运行YOLO预验证。右下坐标允许到图片width/height，极小拖动不覆盖旧框。
- 编辑布局先保存内容，后端确认切换再保存current_layout。删除当前布局先确认替代布局；失败不删除原记录。
- 模型/设备/布局切换重建相应组件并重置牌局。控制设置有白名单，子进程只改内存，不写YAML。
- BASE_DIR是可写运行目录，RESOURCE_DIR负责冻结资源/_internal解析；首次冻结运行复制配置到可写目录。
- UI增量更新计数/历史；出牌标签保留布局归属仅切可见性。depleted/count动态属性更新需unpolish/polish。
- panel_arrangement/PANEL_ARRANGEMENT为纯UI偏好，缺省vertical，可选horizontal；基本设置“界面排列”切换上下/左右长条，原子保存后只重排同一对QFrame，不重启推理或清牌局。两区同底色，无外框、无圆角、无区域标题；PlayedCardsPanel的arrangement动态属性控制单边分隔线，上下用横线、左右用竖线，隐藏出牌区时同时隐藏线；出牌行简称上/本/下并靠左，完整历史可悬停查看。上下默认宽495逻辑像素（原550缩10%），左右默认900且两区始终等宽（相同最小宽和1:1伸缩）；左右模式缩减内边距/行距并将最小高度下限降到80，内容最小高度仍优先，实际最小尺寸受字体/DPI影响；显式切换时收拢；首显允许收拢，并在标题栏子QTimer回调中待原生布局生效后补算一次（_initial_size_pending先清再resize），不保留创建期间的临时高度；后续重显及置顶仍保留用户尺寸。布局二级配置用palette(window)/palette(mid)，不再蓝色底纹。
- 标题栏四个操作统一使用ui/icons.py按主题绘制的细线图标；ui/layout_status.py仅压缩展示文案到最多六字，不改变业务状态。ui/title_bar.py在耗时后显示更小字号的短状态，悬停显示完整说明；正常布局提示不占底行且只刷新标题栏；完整诊断backpressure暂缓仅在标题栏显示“诊断暂缓”，悬停完整原因，不受show_layout_status影响，恢复后回显布局提示；故障、诊断暂停和日志错误也只在顶部显示，优先级为识别故障/错误→持久诊断告警→诊断暂缓→普通布局；悬停详情包含并存告警。完全不创建QStatusBar，并拦截StatusTip事件防止Qt隐式创建；顶部“重启”仅failed时显示，状态变化不计算窗口尺寸。空间不足时标题文字省略，不挤占原生窗口按钮。
- 主窗口设置、暂停/继续、重置、赞赏图标与运行标题显示在Windows原生标题栏同一行左侧；Qt 6.9+扩展客户区中，CardUI自身及central_widget都关闭WA_ContentsMarginsRespectsSafeArea，避免主窗口把整行再次下推。右侧按钮避让读取DWM物理宽度并按devicePixelRatioF转换；子QTimer在显示/缩放后刷新，不轮询。首次可见在延迟回调中收拢一次；后续重显只按需增高，保留用户尺寸。已撤回无效的底层QWindow标志切换。ui/title_bar.py的clip_native_title_overlay只裁剪本窗口的_q_titlebar子窗口，让Qt标题叠加层仅显示右侧系统按钮，隐藏左侧默认图标/重复文字；显示、缩放和切换屏幕后重新设置，依赖Qt 6.10/6.11的Windows标题层实现。窗口名称固定Han记牌器，运行状态/耗时只交由MainTitleBar显示，保留系统最小化/最大化/关闭提示。NoTitleBarBackgroundHint固定为False，让右侧Qt标题叠加层先绘制不透明主题底色后再画悬停高亮，避免深色半透明白色悬停合成白块；左侧默认内容仍由原有裁剪隐藏，不切换其他底层QWindow标志。右侧系统窗口控制按钮及窗口缩放仍由Windows处理。赞赏爱心仅为静态细线图标，点击复用同一设置窗口并直接选中既有tab4，后台识别继续；已经打开设置时只跳页。赞赏页签流光/波动仍在讨论，未实现。关于页介绍使用两字首行缩进，提供GitHub项目主页使用说明入口及mailto:jiangfan0519@qq.com联系链接，支持选择复制；更新项目地址/联系方式时同步使用指南。
- 窗口置顶仅标志变化时设置并保留几何，不反复adjustSize；普通窗口重显后先QWindow.resize恢复Qt逻辑尺寸，再setPosition恢复位置，避免150%缩放下边框换算漂移1像素；最大化/最小化仍用序列化状态。设置框打开时后台检测继续，关闭不发送touch_time、不重启已运行定时器；保留手动暂停、busy及后端故障保护。
- 截图下沉/恢复统一Qt逻辑坐标，防止重入，失败也恢复。延迟定时器随窗口销毁。show_layout_status仅隐藏正常提示，故障和恢复按钮始终可见。

## 8. 图像、计时与模型

- 原图完整性：regions=None时必须把完整截图交给模型，仅letterbox等比补边，不按模型比例裁切。ScreenCapture在per-monitor线程DPI上下文内优先PrintWindow完整渲染；失败/黑输出才桌面GetDC(0)+BitBlt回退（需完整可见无遮挡），最小化返回未知而非小位图，结束恢复原DPI。预览按contentsRect完整等比显示，标题标实际原图/拼图、像素及桌面回退；切换确认或当前无帧清除旧图，旧代次不得清新图。截图与预览应以受控遮挡/四角测试和用户实际窗口验证，不冒充所有游戏渲染或完整实战验收。

- full_diagnostics/FULL_DIAGNOSTICS缺省false，经设置回执/原子保存启用。run_controller_loop注入DiagnosticRecorder，GameController单独使用默认不落诊断文件；进程退出finally有界收尾。
- core/diagnostics.py只在完整诊断开启时建立一个后台线程；主识别线程先预留预算、复制RGB图和CPU结果/状态再提交。每次模型调用输入必须在调用前冻结，不能把复用画布、GPU张量或Qt对象入队；异常及同帧整图回退分别留证。
- 帧数上限8（含正在写的一帧），待保存RGB+JSON预算64MiB，单帧JSON256KiB；后台编码、绘制标注、SHA、配额清理。队列/总内存压力进入backpressure跳过采样，降到最多4帧且有足够字节后自动恢复；区间由后台写入events.jsonl，缺帧使complete为false。单帧过大/容量/写入错误仍暂停并报告，不能阻塞或改变识别业务，也不能隐性丢帧称完整。
- core/diagnostic_storage.py负责diagnostics/session_*/的manifest/逐帧证据/索引，按waiting/game_XXXX分局；diagnostic_disk_gib/DIAGNOSTIC_DISK_GIB为持久化GiB总额度，缺省1.0、0.1–1024且最多两位小数。只清已封口旧会话及当前会话已封口旧局，保护当前局/未封口/未知目录；日常logs/runtime.jsonl只记关键状态/错误，10MiB×5轮转。两目录均gitignore。
- diagnostic_status含当前预算、待写字节与跳过数；独立Response状态diagnostic携带diagnostic_status，不回执或解锁检测；异步状态含单调时间戳，UI拒绝旧通知。暂停警告不随show_layout_status隐藏，后台实际状态不能与保存的开关混淆。
- 详见[诊断记录](docs/诊断记录.md)。关闭最多等待250ms，退出最多1秒；超时不宣称完整；暂未实现一键导出/自动回放；会话跨多局但每局独立子目录，冻结round_id随帧保存；开局边界最多128条元数据按帧序由写线程处理，即使跳过全部图帧也建局标记。GameController._sync_diagnostic_round只观察业务状态转换，不改Tracker规则。清理按所设总额度触发，记录retention_cleanup和pruned_rounds，清理后complete为false。额度改变只在后台下次检查执行；已暂停需关闭重开。
- 已移除同步图片保存模块及其构造参数、开关、白名单与旧YAML键；YoloInferencer只推理，Tracker只记牌。已有本地历史图片不自动删除。
- 有模型旁置配置时，原图/拼图/同帧复查与静态导出统一使用配置(H,W)，预测rect=False；首帧前只初始化后端核对实际绑定和步长，不额外跑整图。CPU优先ONNX、CUDA优先TensorRT FP16，再回退同尺寸PT。core/model_export_cache.py在原PT同目录生成同名.onnx/.engine及独立.meta.json，按PT内容SHA、尺寸、静态batch=1、精度、环境与设备校验；不信任旧同名文件，目录内短期工作区导出并验证后原子替换，原PT不改。根model_cache已按用户要求删除，不搬迁、不再创建；目录不可写回退同尺寸PT。配置/哈希/导出仅在模型加载时处理，不入逐帧路径。缺旁置文件旧模型才保留原动态请求/旧静态兼容行为。
- yolo_ms含本轮全部Ultralytics调用及内部前后处理/回退，GPU同步；主进程整轮另含截图/队列/轮询，子进程performance也不等于物理显示延迟。
- ONNX加载前比较Python包装/原生库主次修订版本，混装则明确回退PT；不只相信安装记录。requirements锁定ORT1.26.0、onnx1.21.0和onnxslim0.1.94。导出显式simplify=False，应用再单独用已安装的onnxslim简化ONNX、保留元数据并校验；避免Ultralytics按CUDA是否可用自动叠装onnxruntime-gpu。缺导出依赖不自动安装，回退PT。导出校验格式version=3。
- 权重仅在yolo/weights/<目录>/best.pt，新模型同目录必须提供model_config.yaml（模板yolo/model_config.example.yaml）；model描述不决定n/s结构，实际网络来自PT。训练仓库hey-three/yolo-for-han-card-tracker。一个模型混合原图与两阶段图，尺寸以真实训练批次为准（960×608为30:19，不是严格8:5），按牌局先划分，未启动训练或承诺200ms。

## 9. 验证与提交边界

- V4.3.3发布纳入此前本地预览、设置继续识别、检测去重及模型回退改动；用户已明确要求新建V4.3.3并合并main，本地同步。预览标签统一上/下/本/底/手，默认495逻辑像素。自动底牌框必须原样显示bottom_region实际判断边界，空区也显示；只有尚未定位手牌保留search:示意，不得再用宽泛底牌示意替代真实范围。拼图按实际Plan标裁片名称。
- core/detection_filter.py在原图坐标恢复后、区域解析前统一过滤同位置候选：IoU≥0.65通常取最高置信度，同分保留先出现者；J与大小王同位置或符合竖排内嵌字母几何时优先大小王，不额外增加大小王置信度门槛。不同位置相同点数保留。拼图异类完整候选保留到统一过滤器，不再仅因类别冲突回退；内部残片仍优先完整副本，不能拿残片高分替代完整牌。原始调用证据保留；final_boxes为过滤后结果，visual_filter记录duplicate_detections_resolved、策略与移除项，visual_filter_ms包含于布局阶段。不钳制剩余计数，不恢复本家出牌补扣。

- 回归：python -X faulthandler -B -m unittest discover -s tests -v；doctest见排查文档。常规测试不加载真实模型，Qt offscreen；配置测试仅tests/.tmp，主动销毁窗口/DeferredDelete防跨测试遗留事件。原生窗口、截图和DPI测试另以windows平台执行。
- 模型、真实截图、GPU/静态后端、打包与Android需分别验收；不要把单测、真实spawn测试或静态图调用说成完整实战。依赖锁定环境与既有本机验证环境不同。
- 通常不提交个人配置与权重替换。本次用户明确授权：以E盘最新版本整理、完整回归后上传V4.3.3并合并main，config/config.yaml与yolo/weights的模型/配置/目录调整也一并上传，取代此前仅本地及排除项约定。
- 诊断、日志、tmp参考图、tests/.tmp、games_images、布局参考图、本地编辑器及导出工作区/机器校验缓存仍不提交，也不因清理文档删除用户现有材料。二维码使用原images/QR.png，README中英文直接展示并保留SUPPORT.md入口。
- 保留诊断按局目录、可配置磁盘额度及8帧/64MiB内存与压力自动恢复。本家出牌补扣仍已撤回，首扣缺张原因保持待查。
- 模型导出在所选PT同目录，加载提示独立于busy与请求回执。InferenceWorker.is_loading_model/model_loading_changed只服务提示，启动及所有模型/设备请求完成前保持；普通布局提示开关不隐藏加载，故障优先。
