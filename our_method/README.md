# OurMethod：ST 比较实现

2026-09-16 14:17 当前实验已切换到 [100 题四组实验](../experiments/history_feedback100/README.md)。
原 200 题四组保持停止、结果保留；新四组从空历史库重新运行，方法和每题 10 次尝试设置保持，
只调整测试集合及题数处理，没有应用提速改造。以下条目保留此前启动历史。

2026-09-16 用户要求新版 Full 持续运行，并从空知识库重新启动新版 NoAssets、NoFeedback、Neither。
四组共同协议及运行入口见 [新版消融实验](../experiments/history_feedback200_ablation/README.md)。
本轮沿用两层反馈方法和每题 10 候选预算，Full 已有进度保留；旧版结果单独保存。

2026-09-15 当前入口为 [两层反馈方法](method_history_feedback/README.md) 和
[华硕本机 Qwen 量化模型的 200 题 Full 实验](../experiments/history_feedback200_full/README.md)。
当前题用真实测试反馈修复；题后按编译或运行“失败→通过”的证据发布条件性经验。
本轮只重跑 Full，每题最多 10 个候选、64 次总调用，旧四组暂停并保留。以下按日期保留历史说明。

2026-09-14 最新实验入口为 [method_history_feedback](method_history_feedback/README.md) 及
[117 题四组在线实验](../experiments/history_feedback117_study/README.md)。用户已停止此前 repair-policy
实验（保留 467/468 项结果及一项中断现场），改用四个独立空知识库，比较历史知识与本题反馈。
模型仍为科美 Qwen3.8-27B BF16；用户确认每题最多 20 个代码候选、64 次总模型调用，学习调用计入成本。
以下关于此前 Qwen 实验继续收尾的文字已被本次停止指令覆盖，DeepSeek 接续仍禁用。

2026-09-14 新实验入口为 [历史修改策略与 Qwen 117 题消融](../experiments/repair_policy117_study/README.md)：使用科美 `qwen3.8-27b-bf16`，原 Full、NoAssets、NoFeedback 各 117 题，每题每组最多 20 次。按用户后续要求，已补充并启动 [Neither 第四组](../experiments/repair_policy117_neither_20260914/README.md)，同时关闭历史资产与模型可见的任务错误反馈，共计 468 项运行。实现见 `repair_policy.py`、`policy_workflow.py` 及补充组目录；本目录以下 45 题、DeepSeek、五次预算文字是历史版本说明。

2026-09-14 17:36 范围更新：用户取消了 [本地 DeepSeek Flash 接续实验](../experiments/repair_policy117_deepseek_20260914/README.md)。接续服务已停止并 mask，尚未启动 GPU 切换或 DeepSeek 生成请求。当前 Qwen 四组继续完成 468 项及结果审计，随后结束本轮实验，不自动启动后续模型；用户准备更新方法论。

此前 45 题研究的方法入口为 [两组件方法与证据边界](CURRENT_METHOD.md)：全部 1000 条原始训练记录建立历史软件资产，测试时结合本题反馈修复；当时只运行 Full、NoAssets、NoFeedback 三组。已取消训练留出及额外训练练习，测试反馈不进入跨题资产。对应后台目录、冻结版本和结果见 [历史实验协议](../experiments/historical_assets_study/CURRENT_PROTOCOL.md)。

以下保留静态基线入口、旧训练练习实现及其复现说明。它们不代表当前后台候选的启动或重启命令；效果主张以完整、已审计的三组结果为准。

## 静态比较实现与历史说明

从成功 ST 程序构建合同案例库，按 A/B 子系统需求覆盖选择程序并显式展示差异；失败后按阶段检索最终成功轨迹中的真实修复转换。

方法论以 [软件知识沉淀与复用设计](METHODOLOGY.md) 为准：先确定知识需求，再从训练证据提炼候选，按需选择规则、模板、状态机、关系图或操作技能，经过训练内验证后发布版本。知识图谱是关系类知识的一种表示工具；方法贡献需由证据约束的提炼、条件化复用和递归版本更新的实验结果支撑。新增 `knowledge_candidates.py` 统一多种候选表示、原文引用与验证义务；候选检查通过不等于语义正确或允许用于生成。

当前实现保留静态比较入口，并新增训练阶段的多轮 RSI：当前版本完成训练练习、验证程序、提炼有来源的技能，在训练内部开发集上通过配对准入后发布新版本，供下一轮使用。测试阶段始终只读，禁止将测试反馈用于资产积累、参数选择或版本晋级。不更新生成模型权重，也不能在取得比较证据前声称提高成功率。

完整流程、隔离边界、准入规则、消融接口和华硕运行说明见 [RSI 设计与协议](RSI_DESIGN.md)。RSI 编排入口为 `python -B -m experiments.rsi_study.run --root <独立研究目录> --phase all`；以下 `training.train` 命令只构建静态初始资产，不自动执行多轮学习。

2026-09-11 按用户要求启动华硕测试。独立运行目录为 `/home/qyb/RESEARCH/RSI_PLC_Generation/our_method_study_20260911`，与八个基线使用相同的 1000 条训练输入和 50 个 ST 测试任务。先构建并冻结资产，再调用 DeepSeek 官网；仅官网 HTTP 402 余额不足时使用 TeamRouter。测试每题最多五个候选，记录全部请求、token、反馈和独立最终评价。准备方式、校准复用依据和后台入口见 [本轮测试说明](../experiments/deepseek_study/OUR_METHOD_RUN.md)。

每个方法独立保存在本目录，公开接口为 `run(task, ctx, config)`；学习入口为 `training.train(...)`。共用 `baseline_common` 的数据边界、模型传输、预算与工具回执，方法逻辑在本目录的 `workflow.py`、`training.py` 以及 `learning.py` / `engine/` / `retrieval.py` 中。

## 训练并冻结资产

从 `source_codes` 执行。共同训练数据只需准备一次：

```bash
python3 -B train_baseline.py prepare --dataset final_train_datasets --output artifacts/common_corpus
python3 -B train_baseline.py train --method OurMethod --corpus artifacts/common_corpus --config our_method/training_config.json --output artifacts/OurMethod
```

所有路径指向新目录；已有输出不会覆盖。`training_config.json` 的模型地址和编码器是明确的占位配置，需要按实际实验设置填写。Vanilla、FewShot、FinalCodeRAG、RawTrajectoryRAG 和 OurMethod 不创建模型客户端；它们的资产准备不消耗 API。Memento 需要真实冻结编码器，EverMemOS/MemSkill/MSCE 另需学习模型。

静态初始资产构建不调用模型；多轮 RSI 的训练练习和技能提炼会调用模型，并分别记录 token。PLC 候选仍通过配置的模型完整生成。这里复用的是合同案例与成功轨迹中的修复经验，不将训练中的零调用代码拼接或特定测试答案带入测试。`use_code_memory`、`use_repair_memory` 等开关可用于消融。未证明单次修复与最终成功之间的因果关系；每个新候选仍需完整验证。

## 生成与验证

```bash
python3 -B -m our_method --task-id TE_C01_C01_01 --dataset test_dataset --config our_method/example_config.json --output runs/OurMethod-demo
```

`example_config.json` 用一个自编程序演示响应结构，provider 是 replay，未配置 PLC 工具。只有 Vanilla 无需先建资产；其他方法先完成上述训练并配置正确的 `memory_root`。此配置不是可用于正式实验的模型/验证配置，预期为 `incomplete`，不能用 replay 程序评估成功率。正式实验替换 provider 与 validators，详见上层 README 和 `VALIDATOR_PROTOCOL.md`。

所有比较方法默认最多 5 个候选（首次生成 + 最多 4 次修复）、100 次总模型调用和 50 次工具调用；记忆检索所需模型调用同样计数。每次修改后重新验证；工具 `unknown/error` 只允许原样重试，不视为程序逻辑失败。所有配置阶段对当前候选返回真实 `pass` 才结束为 `passed`，其范围是配置工具的检查结果，不等于独立隐藏测试通过。

测试阶段资产只读，输入与训练任务 ID、基础行为组合组、语义签名相交时拒绝执行。生成器读取 `test_dataset/public`，不会访问 `test_dataset/evaluator`。输出包含当前源码、工具回执、逐次模型用量、检索内容及停止原因。

## 方法来源与验证边界

这是对项目既有方法代码的 ST 数据适配。旧实现的具体迁移文件与 SHA-256 见 `../MIGRATION_SOURCES.json`；历史机制说明在 `../docs/previous_named_baselines.md`、`../docs/previous_msce.md`。FinalCodeRAG/RawTrajectoryRAG 本轮改为读取压缩训练交付包，使用公共 BM25 和完整记录预算，不再依赖旧实验绝对路径。固定 Few-shot 是新增的朴素对照。OurMethod 的训练试点历史见本目录的 `PREVIOUS_METHOD_EVIDENCE.md`，该历史不代表本轮测试效果。

运行本方法离线检查：

```bash
python3 -B -m unittest discover -s our_method/tests -v
```

测试只证明相应软件分支、数据约束和编排行为，不代表真实模型、编译器、形式验证或 PLC 硬件实验已经完成。正式比较仍需固定模型版本、预算、评价器和独立测试协议。当前 50 题来自已用于方法设计反馈的旧 100 题，不能称为从未见过的留出集。
