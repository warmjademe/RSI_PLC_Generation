# 当前 100 题实验的过程证据

本目录按用户要求，从 NAS 与华硕提取能证明实验过程的材料，不是整份远端运行目录备份。是否完成同步，以 `INDEX.json` 的 `status: pass` 和各组 `EXPORT.json` 的 `local_verification: all_file_hashes_pass` 为准。

六组证据已收集完成，逐文件 SHA-256 全部核验通过。GitHub 中的 `process_evidence.tar.gz` 使用 Git LFS 保存完整文件。克隆仓库后需要 Git LFS 下载实际压缩包；普通 Git 文本指针不是证据内容。

```bash
git lfs install
git lfs pull
```

| 本地目录 | 来源与实验 |
|---|---|
| `nas/qwen/` | NAS：八基线，Qwen 3.8 27B Q4 |
| `nas/deepseek/` | NAS：八基线，DeepSeek 官方 |
| `nas/haiku/` | NAS：八基线，Haiku 从原 4 次历史续跑至最多 10 次 |
| `huashuo/qwen/` | 华硕：我们的方法及 Full、NoAssets、NoFeedback、Neither 四组 |
| `huashuo/deepseek/` | 华硕：上述四组的 DeepSeek 最终合并记录 |
| `huashuo/haiku/` | 华硕：上述四组的 Haiku v3 最终记录 |

两台主机的实验对象不同，不能把八基线和我们方法的消融混在同一个分母里统计。只同步当前 100 题实验，不复制旧 117/200 题运行目录。

每组包含：

- `summary/`：可直接阅读的顶层结果、协议、配置、完成审计和成本汇总；内容保持源文件不变。
- `process_evidence.tar.gz`：选择性过程证据包，文件位于包内 `evidence/`。
- `MANIFEST.jsonl`：包内每个证据文件的相对路径、长度和 SHA-256。
- `EXPORT.json`：源运行名称、导出时间、文件数、包哈希、排除规则和本地核验结果。

## 保留的证据

保留实际测试数据与公开输入、冻结的 Python 源码及评价器文本、实验协议与配置、逐题终局结果、候选 ST、调用请求及完整响应、错误反馈、工具回执、授权评价输出与测试 trace、事件记录和 token 账本。知识机制保留每次实际检索进请求的上下文、学习提案/审计，以及原在线流最终快照中的文本状态。

成功调用已有完整 `response.json` 时，不再重复保存 `response.sse`；没有完整响应文件的调用保留原始 SSE，便于检查中断与未知用量。原调用计数和已付费记录不改写、不退款。

不保留编译二进制、中间构建文件、缓存、监测轮询、重复的中间知识库状态、向量数组、控制器权重或数据库。Haiku 的原始 4 次调用已经包含在续跑目录，不额外复制整个原始目录。保留学习审计不代表复制了所有可供恢复执行的知识资产。

## 完整性与使用边界

华硕 DeepSeek 最终目录中的链接展开为目标文件内容，包内不依赖远端软链接。远端文件只读，未修改或删除。

每个导出的文件在远端计算 SHA-256，并在本地解压读取时逐一复核。实际导出内容通过已知凭据格式的启发式扫描；这不是对任意未知编码秘密的穷尽证明。

这是用于审查过程的证据子集，不是完整可原地恢复的运行镜像。原 `protocol.lock.json`、快照清单仍可能引用有意未导出的文件；不能直接用原完整目录审计器对这个子集宣称全量复现通过。原绝对路径保留为来源证据，迁移执行需重新配置。六个完整压缩包已通过 Git LFS 发布至 `warmjademe/RSI_PLC_Generation`；可以按 `EXPORT.json` 中的哈希核验下载内容。

列出某组的证据：

```bash
tar -tzf nas/qwen/process_evidence.tar.gz
```

若需展开查看，可在单独目录执行：

```bash
mkdir -p /tmp/plc-qwen-evidence
tar -xzf nas/qwen/process_evidence.tar.gz -C /tmp/plc-qwen-evidence
```

导出规则及收集脚本分别为 `../tools/export_process_evidence.py`、`../tools/collect_process_evidence.py`。
