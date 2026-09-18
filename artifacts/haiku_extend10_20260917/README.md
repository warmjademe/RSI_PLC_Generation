# Haiku 既有历史续跑到最多 10 次

NAS 实验目录：
`/home/qyb/RESEARCH/RSI_PLC_Generation/baseline8_haiku45_100_extend10_20260917_v1`

原始、保持不变的 4 次实验：
`/home/qyb/RESEARCH/RSI_PLC_Generation/baseline8_haiku45_100_max4_20260917_v1`

- 同一 100 题、八个方法、Haiku 模型标识与 TeamRouter 原生接口。
- 92 项原有成功结果保留；708 项未成功结果接续原交互，累计最多 10 个候选，成功立即停止。
- 原有调用、失败、未知结果、工具记录和 token 全部保留；不是额外再给 10 次。
- Vanilla、FewShot、FinalCodeRAG、RawTrajectoryRAG 不接收当前题错误反馈；其余四种方法沿用确认失败后的修复。
- 验证不确定时保留 unknown，允许新候选；不把 unknown 当作确认的程序错误，也不用于负例学习。
- 每题沿用其最初的前序任务知识快照。新提炼知识保存到 `extension_learning`，不回灌已经执行过前四次的其他题。
- 因此这是追溯性 4→10 续跑，不是从第一题起连续执行的在线 10 次实验。尝试上限可以对齐，但知识演化路径和 unknown 后继续的策略仍须在比较中披露。
- 历史学习调用不退款；新增学习也计费。`cost_accounting/report.json` 分列 `original_run`、`continuation_only` 和总账。未知实际 usage 与保守预算扣费分开。
- 重建验证对全部 708 项逐一检查原有候选、模型调用、工具调用、输入/输出 token 和未知费用计数，未新增模型/工具调用。
- 八条独立 systemd 队列，单条串行；监测 timer 每 30 秒检查、保留账本恢复、异常 Bark 通知。没有单题 30 分钟强制截止。

代码：`experiments/baseline8_haiku100/extend.py`。
回归：`experiments/baseline8_haiku100/extension_tests.py`。
真实账本重建验证：本目录 `preflight.py`。
NAS 证据：`extension_protocol.json`、`extension_preflight.json`、`extension_full_audit.json`、`launch.json`。

成功仍指同一 ST 代码通过绑定的 MatIEC 编译和给定有限运行断言，不代表台达厂商编译、真实硬件或形式化验证。
