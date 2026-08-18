# API

- FastAPI 为薄层：输入校验、服务调用、错误映射与脱敏后的 DTO。
- 默认仅监听 `127.0.0.1`；V1 不承诺公网认证、TLS、RBAC、多租户或网络边界安全。
- 提供 Chat-first Conversation/Message、Project 创建/查询、文件导入/版本/检索，以及显式 Analysis Task 创建、查询、取消、恢复、重试、事件、Dataset、Artifact 和受控文件访问。
- V1 不提供 Webhook；SSE/WebSocket 若实现，必须复用相同事件源和隐私策略。
- API 不直接操作 SQLite、OpenSandbox、模型 SDK 或 Workspace 路径。
