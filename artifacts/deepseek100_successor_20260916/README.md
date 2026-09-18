# DeepSeek Flash 接续实验配置记录

2026-09-16 22:23（北京时间）完成核验。科美已切换至本地
`deepseek-v4-flash-0731-ud-q8-k-xl`，不是调用官方云 API。
五个权重分片重新通过 SHA-256 校验；两路并发、每路 131072 token
上下文、关闭思考模式，使用已固定的 llama.cpp build-v2。

NAS 接续运行目录：
`/home/qyb/RESEARCH/RSI_PLC_Generation/baseline8_deepseekflash_100_max10_20260916_v1`。
连接地址为 NAS `http://127.0.0.1:18186/v1`，通过持久 SSH 隧道连接
科美的本地鉴权接口。私钥和 API 凭据位于仓库之外，未写入实验数据。

当前 Qwen 目录：
`/home/qyb/RESEARCH/RSI_PLC_Generation/baseline8_qwen38q4_100_max10_20260916_v1`。
最后核验时为 198/800 个方法任务结果，八路仍在运行；其冻结协议哈希保持
`e43ffb72cdb6a65b402935f3e53ee34c67279ae0d11dd51bb17f2a018c2d5b81`。

接续定时器 `plc-baseline8-deepseek100-max10-20260916-v1-queue.timer`
已启用，每 60 秒检查一次。只有 Qwen 的完整 800 项结果通过审计、状态完成、
工作进程退出且没有 STOP/PAUSE 时，才启动 DeepSeek。当前状态为等待，
DeepSeek 正式测试调用数为 0。启动标记用于阻止重复调度；异常会保留现场并通过 Bark 告警。

实验保持相同的 100 题、题序、反馈权限、评测器和预算；每题最多 10 次生成，
每次输出上限 8192 token，八个方法从各自空知识库开始。
Vanilla、FewShot、FinalCodeRAG、RawTrajectoryRAG 不使用本题错误反馈；
Memento、EverMemOS、MemSkill、MSCE 可以使用。成功生成前的尝试次数、
生成/检索/学习调用和 token 成本继续记录，未知用量与实际用量分开。

验证证据：52 项回归测试通过；800 个首次请求与 Qwen 的空知识库请求逐一一致；
数据集、公开需求和评测器逐字节一致；四个真实编译/运行正反例符合预期；
两个真实流式请求在不同模型槽位同时执行；原生 tokenizer 与响应 input token 一致。
详见 `evidence/bootstrap/armed_verification.json` 和 `evidence/readiness.json`。
新协议哈希为 `cc9606f5559117e74f293612503d94154ab2a18dfa183cc5e5de900a0f3201aa`。

部署过程保留了两类检查记录：旧运行时缺少 `--slot-save-path`，导致客户端
不能清理空闲槽位；已配置该参数并实测两槽位清理成功。一次合成模型响应漏写
`END_FUNCTION_BLOCK`；原始响应和费用保留在 NAS `bootstrap/synthetic_missing_end/`。
随后仅在合成基础设施探针中明确要求该结束词，检查通过；正式任务提示词和评分
没有改变。合成探针共发生 4 次模型调用，费用独立记录在
`evidence/bootstrap/model_probe_costs.json`，不混入正式实验。

成功口径仍是 MatIEC 编译和给定有限运行断言通过；已有的提前 unknown 判定规则
保持不变。模型、量化、tokenizer、硬件和运行时不同，结果不能解释为仅由模型架构
造成的差异。科美实际同时推理两路，八个 baseline 通过可观测的 FIFO 队列共享服务。

调度前暂停：在新运行目录创建 `queue/PAUSE`。调度后停止：创建 `STOP`。
任务不会因正常排队或累计达到 30 分钟而被单独终止；既有无输出、持续低速和
单请求超时监测保持启用。
