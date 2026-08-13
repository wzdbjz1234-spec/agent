# OpenSandbox Provider

- V1 唯一正式 SandboxProvider，封装 OpenSandbox SDK，不向上层泄漏 SDK 类型。
- 实现 create/connect/execute/terminate，并将供应商错误映射为稳定领域错误。
- 创建和重连后校验 image digest、断网、非特权用户、只读根、Task-scoped 挂载及资源上限。
- 一个 Run 默认一个可替换 lease；每个 Step 启动独立进程并在结束时清理残留进程。
- 配置或 attestation 不符合请求时 fail closed，禁止回退到 Host 执行或宽松容器。

