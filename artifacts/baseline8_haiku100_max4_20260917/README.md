# Haiku 八基线启动记录

2026-09-17 13:27（北京时间）启动核查通过，八路均已收到真实模型响应并确认同时在途；实验继续运行。

- 执行主机：NAS。
- 模型：`claude-haiku-4-5-20251001`，沿用 TeamRouter `https://api.teamorouter.cn/v1/messages`。
- 身份范围：中转接口报告的 Haiku 4.5，不是独立权重验证，也不标作 Anthropic 官方直连。
- 运行目录：`/home/qyb/RESEARCH/RSI_PLC_Generation/baseline8_haiku45_100_max4_20260917_v1`。
- 同一批 100 题 × 8 baseline，共 800 项；每题最多 4 次生成（首次加最多 3 次后续尝试）。
- 不用本题反馈：Vanilla、FewShot、FinalCodeRAG、RawTrajectoryRAG；另外四个方法允许。
- 独立空知识库，只学习自身本轮已结束任务，不导入 Qwen、DeepSeek 或历史训练记录。
- 每次生成输出最多 8192 token，temperature=0，未启用思考；总调用、学习预算沿用既定方案。
- 监测 timer：`plc-baseline8-haiku45-100-max4-v1-watchdog.timer`，每 30 秒检查，保留异常告警和有界单路恢复。
- 成本：远端 `cost_accounting/report.json`、`calls.json`，记录生成次数、实际 token、缓存组成、题后学习和未知用量；没有中转费率证据时不估算美元。

通过 50 项回归检查、800 个空库边界、4 个真实编译/运行正反例，以及两个池外模型探针。
回归包含全部八种反馈权限、第四次成功正常验收、第四次中断恢复后不发第五次，以及缓存用量不重复累加。
首批部分审计通过（2 项已结束、均有完整 token），不能据此评价最终成功率。

原生 SSE 保存于 `response.sse`；实际请求在 `anthropic_request_body.json`，逻辑审计投影在 `request_body.json`。
为兼容既有中转通道，同时在 user 中发送原角色指令；不改写模型代码，审计验证实际请求与逻辑输入绑定。
Qwen 与 DeepSeek 仍为最多 10 次，本轮为 4 次，不能称为同预算模型比较。
成功仍仅指 MatIEC 编译及给定运行断言通过；既有未知结果提前结束规则保留。

协议锁：`80b6c546c419e16ea792130745187a196568bd8db24bd81dd74cfeaa66f69490`。
配置与启动回执见 `evidence/`，代码见 `../../experiments/baseline8_haiku100/`。
