# Memento：ST 比较实现

保留原实现的 state/action/reward 案例记忆、冻结编码器余弦检索和 Top-4 选择，同时呈现成功与失败案例。

每个方法独立保存在本目录，公开接口为 `run(task, ctx, config)`；学习入口为 `training.train(...)`。共用 `baseline_common` 的数据边界、模型传输、预算与工具回执，方法逻辑在本目录的 `workflow.py`、`training.py` 以及 `learning.py` / `engine/` / `retrieval.py` 中。

## 训练并冻结资产

从 `source_codes` 执行。共同训练数据只需准备一次：

```bash
python3 -B train_baseline.py prepare --dataset final_train_datasets --output artifacts/common_corpus
python3 -B train_baseline.py train --method Memento --corpus artifacts/common_corpus --config baseline_Memento/training_config.json --output artifacts/Memento
```

所有路径指向新目录；已有输出不会覆盖。`training_config.json` 的模型地址和编码器是明确的占位配置，需要按实际实验设置填写。Vanilla、FewShot、FinalCodeRAG、RawTrajectoryRAG 和 OurMethod 不创建模型客户端；它们的资产准备不消耗 API。Memento 需要真实冻结编码器，EverMemOS/MemSkill/MSCE 另需学习模型。

需要安装 `requirements-learning.txt` 中的可选依赖，并填入实际编码器及不可变版本号。缺失依赖或版本时明确报错，不会偷偷改用随机向量或词频编码器。

## 生成与验证

```bash
python3 -B -m baseline_Memento --task-id TE_C01_C01_01 --dataset test_dataset --config baseline_Memento/example_config.json --output runs/Memento-demo
```

`example_config.json` 用一个自编程序演示响应结构，provider 是 replay，未配置 PLC 工具。只有 Vanilla 无需先建资产；其他方法先完成上述训练并配置正确的 `memory_root`。此配置不是可用于正式实验的模型/验证配置，预期为 `incomplete`，不能用 replay 程序评估成功率。正式实验替换 provider 与 validators，详见上层 README 和 `VALIDATOR_PROTOCOL.md`。

所有比较方法默认最多 5 个候选（首次生成 + 最多 4 次修复）、100 次总模型调用和 50 次工具调用；记忆检索所需模型调用同样计数。每次修改后重新验证；工具 `unknown/error` 只允许原样重试，不视为程序逻辑失败。所有配置阶段对当前候选返回真实 `pass` 才结束为 `passed`，其范围是配置工具的检查结果，不等于独立隐藏测试通过。

测试阶段资产只读，输入与训练任务 ID、基础行为组合组、语义签名相交时拒绝执行。生成器读取 `test_dataset/public`，不会访问 `test_dataset/evaluator`。输出包含当前源码、工具回执、逐次模型用量、检索内容及停止原因。

## 方法来源与验证边界

这是对项目既有方法代码的 ST 数据适配。旧实现的具体迁移文件与 SHA-256 见 `../MIGRATION_SOURCES.json`；历史机制说明在 `../docs/previous_named_baselines.md`、`../docs/previous_msce.md`。FinalCodeRAG/RawTrajectoryRAG 本轮改为读取压缩训练交付包，使用公共 BM25 和完整记录预算，不再依赖旧实验绝对路径。固定 Few-shot 是新增的朴素对照。OurMethod 的训练试点历史见本目录的 `PREVIOUS_METHOD_EVIDENCE.md`，该历史不代表本轮测试效果。

运行本方法离线检查：

```bash
python3 -B -m unittest discover -s baseline_Memento/tests -v
```

测试只证明相应软件分支、数据约束和编排行为，不代表真实模型、编译器、形式验证或 PLC 硬件实验已经完成。正式比较仍需固定模型版本、预算、评价器和独立测试协议。当前 50 题来自已用于方法设计反馈的旧 100 题，不能称为从未见过的留出集。
