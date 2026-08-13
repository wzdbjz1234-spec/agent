# Providers

- Provider 是第三方 SDK 和基础设施细节的唯一落点，实现上层定义的 Protocol。
- V1 正式 Provider：OpenSandboxProvider、LocalWorkspaceProvider、LocalDurableExecutor、SQLite storage、OpenTelemetry adapter。
- Provider 必须支持超时、取消、错误分类、资源清理、配置校验和可关联遥测。
- 不得向领域层泄漏 SDK 类型；不得静默降级安全配置。
- AgentFS、Prefect、MLflow 与远程 Blob 均非 V1 正式依赖。

