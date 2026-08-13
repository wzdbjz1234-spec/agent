# Sandbox Boundary

- 定义稳定的 SandboxProvider、SandboxSpec、SandboxLease、ExecutionRequest/Result 与错误类型。
- 协议不依赖 OpenSandbox SDK；正式实现位于 `providers/sandbox`。
- Spec 必须表达镜像 digest、网络关闭、挂载、用户、CPU/内存/磁盘/进程/时间/输出上限。
- ExecutionRequest 只接受 Task Workspace 内的代码和数据引用，不接受 Host 命令或任意挂载。
- Sandbox 是临时计算资源，不是事实源；恢复只依赖 Runtime SQLite、checkpoint 与 Workspace。

