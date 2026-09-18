# Baseline 1: Memory-to-Skills Continual Evolution for PLC

这个目录实现论文 *Recursive Self-Improvement from Memory to Skills* 的第一条基线，面向 DeepSeek 官方 API 的正式版 `deepseek-v4-flash` 和外部记忆，不修改、微调或重新训练大模型权重。正式配置只接受 `https://api.deepseek.com`，不使用第三方免费路由或备用模型。

正式训练 `msce-common-trace-four-shard-formal-v1` 已完成、通过审计并冻结；紧凑结果和哈希证据见
[`results/msce-common-trace-four-shard-formal-v1/README.md`](results/msce-common-trace-four-shard-formal-v1/README.md)。
该结果只证明 1000 条训练轨迹已形成冻结外部记忆，不包含密封 100 题的最终效果。

## 一句话理解

把每次“读需求 → 生成 ST → 让 Oracle 判卷 → 根据错误修复”的过程保存成轨迹；相似轨迹积累到两条以上后，归纳成带触发条件、步骤、检查方法、边界和证据的策略；策略在后续任务中确实提高成功率后，再结晶成可检索技能。测试前冻结记忆和技能，避免把测试答案学回训练库。

## 与论文的对应关系

- L1 轨迹：`state, action, observation, reflection, V`，保存在 SQLite 的 `trace_steps`。
- L2 策略：`trigger, procedure, verification, boundary, evidence`，保存在 `policies`。
- L3 环境认知：跨策略归纳的 ISPSoft、COMMGR、DVP-ES3 和 IEC-ST 规律，保存在 `cognition`。
- 技能：只有观察到正收益且证据稳定的 L2 策略才进入 `skills`。
- 回填：终点值等于任务奖励，前序步骤按论文的折扣递推回填，`gamma=0.9`。
- 可靠度：采用 `(pass+1)/(trials+2)`；试用后达到 0.6 才激活，低于 0.2 归档。

## 实验口径

“训练”指训练外部记忆与技能库，不是微调 DeepSeek 参数。早期独立 runner 的1000个训练任务允许最多2轮可见反馈，旧的测试入口上限为10轮；这两个入口只用于开发期兼容性检查。论文正式四轨评测固定为每题最多3个候选，与 Harness V5 最终评测使用同一个成熟生成—判卷链和同一个 Windows Worker。结果分别记录候选数和反馈轮数，避免两种口径混淆。

DeepSeek `deepseek-v4-flash` 固定使用 `thinking.type=disabled`。原因是该接口把思考和最终答案共同计入输出上限；PLC 长任务在默认思考模式下可能在输出 ST 前耗尽 token。这里关闭的是模型内部长思考，不是 MSCE 的外部“生成 → Oracle → 修复 → 轨迹反思”过程。

每个候选依次经过：

1. 本地响应与接口检查；
2. Huashuo 指定 Windows Worker 的 ISPSoft 3.24 编译；
3. COMMGR 2.11 连接 DVP-ES3；
4. 可见测试向量；
5. 候选通过可见测试后，再运行密封测试。密封失败只返回“未通过”，不把隐藏向量泄露给模型。

`pass`、`fail`、`inconclusive` 三类严格分开；基础设施异常不会记成模型错误，也不会进入策略收益估计。

## 数据与输出

- 训练集：`data/train/tasks`，1000 个完整任务包。
- 测试输入：`data/test_public/tasks`，100 个公开需求包。
- 密封 Oracle：`data/test_sealed/tasks`，模型永远看不到。
- 运行数据库：`runtime/<run-id>/memory.sqlite3`。
- 每次原始请求、模型响应、候选 ST、Oracle 原始证据：`runtime/<run-id>/artifacts/`。
- 汇总报告：`runtime/<run-id>/reports/summary.json` 和 `summary.md`。
- 实际文件按 split 命名，例如 `train_summary.json`、`test_summary.json` 和对应的任务级 JSONL。

统一轨迹模式使用 `CommonTrajectoryReader` 校验
`history_plc_feedback_paths/collections/<collection-id>` 中的清单、哈希、模型身份、
任务快照和 Harness 工件。它把候选哈希、动作、Oracle 观察、反馈类别和回填值导入
L1，但不把完整 PLC 源代码复制进 SQLite。正式 1000 题分布在四个连续 collection，
因此通过 `freezes/<freeze-id>/corpus_freeze_manifest.json` 绑定分片顺序、父检查点、
1000 个唯一任务、完整工件审计和逐文件哈希。只有该冻结语料能生成正式 baseline
记忆；`--limit` 只用于独立 run ID 的导入预检。人工复核准入和原自动资格化证据均
保留在任务结果的 provenance 中。

正式四轨生成不再使用早期的ST专用提示/Oracle支线。`mature_adapter.py` 把冻结后的MSCE
策略、技能、相似轨迹教训和高层经验转换为不含任务号、候选哈希、完整PLC代码或隐藏向量
的外部记忆块，再注入只读 `our_method_Deploy` 成熟 Harness。PLC型号、ST/梯形图响应契约、
生成—编译—形式化验证—OpenPLC—ISPSoft/COMMGR反馈链均由成熟 Harness 保持不变；适配层
只增加有哈希和固定字符预算的记忆上下文，并拒绝任何非 DeepSeek Flash 的实际模型解析。

`mature_final.py` 是正式的密封100题入口。它先验证baseline的1,000题记忆已经冻结且从未读取
测试集，再要求V5 Harness已冻结并完成自己的基线—冠军密封评测；只有两道门都通过后，才
复用V5已经承诺的同一组100题快照。检索层只读取公开的需求、接口和metadata，隐藏性质和
测试向量仍只由成熟Harness中的Oracle读取。每题使用单机、最多3个候选；RDP/API临时错误
单独封存并排除出模型得分，必须重新证明最终端点后才能续跑。任务清单、外部记忆、官方
DeepSeek模型、token、反馈轮数、基础设施重试和所有Harness工件均有哈希证据。

V5内部的冻结基线—冻结冠军比较采用预注册的逐题交替顺序，是主要算法效果比较。MSCE
baseline为了避免影响V5选择，只能在V5评测结束后运行，所以它与V5两臂的同题胜负只报告
描述性差异，不把后执行造成的时间/环境差异解释为因果改进。

论文中的策略增益把“诱导该 L2 策略的证据 episode”计入 with-policy evidence。
本实现将这种 evidence association 与后续真实 invocation 分表保存，再合并计算启发式
gain，避免有正证据的策略因为尚未在后续任务调用而永远无法进入技能候选。

PLC经验不能只按自然语言相似度混在一起。导入公共轨迹时，L1 episode、L2策略签名和
检索文本都显式加入 `target=DVP48ES300R/AS228T-A` 与 `language=st/ld`；正式测试也用当前
任务的实际四轨分配构造同样作用域。因此，梯形图响应格式经验不会因为需求语义相似而自动
注入ST任务，AS与DVP的厂商差异也不会被无条件合并。跨轨共性仍可由带边界的高层认知表示，
但正式检索只有在该认知的证据策略中存在当前轨道证据时才允许注入。检索顺序固定为
“先硬匹配PLC型号和输出语言，再在同轨内计算语义相似度和可靠度”；高可靠度技能不能绕过
轨道门禁，缺少、部分声明或未知的作用域也不能匹配正式四轨任务。

正式的1,000条公共轨迹训练由
`deployment/run_common_trajectory_training.sh` 在华硕主机后台执行。脚本先确认四分片冻结
清单和完整工件审计存在，正式导入器再逐项复核自哈希、分片文件哈希、continuation 链和
1000 条原始工件；通过后才执行无 `--limit` 的全量导入。训练结束后还会独立
审计恰好1,000条训练 episode、测试集零访问、SQLite完整性、冻结标记、批次异常以及策略、
高层经验和技能的模型来源；三类归纳对象必须全部只来自 `deepseek-v4-flash`。`--limit` 运行
始终只是预实验，不能产生正式冻结训练结论。最后一个归纳批次若保留未完成的模型格式
错误，会进行至多两次固定重试；原失败工件不会被覆盖，最终仍未解决的单项错误也会进入
正式审计，而不是静默消失。

每次归纳调用都使用单调递增的编号保存工件，并把最终调用的相对路径、状态和文件哈希写入
SQLite。审计区分“历史上发生过但后来已恢复的错误”和“最后一次调用仍未解决的错误”：前者
继续保留用于失败分析但不阻止冻结，后者以及最终工件缺失、篡改、编号断裂都会失败关闭。
正式审计还要求每个 episode 至少有一个 trace step，并核对轨迹、episode 和训练结果的任务
身份一致，避免只满足表面上的1,000行计数。

归纳工件还逐次保存请求、原始响应、请求/响应哈希、实际 provider/model、延迟、request ID、
token 用量和格式校验状态。正式审计汇总输入、输出及总 token，并要求所有有响应的归纳调用
均解析为官方 `deepseek-v4-flash` 且具有 token 证据；API 凭据不会进入这些工件。

正式审计通过后，接力脚本调用 `freeze-memory`，使用 SQLite backup 生成
`runtime/<run-id>/freeze/memory.sqlite3` 只读快照和自哈希清单。清单绑定1000条训练
episode、测试集零访问、冻结训练语料、配置、审计报告以及三类归纳对象的模型来源。
正式四轨评测以 SQLite `mode=ro&immutable=1` 打开该快照；修改文件或哈希不一致都会在
读取测试任务前失败。正式训练审计还保存整个 `msce_plc` Python 包的逐文件哈希，最终计划
要求训练审计中的代码清单、当前评测代码和预注册清单三者完全相同。

## 凭据

配置文件只写环境变量名。API key、RDP 密码和 ISPSoft 私有打包口令不得写入本目录、数据库、日志或报告。启动实验时由外层 shell 从本机全局私有配置注入。

## 命令

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

# 先做离线单元测试
.venv/bin/python -m unittest discover -s tests -v

# 校准 Oracle：每台 Huashuo Worker 都必须让参考答案通过、负控失败
.venv/bin/msce-plc oracle-calibrate --config configs/deepseek_v4_flash_huashuo.json

# 小规模端到端试跑
.venv/bin/msce-plc train --config configs/deepseek_v4_flash_huashuo.json --limit 3

# 从公共生成—编译—反馈 corpus 做独立的 L1 导入预检（不运行归纳模型）
.venv/bin/msce-plc import-trajectories \
  --config configs/deepseek_v4_flash_huashuo.json \
  --corpus-freeze-id deepseek-v4-flash-official-plc1000-human-reviewed-four-shard-freeze-v1 \
  --run-id msce-common-trace-preflight --limit 3 --skip-induction

# 1000 条公共轨迹完整后，执行正式记忆/策略/技能训练并冻结
.venv/bin/msce-plc import-trajectories \
  --config configs/deepseek_v4_flash_huashuo_local.json \
  --corpus-freeze-id deepseek-v4-flash-official-plc1000-human-reviewed-four-shard-freeze-v1 \
  --run-id msce-common-trace-four-shard-formal-v1

.venv/bin/msce-plc audit-training \
  --config configs/deepseek_v4_flash_huashuo_local.json \
  --corpus-freeze-id deepseek-v4-flash-official-plc1000-human-reviewed-four-shard-freeze-v1 \
  --run-id msce-common-trace-four-shard-formal-v1

# 早期独立 runner（仅用于兼容性/冒烟检查，不作为正式四轨结论）
.venv/bin/msce-plc train --config configs/deepseek_v4_flash_huashuo.json
.venv/bin/msce-plc test --config configs/deepseek_v4_flash_huashuo.json --memory-run <训练run-id>

# 正式评测：必须等完整baseline记忆和V5密封评测都完成后才会打开同一100题快照
.venv/bin/msce-plc evaluate-mature-final \
  --config configs/deepseek_v4_flash_huashuo_local.json \
  --plan configs/mature_final_evaluation_v1.json \
  --memory-run msce-common-trace-four-shard-formal-v1 \
  --champion-genome-id <V5冻结冠军Genome ID>
```

脚本支持断点续跑；同一个 `--run-id` 已完成的任务不会重复调用 API 或重复判卷。
`inconclusive` 会保留审计记录，但下次使用同一 run ID 时自动重试。若一整批任务均为 `inconclusive`，运行会熔断，避免在模型服务或 Windows Oracle 故障时继续消耗 1000 次调用。长期实验可使用 `deployment/run_full_experiment.sh`；它只从启动环境读取 API key，训练达到 1000 个有结论任务后冻结记忆，再运行 100 个测试任务。

本机磁盘不足时，可把代码和数据快照部署到 Huashuo Linux 宿主机，并使用 `configs/deepseek_v4_flash_huashuo_local.json`。该配置把 Oracle transport 设为 `local`，直接写三台 Windows 的共享 spool，不需要把宿主机 SSH 私钥复制到实验目录；模型 key 仍只从启动进程的环境变量读取。

华硕后台接力脚本 `deployment/run_mature_final_evaluation.sh` 会先验证固定计划及代码哈希，
然后同时等待正式baseline记忆和V5最终评测。它不会在等待阶段加载API key或读取密封题；
条件满足后才从私有环境读取官方DeepSeek凭据并断点执行上述正式命令。
