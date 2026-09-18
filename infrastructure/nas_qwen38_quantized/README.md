# NAS Qwen3.8-27B 量化推理服务

用户于 2026-09-15 授权在 NAS 部署量化版。此目录管理独立的文本推理服务，不启动 PLC 实验。
2026-09-15 已部署并通过独立接口验证；NAS `evidence/latest_verification.json` 保存实测结果。

## 模型与运行环境

- 模型：`ggml-org/Qwen3.8-27B-GGUF` 的 `Qwen3.8-27B-Q4_K_M.gguf`，源模型为 `Qwen/Qwen3.8-27B`。
- 文件：18,973,870,432 字节；SHA-256 为 `31629f53165ab6a7dad8c9847dcfd1fdf55829dac1e6e748f4a68581b0033d34`。
- 从 ModelScope 同名镜像下载，文件哈希与 Hugging Face 上游 LFS 元数据逐项核对。
- 引擎：llama.cpp `b10964`（稳定发布 `v0.4.1` 指向该构建），CUDA 12.8.1。
- 从南京大学 GHCR 镜像已下载的官方镜像层提取本机 CUDA 引擎，逐层哈希与独立从上游 GHCR 读取的 linux/amd64 manifest 核对。
- 本机服务使用独立复制的 CUDA 12.8 运行库；来源、文件大小与 SHA-256 保存在 `ops/native_runtime_provenance.json`，原 Python/CUDA 环境没有修改。
- 硬件：NAS RTX 4090 24GB；启动前检测其他 GPU 计算进程，若占用则拒绝启动，不停止其他工作负载。
- 模型精度为 Q4_K_M，KV 缓存为 Q8_0；未启用视觉 projector。与既有 BF16 实验属于不同执行配置。

上游依据：[模型卡](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF)、
[llama.cpp Docker 文档](https://github.com/ggml-org/llama.cpp/blob/b10964/docs/docker.md)、
[服务端接口文档](https://github.com/ggml-org/llama.cpp/blob/b10964/tools/server/README.md)。

## NAS 路径与接口

- 部署根目录：`/home/qyb/qwen38-27b-quantized`
- 配置与脚本：`ops/`；权重：`models/`；验证证据：`evidence/`
- systemd 用户服务：`nas-qwen38-27b-q4km.service`
- 运行形式：本机 `runtime/app/llama-server`，由 systemd 管理，不依赖 Docker 容器持续运行。
- NAS 本机 OpenAI 兼容 base URL：`http://127.0.0.1:18185/v1`
- 模型标识：`qwen3.8-27b-q4_k_m`
- `/health`、`/metrics`、`/slots` 用于健康、性能及请求状态检查。

已生效配置为 8 个并发槽、共享 131,072 token KV 池，单槽上限 131,072。
单个请求可以使用该池，但八路不能同时各占 128K；多个长上下文会共享总容量。
不启用自动截断上下文。默认关闭 thinking，默认生成上限 8,192 token；调用方可传入自己的 `max_tokens`。
服务的 `--timeout 600` 是 HTTP 读写超时，不是整个生成的墙钟上限；客户端仍应设置总请求期限。
最终实际生效参数以 `ops/config.json`、systemd 启动日志和 `/props` 记录为准。
提示处理的 logical batch 为 1,024，microbatch 为 128；原先 microbatch 512 在初始化计算缓冲区时显存不足，失败日志已保留。

实测健康检查、JSON/ST 文本生成、八路 SSE 与用量回传、取消后槽位释放全部通过。
256-token 短输出探针的单路端到端速度为 39.95 token/s；八路合计为 176.10 token/s，
每路中位数为 22.04 token/s，首个正文约 1.20 秒。当前进程占用约 24,006 MiB（23.44 GiB）显存。
另一个长输入探针处理了 39,153 个实际输入 token、输出 29 token，正确返回输入开头的校验码，耗时 22.02 秒。
未实测完整 128K 输入，也未实测八个长输入同时填满缓存；八路 128K 独占缓存不属于本配置。
服务已启用 systemd 用户级自启动并确认 linger=yes，本轮未重启 NAS 验证开机行为。

```sh
# 以下命令在 NAS 执行。
systemctl --user status nas-qwen38-27b-q4km.service
systemctl --user stop nas-qwen38-27b-q4km.service
systemctl --user start nas-qwen38-27b-q4km.service
journalctl --user -u nas-qwen38-27b-q4km.service -n 80 --no-pager
curl http://127.0.0.1:18185/health
curl http://127.0.0.1:18185/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.8-27b-q4_k_m","messages":[{"role":"user","content":"简要解释 PLC 中 TON 定时器的用途。"}],"max_tokens":256,"temperature":0}'
```

从 Mac 使用 SSH 转发后访问本机 18185：

```sh
ssh -N -L 18185:127.0.0.1:18185 -i ~/.ssh/id_ed25519 -p 2222 qyb@nas.qyb.name
```

服务只绑定 NAS loopback，不新增公网端口。要长期停用并释放 GPU，执行
`systemctl --user disable --now nas-qwen38-27b-q4km.service`。
机器人训练需要 GPU 时先停止本服务；恢复本服务时会检查当前 GPU 是否有其他计算进程。

## 复核与实验边界

`download_model.py` 支持分片续传并验证完整权重哈希；`serve.py` 运行来自固定官方镜像层的本机 CUDA 引擎；
`install_service.py --start` 安装并启动用户服务；`verify_service.py` 运行独立的合成探针。
探针验证 JSON/ST 文本生成、单路和八路 SSE、服务端 token usage、取消后槽位释放以及 PLC 实验 STOP 状态。
吞吐是小样本的 token/端到端耗时，不是与科美 BF16 在同硬件、同精度下的对照，不能直接据此推断模型质量或加速比。

llama.cpp 的 `/tokenize` 格式与旧 vLLM 不同。将来若把本接口接入 PLC 实验，须显式适配上下文预检，
冻结新精度、缓存与运行参数，并重新决定预算；仅替换旧 endpoint 不能视为协议兼容。

## 部署时修复的容器设备映射

预检发现 NAS Docker Snap 的 CDI 文件仍引用旧 DRM 节点 `/dev/dri/card1`、旧 UVM 主设备号 234，
以及已不存在的 `/run/nvidia-persistenced/socket`。核对 PCI `0000:01:00.0` 后，将其更新为
`card0`、主设备号 510，并移除失效的可选 socket 挂载；驱动和库挂载保持原样。
目标为 `/var/snap/docker/3579/etc/cdi/nvidia.yaml`，原件备份和变更哈希在 `ops/nvidia-cdi-before.yaml`、`ops/cdi_repair.json`。
使用显式 `--runtime nvidia` 的最小容器已实际运行 nvidia-smi；最终模型服务采用本机运行方式。
