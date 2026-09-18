"""Generate uniform package entry points and explicit example configurations."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from baseline_common.registry import METHODS, LEARNED_ENCODERS, MODEL_LEARNING

DESCRIPTIONS = {
    "Vanilla": "不读取跨任务记忆；保留当前任务的真实验证反馈与有限重试。",
    "FewShot": "按固定种子为每个目标型号选取成功程序，所有查询使用同一组示例，不根据测试题选例。",
    "FinalCodeRAG": "从已验证成功的训练 ST 程序中，以公开需求和接口做 BM25 召回，再提供完整程序。",
    "RawTrajectoryRAG": "检索原始训练轨迹，保留候选、反馈与实际终局状态，不先提炼规则或技能。",
    "Memento": "保留原实现的 state/action/reward 案例记忆、冻结编码器余弦检索和 Top-4 选择，同时呈现成功与失败案例。",
    "EverMemOS": "保留原实现的叙述合成、原子事实与 foresight、MemCell/MemScene 聚合、混合检索和模型辅助查询改写。",
    "MemSkill": "保留原实现的记忆操作库、执行器、PPO 控制器、训练内开发划分和操作设计反馈；只训练小型控制器，不更新 PLC 大模型权重。",
    "MSCE": "保留原实现的轨迹价值回填、反思、L2 策略、L3 知识和按实际 policy gain 准入的技能结晶算子。",
    "OurMethod": "从成功 ST 程序构建合同案例库，按 A/B 子系统需求覆盖选择程序并显式展示差异；失败后按阶段检索最终成功轨迹中的真实修复转换。",
}


def save(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RuntimeError(f"Refusing to overwrite {path}")
    path.write_text(content)


def main():
    for method, folder in METHODS.items():
        root = ROOT / folder
        save(root / "__init__.py", f'"""{DESCRIPTIONS[method]}"""\nfrom .workflow import run\n\n__all__ = ["run"]\n')
        save(root / "__main__.py", f'from run_baseline import main\n\nif __name__ == "__main__":\n    raise SystemExit(main(default_method="{method}"))\n')
        save(root / "response_schemas.json", json.dumps({"generation_role": "plc.generate", "response": {"code": "one complete ST FUNCTION_BLOCK"},
             "learning": "See training.py and learning.py/engine for method-specific schemas; no fabricated successes."}, ensure_ascii=False, indent=2) + "\n")
        settings = {"memory_characters": 24000, "validation_stages": ["compile", "specification"], "unknown_tool_retries": 1}
        if method != "Vanilla":
            settings["memory_root"] = f"../artifacts/{method}"
        if method in LEARNED_ENCODERS:
            settings["encoder"] = {"kind": "mean_pool" if method == "MemSkill" else "sentence_transformer",
                                   "model_name": "REPLACE_WITH_ENCODER_PATH_OR_NAME", "revision": "REPLACE_WITH_IMMUTABLE_COMMIT", "device": "cpu", "batch_size": 8}
        if method == "FewShot":settings.update(shots=2, seed=20260910)
        if method in {"FinalCodeRAG", "RawTrajectoryRAG"}:settings["top_k"] = 2 if method == "FinalCodeRAG" else 1
        if method == "OurMethod":settings.update(use_code_memory=True, use_repair_memory=True, program_top_k=2, repair_top_k=2, code_memory_characters=14000)
        config = {"provider": {"kind": "replay", "responses": [{"role": "plc.generate", "response": {"code": "FUNCTION_BLOCK Demo\nVAR_INPUT\n Start : BOOL;\nEND_VAR\nVAR_OUTPUT\n Run : BOOL;\nEND_VAR\nRun := Start;\nEND_FUNCTION_BLOCK\n"}}]},
                  "budgets": {"max_candidates": 5, "max_model_calls": 100, "max_tool_calls": 50, "max_total_tokens": 500000,
                              "max_output_tokens": 4096, "max_wall_seconds": 1800},
                  "method": settings, "validators": {}}
        save(root / "example_config.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        training = {"provider": {"kind": "anthropic", "base_url": "https://your-provider.example/v1", "model": "claude-sonnet-4-6",
                                  "allowed_resolved_models": ["claude-sonnet-4-6"], "api_key_env": "PLC_BASELINE_API_KEY", "temperature": 0},
                    "budgets": {"max_candidates": 5, "max_model_calls": 10000, "max_tool_calls": 50,
                                "max_total_tokens": 100000000, "max_output_tokens": 8192, "max_wall_seconds": 86400},
                    "method": settings}
        save(root / "training_config.json", json.dumps(training, ensure_ascii=False, indent=2) + "\n")
        notes = ""
        if method in LEARNED_ENCODERS:
            notes += "\n需要安装 `requirements-learning.txt` 中的可选依赖，并填入实际编码器及不可变版本号。缺失依赖或版本时明确报错，不会偷偷改用随机向量或词频编码器。\n"
        if method in MODEL_LEARNING:
            notes += "\n训练会调用配置的模型，使用 `training_config.json` 中单独的训练预算。调用请求、响应、模型身份、token 与异常保存在 `<输出目录>.audit/`。本次交付未运行这些付费训练。\n"
        if method == "MemSkill":
            notes += "\n`learning.py` 包含实际 Torch PPO 算子。当前机器未安装 Torch，本次检查覆盖操作解析、记忆更新和训练分区；没有执行 PPO 数值训练，不能声称已重新训练出技能库。编码器和控制器可各自显式指定 CPU/GPU。\n"
        if method == "MSCE":
            notes += "\n本轮是历史轨迹驱动的离线适配。没有实际 policy invocation/gain 证据时，原有技能准入门禁保持关闭，`skills` 可以为 0；不会伪造增益来强行生成技能。L1/L2/L3 仍可使用。该范围必须在论文基线设置中说明，不宣称完整复现原论文在线实验。\n"
        if method == "OurMethod":
            notes += "\n资产构建不调用模型；PLC 候选仍通过配置的模型完整生成。这里复用的是合同案例与成功轨迹中的修复经验，不将训练中的零调用代码拼接或特定测试答案带入测试。`use_code_memory`、`use_repair_memory` 可用于消融。未证明单次修复与最终成功之间的因果关系；每个新候选仍需完整验证。\n"
        readme = f'''# {method}：ST 比较实现

{DESCRIPTIONS[method]}

每个方法独立保存在本目录，公开接口为 `run(task, ctx, config)`；学习入口为 `training.train(...)`。共用 `baseline_common` 的数据边界、模型传输、预算与工具回执，方法逻辑在本目录的 `workflow.py`、`training.py` 以及 `learning.py` / `engine/` / `retrieval.py` 中。

## 训练并冻结资产

从 `source_codes` 执行。共同训练数据只需准备一次：

```bash
python3 -B train_baseline.py prepare --dataset final_train_datasets --output artifacts/common_corpus
python3 -B train_baseline.py train --method {method} --corpus artifacts/common_corpus --config {folder}/training_config.json --output artifacts/{method}
```

所有路径指向新目录；已有输出不会覆盖。`training_config.json` 的模型地址和编码器是明确的占位配置，需要按实际实验设置填写。Vanilla、FewShot、FinalCodeRAG、RawTrajectoryRAG 和 OurMethod 不创建模型客户端；它们的资产准备不消耗 API。Memento 需要真实冻结编码器，EverMemOS/MemSkill/MSCE 另需学习模型。
{notes}
## 生成与验证

```bash
python3 -B -m {folder} --task-id TE_C01_C01_01 --dataset test_dataset --config {folder}/example_config.json --output runs/{method}-demo
```

`example_config.json` 用一个自编程序演示响应结构，provider 是 replay，未配置 PLC 工具。只有 Vanilla 无需先建资产；其他方法先完成上述训练并配置正确的 `memory_root`。此配置不是可用于正式实验的模型/验证配置，预期为 `incomplete`，不能用 replay 程序评估成功率。正式实验替换 provider 与 validators，详见上层 README 和 `VALIDATOR_PROTOCOL.md`。

所有比较方法默认最多 5 个候选（首次生成 + 最多 4 次修复）、100 次总模型调用和 50 次工具调用；记忆检索所需模型调用同样计数。每次修改后重新验证；工具 `unknown/error` 只允许原样重试，不视为程序逻辑失败。所有配置阶段对当前候选返回真实 `pass` 才结束为 `passed`，其范围是配置工具的检查结果，不等于独立隐藏测试通过。

测试阶段资产只读，输入与训练任务 ID、基础行为组合组、语义签名相交时拒绝执行。生成器读取 `test_dataset/public`，不会访问 `test_dataset/evaluator`。输出包含当前源码、工具回执、逐次模型用量、检索内容及停止原因。

## 方法来源与验证边界

这是对项目既有方法代码的 ST 数据适配。旧实现的具体迁移文件与 SHA-256 见 `../MIGRATION_SOURCES.json`；历史机制说明在 `../docs/previous_named_baselines.md`、`../docs/previous_msce.md`。FinalCodeRAG/RawTrajectoryRAG 本轮改为读取压缩训练交付包，使用公共 BM25 和完整记录预算，不再依赖旧实验绝对路径。固定 Few-shot 是新增的朴素对照。OurMethod 的训练试点历史见本目录的 `PREVIOUS_METHOD_EVIDENCE.md`，该历史不代表本轮测试效果。

运行本方法离线检查：

```bash
python3 -B -m unittest discover -s {folder}/tests -v
```

测试只证明相应软件分支、数据约束和编排行为，不代表真实模型、编译器、形式验证或 PLC 硬件实验已经完成。正式比较仍需固定模型版本、预算、评价器和独立测试协议。当前 50 题来自已用于方法设计反馈的旧 100 题，不能称为从未见过的留出集。
'''
        save(root / "README.md", readme)
    print(f"Created entry points, configs and Chinese documentation for {len(METHODS)} methods")


if __name__ == "__main__":
    main()
