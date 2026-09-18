# 华硕 Qwen3.8-27B 量化部署

2026-09-15 按用户要求，停止科美 BF16 实验后，将四组实验迁移至华硕本机 RTX 4090。华硕已有 Q4_K_M 权重，完整 SHA-256 与 [ggml-org 上游文件](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF)一致，因此复用已有文件。

- 模型：`Qwen/Qwen3.8-27B-Q4_K_M`，18,973,870,432 字节。
- 模型 SHA-256：`31629f53165ab6a7dad8c9847dcfd1fdf55829dac1e6e748f4a68581b0033d34`。
- 引擎：llama.cpp b10964，commit b29c606e2；CUDA 12.8。使用 NAS 已核验官方镜像来源的运行库副本，NAS 服务不变。
- 部署目录：华硕 `/home/qyb/llm-services/qwen3.8-27b/experiment_20260915`。
- 服务：`qwen38-27b.service`；API：`http://127.0.0.1:18080/v1`。模型和实验位于同一主机，不依赖科美 SSH 转发。
- 四个服务槽，共享 196,608 token KV 池，单槽上限 131,072；KV 为 Q4_0，模型为 Q4_K_M，关闭 thinking 和上下文自动截断。
- 服务启动后显存约 22,837 MiB。客户端按“精确输入 token + 完整输出上限 + 64”预留共享容量；65,536 输出上限下通常允许两路生成并行，短反思可占其他槽。四条任务流持续独立推进，不能将四个槽描述为四路各独占 128K。

原 b10679 引擎配合更大的缓存池出现初始化显存不足，失败日志保留在 `initial_large_cache_failure.log`。原服务配置保存在 `old_unit.txt`；当前 systemd drop-in 为 `~/.config/systemd/user/qwen38-27b.service.d/experiment_20260915.conf`。

`verify.py` 的四个短请求核对 JSON、四路 SSE、无思考输出及 native tokenizer 与实际 prompt usage 一致，共 164 token；不执行 PLC 测试或测评参考代码。随后通过完整实验 Provider 运行一个独立 JSON 探针，结果和成本保存在 `adapter_verified.json`。短探针不证明四路完整长上下文同时运行的容量，也不证明量化前后的 PLC 准确率相同。

第一次正式接入的四个请求因 HTTP API 不接受 `repeat_last_n=-1` 被拒收，未产生有效候选。该失败启动及一次参数诊断保留在旧本地运行 `history_feedback200_local_q4_20260915_v1`，修正为非负的 131072 后启用 v2。API 的该取值边界与 CLI 文档中常见的特殊负值不能混用。

配置和启动脚本分别为 `config.json`、`serve.py`。实际部署验证保存在华硕的 `deployment_verified.json`、`adapter_verified.json`、`runtime_transfer_verified.json`，旧配置和旧实验均保留。
