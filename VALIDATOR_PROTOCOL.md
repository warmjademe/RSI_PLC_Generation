# 外部验证工具协议

验证工具负责真正的编译、性质检查或运行观察。baseline 负责提供候选、接收结果、选择下一步并记录证据。配置正确的工具是验证前提，框架不能从工具名判断其是否已校准。

## 配置

```json
{
  "validators": {
    "compile": {
      "kind": "command",
      "name": "configured_vendor_compiler",
      "command": ["/absolute/path/to/compiler_adapter", "--request", "{request}"],
      "protocol": "json",
      "timeout_seconds": 120
    }
  }
}
```

`command` 必须是 argv 数组，不通过 shell 拼接。工作目录是本次检查的独立工程快照。命令可以直接从 stdin 读取请求，也可以读取 `{request}`。可替换路径包括 `{workspace}`、`{source}`、`{request}`、`{result}`、`{plan}`、`{python}`。只替换这些占位符。

工具命令推荐绝对路径；命令内相对路径相对于检查工作区，不相对于配置文件。模型回放文件和知识库路径则相对于配置文件。需要额外搜索路径时可配置普通 `env` 项，例如 `PYTHONPATH`；凭据应由外部环境提供，禁止把秘密写入配置。

若工具不能让 stdout 只包含 JSON，可写入 `{result}` 并设置 `result_file: true`；其他输出会保存在 stdout/stderr 日志。超时先终止本次进程组并等待退出，再记录为环境错误。工具不得修改输入程序、请求文件或测试计划，否则该次检查记录为错误。

## 请求

```json
{
  "schema_version": 1,
  "check_id": "check-0001",
  "stage": "formal",
  "task": {"id": "...", "requirement": "...", "interface": {}},
  "target": "明确平台",
  "code": "当前入口程序",
  "entry_file": "src/main.st",
  "files": {"src/main.st": "当前入口程序", "src/types.st": "其他工程文件"},
  "properties": [],
  "plan": null,
  "code_hash": "SHA256",
  "project_hash": "SHA256",
  "plan_hash": null,
  "properties_hash": "SHA256"
}
```

阶段为 `compile`、`formal`、`specification` 或 `runtime`。性质和计划只在需要时提供；任务对象还包含公开原始文件和编辑范围。请以顶层 `files` 为待检查的当前工程，不能误检查 `task.files` 中的原版本。

`code_hash` 是入口源字符串 UTF-8 的 SHA-256。其他哈希由 `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)` 的 UTF-8 字节计算。没有计划或性质时对应哈希为 `null`。

## 返回

```json
{
  "stage": "formal",
  "status": "fail",
  "code_hash": "请求中的对应值",
  "project_hash": "请求中的对应值",
  "properties_hash": "请求中的对应值",
  "diagnostics": [{
    "category": "property_violation",
    "message": "实际验证工具给出的失败说明",
    "property_id": "P1",
    "counterexample": []
  }],
  "evidence": {"tool_version": "实际版本", "property_results": []}
}
```

`status` 必填；`diagnostics` 是数组（单个字符串也会被转成数组），`evidence` 是对象。返回的阶段或哈希字段如存在，必须与请求一致。外层同时绑定实际调用和输入快照，不允许模型自行填写一个回执来绕过工具。

状态应按照以下规则解释：

| 状态 | 含义 |
|---|---|
| `pass` | 本次请求中的检查已执行，取得足以支持通过的结果 |
| `fail` | 编译器拒绝程序，或工具取得明确的性质/断言违反结果 |
| `unknown` | 不能确定，例如性质不受支持、未产生有效结论或覆盖不完整 |
| `error` | 环境、启动、协议或工具执行错误 |

形式检查必须报告所有本次提供性质的状态；只验证一部分不能返回整个请求 `pass`。超时、状态空间限制和未生成中间文件不等于程序性质违反。运行检查应区分前置条件/输入施加失败和已经建立工况后的断言失败，保留可诊断的轨迹。空计划、恒零且没有有效输入驱动的执行不能自动提供行为通过证据。

`protocol: "exit_code"` 只允许 `compile`：退出码 0 为通过，配置的基础设施退出码或进程信号为错误，其他非零为失败。建议编译器适配器使用 JSON 保留声明区/实现区诊断。没有配置对应阶段时为 `unknown`。

另有 `protocol: "semaplc"` 用于读取已对齐的原生 runner JSON 信封，要求 `stHash` 匹配当前源码，且请求、计划与工程未被工具修改；原生信封并不自动兼容本实现的测试计划格式。只有已完成明确格式转换、范围和版本校准的适配器才能使用它，不应将同名字段作为兼容性证据。

## 最终判卷隔离

本协议是 agent 可见反馈，不是隐藏评分器。不要向这些请求附加测试参考实现、隐藏场景或评分答案。最终独立评价应另外运行并保留其证据；当前三个方法的 `result.json` 不生成统一成功率。
