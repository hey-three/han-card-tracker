# 贡献指南

感谢帮助改进Han记牌器。当前维护Windows斗地主，不把新增YAML或抽象Tracker接口当作其他游戏已支持。

## 报告问题

使用[Issue模板](https://github.com/hey-three/han-card-tracker/issues/new/choose)，说明源码提交/发布包、模型和后端、窗口及布局、手动原图或拼图、期望与实际及最短复现步骤。参考[排查指南](docs/排查与验证.md)提供当前能保存的原图/最终标注；逐帧诊断包还未实现，不要求提供不存在的产物。

## 提交变更

1. 阅读[AGENTS](AGENTS.md)及AI开发标准。函数、类和构造器需中文Google风格Docstring及完整类型注解。
2. 从当前main建立工作分支，聚焦一项问题；不要提交个人config/config.yaml、tmp/、tests/.tmp/、games_images/或本地编辑器文件。
3. 业务规则、视觉归属与推理后端保持分层；子进程不操作Qt，设置只有主进程原子保存。
4. 对逻辑变化添加有意义回归；纯文档变化检查本地链接、示例路径和前后一致性，不新增镜像实现的测试。
5. 更新相关专题文档、README摘要、AGENTS和CHANGELOG，说明实际验证与未测范围。

```powershell
python -X faulthandler -B -m unittest discover -s tests -v
python -B -m doctest core/frame_packing.py core/square_packing.py core/auto_layout.py
```

真实模型、GPU、截图链路及打包需单独验证；不得把单元测试、静态图或构建成功描述成完整实战验收。不要仅凭微基准承诺200ms或某模型最准。

## 模型与数据

训练开发在[yolo-for-han-card-tracker](https://github.com/hey-three/yolo-for-han-card-tracker)。明确类别映射、原图/拼图尺寸和按牌局拆分数据，避免同帧跨训练测试集合。保留原有许可证与来源信息。
