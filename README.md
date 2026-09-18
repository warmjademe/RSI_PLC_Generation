# PLC ST：基线与 RSI 方法比较代码

本目录保留 8 个基线、我们的方法及最近一轮 **100 个任务**的测试数据与本地实验记录。2026-09-18 已将旧 117/200 题数据、旧实验输出和旧离线训练数据移到本目录之外的可恢复归档；清单见 `CLEANUP_100_ONLY.json`。没有修改 NAS 实验，也没有执行 GitHub 上传。

后续发布范围更新：按用户要求，GitHub 发布包含本目录的代码、100 题数据与 `RESULTS/` 完整选择性证据包，**排除根目录 `experiments/`**。因此下文引用的实验编排模块是本地目录说明，不保证这些被明确排除的模块在 GitHub 中可用。大证据包通过 Git LFS 保存，下载说明见 `RESULTS/README.md`。

当前唯一测试数据集为 `test_dataset_100_tasks/`：从原 200 题中按参考代码长度选取 100 题，包含公开任务、测试向量和评价计划，不包含参考实现。当前在线实验不加载旧 1000 题历史训练数据，只使用同一方法已完成的任务经验；基础生成模型权重不更新。

## 当前实验和记录入口

- 数据及选择依据：[`test_dataset_100_tasks/README.md`](test_dataset_100_tasks/README.md)、`manifest.json`、`selection.json`。
- 八基线入口：`experiments/baseline8_quantized100_study`、`baseline8_deepseek_official100`、`baseline8_haiku100`。部分公共模块仍以 `117`、`200` 命名，这是代码依赖，不代表保留旧测试结果。
- 八基线逐次状态快照：`artifacts/status_three_models_*`；Haiku 历史续跑说明：[`artifacts/haiku_extend10_20260917/README.md`](artifacts/haiku_extend10_20260917/README.md)。保留原 4 次记录是为了核对续跑成本，不能作为另一份独立样本重复统计。
- 我们的方法和四组消融：`our_method/method_history_feedback`、`experiments/history_feedback100*`；汇总：[`artifacts/three_model_final_20260918/REPORT.zh.md`](artifacts/three_model_final_20260918/REPORT.zh.md)。**该汇总是我们的方法及消融，不是八基线最终结果。**
- `artifacts/` 保留本机已有的 100 题配置、审计证据和历史快照，现已取消整目录 Git 忽略。完整逐次交互及编译工作目录主要仍在 NAS，本目录不冒充完整远端复现包。
- [`RESULTS/README.md`](RESULTS/README.md)：2026-09-18 按新要求从 NAS、华硕收集的选择性过程证据，包括测试数据、模型交互、验证回执、学习审计和成本账本。压缩包的逐文件完整性以 `RESULTS/INDEX.json` 与各包 `EXPORT.json` 为准；不包含全部编译产物和重复知识快照。

成功指同一候选代码通过绑定的编译和给定有限运行断言；unknown 不计成功，也不等于确认的程序错误。Haiku 采用原逐题知识快照进行 4→10 次续跑，需披露与从头在线 10 次的差异。任务池已有历史暴露，不称为独立未见留出集。

上传前检查与清理记录见 [`GITHUB_PREPARATION.md`](GITHUB_PREPARATION.md)。环境中的绝对路径、模型服务及编译工具链需要另外配置；保存脚本不表示可以直接在任意电脑复现实验。

## 方法代码概览

| 目录 | 比较对象 | 学习/使用的内容 | 资产构建是否调用生成模型 |
|---|---|---|---|
| `baseline_Vanilla` | 无跨任务记忆 | 当前题需求；当前实验不使用本题错误反馈 | 否 |
| `baseline_FewShot` | 固定少样本 | 按固定种子选定的成功 ST 示例 | 否 |
| `baseline_FinalCodeRAG` | 成功代码检索 | 根据需求/接口 BM25 检索最终成功程序 | 否 |
| `baseline_RawTrajectoryRAG` | 原始轨迹检索 | 候选、失败反馈、修复过程和真实终局状态 | 否 |
| `baseline_Memento` | 非参数案例记忆 | state/action/reward、冻结编码器、Top-4 | 否；需编码器 |
| `baseline_EverMemOS` | 结构化长期记忆 | MemCell、MemScene、事实与混合检索 | 是 |
| `baseline_MemSkill` | 记忆操作学习 | 操作执行/设计、PPO 小型控制器 | 是；另需 Torch |
| `baseline_MSCE` | 多层知识演化 | 轨迹价值、反思、策略、知识与有准入条件的技能 | 是 |
| `our_method` | 成功合同迁移与成功修复记忆 | A/B 需求覆盖、合同差异、最终成功轨迹中的修复转换 | 否 |

Memento、EverMemOS、MemSkill、MSCE 的核心学习算子由既有项目代码迁移，来源及迁移时 SHA-256 见 `MIGRATION_SOURCES.json`。本轮替换了旧数据绝对路径、固定 DeepSeek 路由和旧测试协议，保留实际方法区别，不把同一个 RAG 换名当成多个基线。具体适配偏差见各方法 README。

MSCE 保留原 policy-gain 技能准入规则；新训练轨迹没有真实策略调用增益证据时，不伪造该证据，技能数可能为零。这是离线适配的限制。MemSkill 保留实际 PPO 实现，但本机未安装 Torch，本轮没有执行 PPO 数值训练。它们的代码交付不等于已经完成新一轮模型训练或效果评测。

## 旧离线接口参考（不是当前 100 题实验协议）

以下保留代码最初交付时的离线接口说明及当时的验证数字，仅作 API 和迁移历史参考。所引用的 `final_train_datasets`、旧 `test_dataset` 等数据不在本次上传目录中；不要将这些离线训练命令或旧测试结论当作当前在线实验的复现入口。

### 先准备公共训练输入，再按方法建立资产

在当前目录执行，Python 3.11+，公共入口和简单方法只需标准库：

```bash
python3 -B train_baseline.py prepare \
  --dataset final_train_datasets --output artifacts/common_corpus

python3 -B train_baseline.py train --method FinalCodeRAG \
  --corpus artifacts/common_corpus \
  --config baseline_FinalCodeRAG/training_config.json \
  --output artifacts/FinalCodeRAG

python3 -B train_baseline.py train --method OurMethod \
  --corpus artifacts/common_corpus \
  --config our_method/training_config.json --output artifacts/OurMethod
```

`prepare` 校验原始压缩包哈希、每条成功程序的记录与代码哈希、公开合同对应关系，以及历史 ledger 哈希链。它只读取明确列出的训练文件，不访问测试目录。新的准备目录包含：

- `examples.jsonl.gz`：1000 份最终成功 ST，含公开合同和来源绑定。
- `episodes.jsonl.gz`：每题一个可供旧学习算子使用的记录。选取最新的、对应当前合同的历史轨迹；没有历史轨迹时明确标为 `final_artifact_only`，仅记录最终成功程序。
- `historical_episodes.jsonl.gz`：全部可用的当前合同 ST 历史轨迹，保留成功和失败。
- `manifest.json`：选择规则、排除数、来源哈希和生成文件哈希。

实测当前交付包有 **803 条**带候选的当前合同 ST 历史轨迹，覆盖 **610 个任务**；其余 **390 题**只有可直接导入的最终成功程序。另有 881 条 ST 历史轨迹的合同与当前冻结版本不同，默认排除。每题选出的最新历史记录加最终程序占位记录共 1000 条，其中 742 条终局为成功、258 条是历史失败；这不改变最终成功程序数为 1000 的事实，二者属于不同记录层，代码没有把失败历史改写成成功。

所有方法接收同一份准备包，按其机制选择资产：FinalCodeRAG/FewShot 使用最终成功程序；RawTrajectoryRAG 与三个已命名记忆基线使用每题记录；OurMethod 同时使用最终成功程序和成功历史中的修复转换。这里包含“资产表示/可用信息的差异”，正式实验须报告，并可用统一历史记录来源另做消融，不能将所有差异都归因于检索算法。

模型学习方法使用各自的 `training_config.json`。地址和编码器版本是占位符，必须显式填写。可选依赖列表见 `requirements-learning.txt`；不会自动安装大模型或抢占远程 GPU。训练内开发划分只取训练任务，不读取这 50 个测试任务。

已有输出目录不会被覆盖。新学习输出包含 `adapter_manifest.json` 和 `adapter_inputs.json`，生成前检查文件完整性及查询任务与训练任务的身份/组合组/语义签名交集。生成期间不更新这些资产，不把测试轨迹回灌训练。

## 运行一个测试任务

```bash
python3 -B -m baseline_FinalCodeRAG \
  --task-id TE_C01_C01_01 --dataset test_dataset \
  --config baseline_FinalCodeRAG/example_config.json --output runs/finalcode-demo

python3 -B -m our_method \
  --task-id TE_C01_C01_01 --dataset test_dataset \
  --config our_method/example_config.json --output runs/ours-demo
```

也可用 `python3 -B run_baseline.py --method OurMethod ...`。如果使用自有公开任务 JSON，改用 `--task <文件>`；其公开字段格式沿用 `baseline_Agents4PLC` 的公共协议。

示例 `provider` 是 replay，响应是自编说明程序，不是当前测试题的正确答案；未配置验证器时结果为 `incomplete`。正式执行前，替换为自己的 HTTP provider 和经过校准的工具。程序支持原生 Anthropic Messages 和 OpenAI 兼容接口，模型与返回身份允许列表显式配置；密钥只通过 `api_key_env` 读取，不自动选择账户、回退提供商或重试付费请求。

```json
{
  "provider": {
    "kind": "anthropic",
    "base_url": "https://your-provider.example/v1",
    "model": "claude-sonnet-4-6",
    "allowed_resolved_models": ["claude-sonnet-4-6"],
    "api_key_env": "PLC_BASELINE_API_KEY",
    "temperature": 0
  }
}
```

模型设置对所有方法应一致。编码器选择及版本应另行固定。当前公共 HTTP 实现为非流式；若平台长请求需要流式处理，应另行适配，不把旧采集服务的能力当成本入口已具备的能力。

## 验证、预算和结果含义

所有方法使用同一候选循环。默认最多 **5 个候选**（首次生成加最多 4 次修复）、100 次总模型调用、50 次工具调用、500000 token 和 1800 秒。EverMemOS 的模型辅助检索也计入总模型/token/时间预算。失败请求计数，未知反馈不会被当成代码错误。当前接口按模板保留最高 5 个候选的比较协议；这与历史训练采集的 10/20/40 次预算是不同实验。

默认检查阶段为 `compile` 和 `specification`，均须配置真实工具。接口见 [VALIDATOR_PROTOCOL.md](VALIDATOR_PROTOCOL.md)。`specification` 适配器可调用已有动态测试与 PLCverif，并返回明确的失败门名称；工具应绑定当前代码/工程哈希，固定测试要求，保留有限测试和形式验证的边界。ISPSoft/COMMGR 没有被设为本次训练资产整理的必需条件。

每次改码后旧回执失效。只有当前候选在全部配置阶段真实通过才返回 `passed`；工具缺失、超时或不支持返回 `incomplete`。`passed` 只描述配置工具的检查范围，`benchmark_score` 仍为空。最终比较成绩应由独立的统一评分流程生成；本次没有把模型自评或 replay 结果当成 PLC 正确性证据。

每次运行保存 `task.json`、`run_config.json`、模型请求/响应与 token、工具请求/回执、`events.jsonl`、逐次 `memory/` 内容、候选 ST 和 `result.json`。学习模型调用另保存在 `<资产目录>.audit/`。资产构建费用与生成费用分别报告；报告货币成本须提供实际费率，不能只凭 token 数断言费用。

## 数据边界与旧实验清理

当前训练包 `final_train_datasets`、50 题 `test_dataset` 及原始任务源 `training_datasets/plc_rsi_1000_100_v1` 保留。原始任务源用于重建与审计，不作为新入口的隐式学习源。

淘汰的旧代码树、旧实验轨迹、旧模型 smoke test 和 LD 路由/校准试验在本轮验证后移出工作目录，归档到同级 `source_codes_archive`。实际移动清单和校验见 `CLEANUP_REPORT.json`。采用保留原文件的归档迁移，便于追溯和恢复，不用删除失败记录换取目录整洁；同盘移动不会释放磁盘容量。新代码不依赖归档目录。

这 50 题与当前训练任务/成功程序没有直接重叠，但来自曾用于方法设计反馈的旧 100 题。使用历史已记录于 `test_dataset/README.md`，不能称为全新独立留出集。公共生成端只读取 `public/`；评价端单独读取 `evaluator/`。目录划分是软件输入边界，真实运行环境仍需配置文件访问隔离。

## 离线交付检查

```bash
python3 -B -m unittest discover -s tests -v
python3 -B tools/verify_delivery.py
```

各方法 README 另提供其单独的测试入口。离线检查使用自编脚本化模型/编码器与工具协议 fixture；它们测试编排，不进行 PLC 编译或模型能力评估。本次真实数据导入的统计和最终测试结果见 `DELIVERY_VALIDATION.json`。检查不启动华硕任务、不修改远程服务，也不启动付费实验。
