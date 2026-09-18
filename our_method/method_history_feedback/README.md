# 当前任务反馈与历史经验积累

2026-09-16 14:17 最新启动范围为 [100 题四组实验](../../experiments/history_feedback100/README.md)。
Full、NoAssets、NoFeedback、Neither 均从空历史库开始，沿用本版本的两层反馈实现，
每题最多 10 个候选、64 次总调用。原 200 题实验保持停止，提速改造已取消。

2026-09-16 用户要求 Full 继续，并按本版本重新启动另外三个消融组件。四组方案见
[新版消融实验](../../experiments/history_feedback200_ablation/README.md)；Full 的原预算与进度保持，
三个新组件不加载 Full 或旧实验的知识。以下 9 月 15 日的“只运行 Full”是当时的启动范围。

2026-09-15 当前版本为 `two_level_feedback_20260915_v1`，运行入口见
[200 题新版 Full 实验](../../experiments/history_feedback200_full/README.md)。本轮在华硕本机
Qwen3.8-27B Q4_K_M 上，仅重新运行 Full，旧四组结果单独保留。200 题来自
`test_dataset_200_tasks`；输入包含完整需求、接口和给定测试，不含参考实现。

第一层处理当前题：把实际编译错误、相关代码行，或运行失败的用例、扫描输入、期望值与实际值
送入下一次生成；模型在一次响应中给出简短修复假设和完整代码。工具重新执行，判断修改后的结果。
连续两次同类失败或再次生成已有代码时，下一候选从需求和测试重新构造；阶段退步或失败断言增多时，
回到本题较好的候选再修改。不会因解释听起来合理就把假设记为成功。

第二层只在本题结束后发布经验：程序从真实回执中建立同题、同阶段的“失败→通过”转换，
模型提炼适用条件、具体写法、例外和验证建议，并由另一调用复核。模型引用转换 ID，程序解析原始
代码哈希与回执，不再要求模型抄写长段证据。编译修复允许在运行尚未成功时积累，但只能标为编译经验；
运行经验必须有运行失败→通过的证据。不支持的提议单独拒绝。没有已验证转换时保留原始失败记录，
不花额外调用强行总结。历史知识默认最多检索 4 条，只能用于之后的题，初始库为空。

每题仍最多 10 个候选、64 次总调用。没有固定的额外反思调用；生成输出最多 16,384 token，
提炼和复核各 4,096 token，所有调用和失败成本均记录。判分仍为编译及全部 100 ms 扫描测试通过，
独立终审保持原样；不做形式化验证。30 分钟只提醒，不终止仍有效生成的任务。

阶段转换和模型复核只能支持“有证据的条件性建议”，不能证明某次修改的因果性或跨任务普遍正确。
本轮同时调整了上下文、输出预算和执行策略，旧消融数据不能直接用于声称历史记忆提高了成功率。
`two_level_feedback=true` 启用此流程；省略该项仍保留旧配置兼容行为。

## 以下为旧版协议及兼容接口说明

本目录实现独立的在线知识积累方法：固定 API 模型，从空知识库开始，逐题处理
`source_codes/test_datset_117_tasks/tasks/*.json`。当前题通过错误反馈修复；题目结束后，
把有来源、带适用条件的知识发布给后续题。不会读取先前 1000 条训练资产，也不会修改模型权重。
这是借鉴 KSI 的方法实现，不是 KSI 原作者代码或原实验的复现。

## 一个完整例子

第 1 题要求“压力大于等于阈值时报警”，模型写成 `>`。执行器返回边界用例失败，
并给出当前题的实际输入、期望值和观测值。

1. 本题历史保存错误代码、代码哈希、失败阶段、实际诊断以及候选编号。
2. `task.reflect` 提出“可能遗漏等号”，明确下一步修改与可验证的预测。这时只是一条本题假设。
3. `plc.generate` 同时看到当前代码、当前题最近的反馈和已发布知识，改成 `>=`。
   新程序重新经过 compile、runtime、formal；旧程序的通过记录不能用于新程序。
4. `knowledge.curate` 对比本题失败与成功尝试，提出：“要求包含边界时，比较操作应保留等号。”
   记录适用条件、例外、建议的边界测试，以及原始代码/工具反馈的准确引用。
5. `knowledge.review` 使用同一个固定模型另开一次调用，检查这条结论是否超出证据范围。
   程序再检查来源、引用和本题成功观测；尚未验证成功的修复只保留为草稿。
   符合条件的知识在本题结果落盘后入库。
6. 第 2 题如果也是边界比较，可以检索这条规则，但不会收到第 1 题的完整程序，
   也不应照搬第 1 题的阈值或变量名。第 2 题仍需独立验证。

上面是说明机制的例子。随附 demo 使用脚本响应与合成验证器演示此过程，不证明真实 PLC 程序正确。

## 两层记录与知识状态

| 层次 | 保存什么 | 何时使用 |
|---|---|---|
| 当前题历史 | 候选代码、真实诊断、检查回执、修改说明、本题反思 | 当前题后续候选；上下文最多最近 4 次，完整记录保存在磁盘 |
| 跨题知识 | 规则、适用条件、例外、验证建议、来源引用、支持任务、环境范围、版本 | 下一题起按相关性检索，默认最多 4 条、10,000 字符 |

知识状态为 `draft`、`active`、`disputed` 或 `superseded`。只有 `active` 进入生成提示。
草稿可以在后续整理时获得更多支持；发现有证据支持的反例时撤回旧规则；修改规则使用新版本，历史保留。

默认 `min_support_tasks=1`：一题的成功经验就有机会帮助下一题，标签为 `single_task_observed`。
设成 2 时，必须累积两个不同任务的支持后才进入生成上下文；同题多次成功不能冒充多个任务。
这两个任务可能相似，标签不表示统计独立或已证明普遍正确。

准确引用只能证明“这段证据存在”。第二次模型复核也可能判断错误。因此这里的 `active`
表示“有来源、经过复核、允许条件性参考”，不是数学意义或厂商认证意义的正确知识。
旧知识与当前需求冲突时，以当前公开需求和实际验证为准。

## 运行

Python 3.11+，本方法本身仅用标准库。以下命令均在 `source_codes` 目录执行。

先运行不调用外部 API 的例子和测试：

```bash
python3 -m our_method.method_history_feedback demo --output /tmp/plc-history-feedback-demo
python3 -m unittest our_method.method_history_feedback.tests.test_online -v
```

demo 的输出目录必须是新目录。查看 `demo_result.json`、两题的 `result.json`、
`knowledge_before.json` 和 `learning/evidence_context.json` 可以跟踪完整链条。

正式运行前复制本目录的 `example_config.json` 到自己的私有运行配置中，填写模型接口和验证器。
示例配置故意没有预选付费服务或虚构可用的 PLC 工具；它不能直接通过正式运行预检。
API key 只从 `provider.api_key_env` 指定的环境变量读取，不能写进 JSON。
原生 Anthropic 接口使用 `kind: "anthropic"`，`base_url` 填到 `/v1`；其余配置结构相同。

```bash
python3 -m our_method.method_history_feedback preflight --config /absolute/path/history_config.json

python3 -m our_method.method_history_feedback init \
  --config /absolute/path/history_config.json \
  --output /absolute/path/new_history_study \
  --seed 20260914

# 真正开始模型调用与 PLC 验证；先处理一题，检查证据和成本。
python3 -m our_method.method_history_feedback run \
  --study /absolute/path/new_history_study --max-tasks 1

# 继续剩余任务，已完成的题不会重新计费或重复发布。
python3 -m our_method.method_history_feedback run --study /absolute/path/new_history_study
python3 -m our_method.method_history_feedback report --study /absolute/path/new_history_study
```

`init` 只复制 117 份公开输入、固定顺序/配置/实现哈希并建立空 SQLite 库，不调用模型。
默认用指定 seed 打乱按 ID 排序的任务；`--no-shuffle` 使用 ID 排序。每个 study 是一条串行流，
不能同时并行处理它的不同题，否则后题可见的知识取决于调度，研究协议会改变。

`run --max-tasks N` 表示本次最多继续 N 道未提交的题，不是只允许整项研究有 N 道题。
每题预算包含生成、反思、知识提炼和复核的全部模型调用，没有隐形的免费学习轮次。
默认每题最多 10 个候选、64 次模型调用；生成、反思、提炼和审核共同计入调用预算。候选通过测试后提前结束。这是上限，不是成本估算。实际 tokens 和调用数保存在报告中。

## 接入已有的 117 题验证环境

`RunContext` 复用项目现有的 JSON 验证器调用协议。本方法固定要求
`compile → runtime → formal`，前一阶段通过才进入后一阶段。
仅调用普通编译器不能代替运行时/形式验证；本方法不会自动补造运行计划或隐藏正确答案。

已有部署的 `experiments/mixed117_study/feedback_validator.py` 可以直接用作反馈适配器：
它在验证器进程中读取已经校准的当前题 oracle，返回压缩后的失败诊断，不向生成器返回参考实现。
将三个阶段的配置分别设为以下形式，替换成**验证进程所在机器的真实绝对路径**：

```json
{
  "kind": "command",
  "protocol": "json",
  "timeout_seconds": 1200,
  "command": [
    "/usr/bin/python3", "-B",
    "/absolute/path/qualified_117_study/feedback_validator.py",
    "--root", "/absolute/path/qualified_117_study"
  ],
  "env": {"PYTHONPATH": "/absolute/path/qualified_117_study/evaluator_source"}
}
```

该旧 study 仅提供已校准的验证环境。本方法没有调用旧研究的 `setup.py`、旧方法工作流或资产加载器；
也不会读取它的 1000 条历史知识。旧适配器的 `feedback_scope` 文本描述旧协议；
本方法的证据视图只提取 `stage/status/diagnostics`，按本目录的新协议整理跨题知识。
保留旧工具原始字段用于追溯，不改写旧实验。

外部验证器必须通过 stdin 接收 JSON，并在 stdout 返回包含以下字段的 JSON：
`stage`、`status`、`code_hash`、`project_hash`、`plan_hash`、`properties_hash`、
`diagnostics`、`evidence.executed`。所有哈希必须回指本次请求；即使 plan/properties 为 null，
也必须显式返回相应 null 哈希。`pass/fail` 都要求 `executed=true`。
`unknown/error` 默认对同一程序重试一次，仍不确定就结束本题为 `incomplete`；不会编造失败原因继续修复。

已有四槽位验证池可在顶层配置：

```json
{"validation_admission": {"slots": 4, "directory": "/absolute/path/shared_validation_slots"}}
```

这只共享验证资源，不改变本方法逐题学习顺序。验证工具、oracle、运行环境应在实验期间保持固定；
预检只检查配置和命令入口，不代表已经完成工具校准或真实设备验证。

## 数据边界、恢复与输出

公开任务加载器只读取 `tasks/*.json` 并使用明确字段白名单，不读取 dataset 的 evaluator、provenance
或历史训练目录。它不接受 `project_root` 外部文件引用。隐藏 oracle 只由上述独立验证器按当前题读取。
知识范围绑定任务的 `target`、ST 语言和验证器配置指纹；不会自动跨不同 PLC 平台迁移。

每题作答前固定 `knowledge_before.json`。本题处理中产生的知识不能提前进入本题的跨题检索；
反馈仍可作为本题历史使用。结果与知识发布通过 SQLite 事务按任务序号提交，禁止未来题先提交。

已经写完结果但尚未入库时中断，继续运行可以核对哈希后发布，无需新调用。
如果在付费请求或题内步骤中断、尚未写完结果，当前版本会拒绝自动重跑该题：
它保留全部日志和已发生预算，要求先审计中断现场，不能靠删除目录重新得到完整预算。
这是当前恢复能力的边界；尚未实现逐模型调用的细粒度续跑。

| 输出 | 用途 |
|---|---|
| `study.json` / `config.json` | 固定任务顺序、公开输入哈希、实现哈希与配置 |
| `knowledge.sqlite3` | 已完成任务、尝试证据和不可覆盖的知识版本 |
| `runs/0001/model/` | 各角色的请求、响应、usage 和错误 |
| `runs/0001/checks/` | 真实工具请求、输出和代码绑定回执 |
| `runs/0001/attempts/` | 每次候选、本题反馈、反思与知识使用说明 |
| `runs/0001/learning/` | 提炼时实际可见的证据和提案 |
| `runs/0001/publication.json` | 复核结果、接纳/拒绝决定、待发布知识 |
| `runs/0001/result.json` | 本题结果与包括学习调用在内的预算 |
| `report.json` / `knowledge_latest.json` | 逐题统计与当前知识快照 |

`knowledge_offered_ids` 是实际送入提示的知识；`model_reported_used_ids` 仅为模型自报使用。
二者都不能单独证明某条知识导致了成功。回放或合成工具的结果带有对应证据模式，不能作为真实通过率。

## 用于研究的比较方式

这 117 题在本方法中是**在线学习任务流**。一边利用这些题的反馈、一边测量它们，不能再把结果称为
“完全独立测试集泛化成绩”；报告因此保留 `benchmark_score: null`，同时给出首候选通过数与最终通过数。

建议后续按预先固定的任务顺序和总预算建立独立 study，分别比较：

- 只有当前题反馈：关闭 `use_cross_task_knowledge` 和 `learn_cross_task_knowledge`。
- 当前题反馈与跨题知识：采用默认配置。
- 取消本题反思调用：只关闭 `reflect_after_attempt`，仍把真实反馈交给生成器。
- 更严格的知识接纳：设 `min_support_tasks=2`。

另有 `use_task_feedback=false`，此时必须关闭本题反思，生成器不会收到工具反馈。
2026-09-14 四组实验增加了“仅历史知识”条件：允许本题作答结束后的知识提炼和复核读取私下保存的
检查证据，所发布知识只供后续题使用，不能回流到本题生成。这样历史组件可以独立于本题反馈开关。
只关闭跨题检索、保留知识学习可研究“知识积累但不注入”的效应，但其额外调用也必须计费。
不同设置采用新的输出目录，不能修改已经固定的 study 配置继续混跑。

未来要证明可迁移的软件知识，应另留未参与规则提炼和阈值选择的新任务，冻结知识库后测试。
初始代码交付仅做合成验证；实际 Qwen 四组部署及启动证据见
[117 题四组在线实验](../../experiments/history_feedback117_study/README.md)。旧数据和既有实验结果保留。
