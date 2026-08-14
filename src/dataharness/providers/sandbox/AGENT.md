# OpenSandbox Provider

- V1 唯一正式 SandboxProvider，封装 OpenSandbox SDK，不向上层泄漏 SDK 类型。
- 实现 create/connect/execute/terminate，并将供应商错误映射为稳定领域错误。
- 官方 SDK 的受控实现位于 `opensandbox_sdk.py`（SdkOpenSandboxClient）：把 SandboxSpec 翻译为 SDK 创建参数（digest 引用、deny-all egress、三项 host volume、metadata 回写），把运行时事实翻译为 SandboxAttestation（有界探测：user/uid、NoNewPrivs、CapEff、根可写性、出站网络、挂载读写、cgroup 内存）。
- 创建和重连后校验 image digest（创建时由 docker daemon 对 digest 引用强制，重连时比对 metadata）、断网、非特权用户、根文件系统对执行用户不可写、ProjectSnapshot 只读挂载、Task 可写挂载及资源上限。
- 一个 Run 默认一个可替换 lease；每个 Step 启动独立进程并在结束时清理残留进程。
- 同一 Project 的并行 Run 必须使用独立 lease；取消或销毁一个 Run 不得影响另一个 Run。
- 配置或 attestation 不符合请求时 fail closed，禁止回退到 Host 执行或宽松容器。
