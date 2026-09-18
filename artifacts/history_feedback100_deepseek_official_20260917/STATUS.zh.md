# 官方 DeepSeek V4.1 Flash 启动核查

2026-09-17 09:28 已按用户确认启动，官方 API 模型名为 `deepseek-flash`。
运行目录：华硕 `/home/qyb/RESEARCH/RSI_PLC_Generation/history_feedback100_deepseek_official_twolevel_20260917_v1`。

Full、NoAssets、NoFeedback、Neither 四组均已实际请求官方接口并正常收到返回。
同一冻结 100 题，每题 10 候选、64 次总调用；非思考模式，temperature=0.7；四组独立空历史库。
需求和完整测试用例、原编译器、100ms 扫描测试和独立复判保持原样，无新增形式化验证。

18 项此前完成的离线测试通过。启动时源码/数据冻结审计、运维审计和首批 13 次真实调用的请求与规范化映射审计通过。
这 13 次均通过输出格式解析，模型返回身份为 `deepseek-flash`；账本实际输入 107,388、输出 27,330，合计 134,718 token。
这是首批核查快照，不是最终消耗或最终成功率。09:28:53 的实时状态显示 NoFeedback 首题已通过独立复判，其他组继续运行。

主程序、监测和成本统计三个服务均 active。Qwen 与 Haiku 继续运行；科美旧 DeepSeek 任务、监测和专用隧道保持 inactive。
旧结果、失败和成本保留，新轮不复用旧答案或历史库。已发送 Bark 启动通知，整个实验尚未完成。

此前 `MODEL_CHOICE_PENDING.json` 是未启动时的历史快照，已被 `model_authorization.json`、`launch_receipt.json`、`launch_verification.json` 覆盖。
