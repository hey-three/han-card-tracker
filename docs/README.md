# 项目文档目录

当前运行行为以源码与以下专题文档为准。历史讨论中已取消的方案不再混在使用说明里；值得保留的原因放在设计决策，实测放在验证记录，版本变化放在CHANGELOG。

| 你想了解什么 | 文档 | 对应代码 |
|---|---|---|
| 安装、启动、选择窗口、常见操作 | [使用指南](使用指南.md) | main.py、ui/ |
| 可调参数与配置保存 | [配置参考](配置参考.md) | config/settings.py、config/store.py |
| 一帧如何从截图走到计数 | [架构与数据流](架构与数据流.md) | core/game_controller.py、推理进程协议 |
| 开局、扣牌、结束与保留历史 | [斗地主记牌设计](斗地主记牌设计说明.md) | core/games/doudizhu.py、base_tracker.py |
| 自动判断牌组属于哪家 | [自动布局](自动布局设计说明.md) | core/auto_layout.py |
| 两阶段拼图、严格选区、在线缩放 | [拼图与自适应](拼图自适应与检测方式.md) | frame_packing.py、square_packing.py、packing_scales.py |
| 一个模型兼容原图与拼图 | [训练与拼图](模型训练与拼图约定.md) | config/packing_profile.py、训练仓库 |
| 首扣缺张证据与待查项、缺帧恢复与清理 | [V4.3.2故障回归](V4.3.2问题回归与诊断机制.md) | 真实日志脱敏fixture、Tracker/诊断回归 |
| 日常日志、完整逐帧证据、容量和后台保存 | [诊断记录](诊断记录.md) | diagnostics.py、diagnostic_storage.py、diagnostic_state.py |
| 出错时已有材料、测试证据与未测项 | [排查与验证](排查与验证.md) | diagnostics.py、tests/ |
| 为什么采用这些规则、哪些路线已放弃 | [设计决策](设计决策.md) | 核心契约摘要 |
| V4.3.3的方案与实施范围 | [V4.3.3实施记录](V4.3.3实施记录.md) | 自动底牌区域与本地功能汇总 |
| 维护文档、仓库简介、Topics与发布 | [维护与发布](维护与发布.md) | 文档与公开元信息 |

[更新记录](../CHANGELOG.md) · [贡献指南](../CONTRIBUTING.md) · [开发约束](../AGENTS.md) · [English introduction](../README.en.md)

## 维护原则

一条规则只保留一个主要解释位置。改变功能时同步专题文档、README摘要、AGENTS及相关测试，不通过反复添加“以上作废、以下为准”维持一致性。未实现的功能必须明确标注；测试通过不等于实战或安装验收。
