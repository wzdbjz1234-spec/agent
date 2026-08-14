# Sandbox Boundary

- 定义稳定的 SandboxProvider、SandboxSpec、SandboxLease、ExecutionRequest/Result 与错误类型。
- 协议不依赖 OpenSandbox SDK；正式实现位于 `providers/sandbox`。
- Spec 必须表达镜像 digest、网络关闭、挂载、用户、CPU/内存/磁盘/进程/时间/输出上限。
- `root_read_only` 的 V1 语义是「根文件系统对执行用户不可写」：官方 OpenSandbox docker 后端不提供只读根挂载，等价保证由非 root sandbox 用户 + no_new_privileges + CapEff=0 提供；attestation 以运行时探测事实为准（见 ARCHITECTURE.md 8.4）。
- ExecutionRequest 只接受当前 ProjectSnapshot 的只读引用和 Task Workspace 内代码/数据引用，不接受 Host 命令或任意挂载。
- Sandbox 是 Run-scoped 临时计算资源，不是 Project 事实源；恢复只依赖 Runtime SQLite、checkpoint、固定 Snapshot 与 Workspace。
