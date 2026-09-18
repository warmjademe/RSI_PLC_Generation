# 官方 DeepSeek V4.1 Flash 八基线启动记录

2026-09-17 10:15（北京时间）已在 NAS 启动，启动核查时八个 worker 全部运行。
首次请求时间区间确认八路同时在途，全部 HTTP 200，返回模型均为 `deepseek-flash`。
实验继续运行；本记录不代表 800 个方法—任务组合已经完成。

- 运行目录：`/home/qyb/RESEARCH/RSI_PLC_Generation/baseline8_deepseek_v41_official_100_max10_20260917_v1`
- 模型地址：`https://api.deepseek.com/v1`；官方 2026-09-10 更新说明对应 V4.1 Flash。
- 数据：最长参考代码筛选出的同一 100 题；8 方法 × 100 题，共 800 项。
- 每题最多 10 次生成，64 次总模型调用，生成输出 8192；非思考、temperature=0。
- 不用本题反馈：Vanilla、FewShot、FinalCodeRAG、RawTrajectoryRAG。
- 使用本题反馈：Memento、EverMemOS、MemSkill、MSCE。
- 所有方法从空知识库开始，只积累本轮自身已完成任务的记录，不导入历史数据或 Qwen 的结果。
- 每 30 秒运行监测，保留持续低速、流停滞、未启动告警及保留预算的单路恢复。
- 监测 timer：`plc-baseline8-ds41official-100-max10-v1-watchdog.timer`。

验证通过：74 项离线回归检查、800 个空库边界、4 个真实编译/运行正反例，以及 1 个池外官方 API 探针。
补充回归初次运行中，一个旧测试 fixture 缺少当前已要求的 `source_split` 字段；补齐测试输入后全部通过，
初次失败日志保留。未为通过该测试修改实验方法或评分代码。

启动时已有 MSCE 首题成功，独立部分审计确认该题源码、运行计划、断言和 token 账本一致。
这仅是早期运行证据，不能据此比较成功率。

成本在远端 `cost_accounting/report.json` 和 `calls.json` 持续更新：每题成功前的生成次数、全部模型调用、
输入/输出 token、缓存组成、题后学习费用及未知用量分别保留。美元数值为峰谷费率区间估算，非实付账单。
池外探针单独记录于 `bootstrap/model_probe_costs.json`，不混入正式成本。

与 Qwen 相比，正式逻辑提示词、评分器、预算、任务顺序和反馈策略保持一致。
接口差异：官方无公开本地 tokenizer，采用有明确类型标签的 UTF-8 字节保守上界；不发送生成 seed 和本地 slot 参数。
保守检查可能更早减少学习上下文。既有未知评价提前结束策略保持，因此“最多十次”不表示所有失败任务都用了十次。
成功仅指给定 MatIEC 编译和有限运行测试通过；该任务池已暴露，不是独立未见留出集。

Qwen 活跃实验及旧科美接续队列未被修改；本轮结果独立保存。
协议锁 SHA-256：`9952572f59b1dab3eb3457b61625964f0f10c22e1e36bc2fdfb9f78eaf8e98cf`。
启动配置、验证回执和早期成本快照见 `evidence/`；它们不是持续同步的实时结果。

源代码：`../../experiments/baseline8_deepseek_official100/`。
