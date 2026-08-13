# Orchestration

- 使用 LocalDurableExecutor 与 Runtime SQLite 管理 Task/Run 生命周期、本地队列、lease、heartbeat、取消、恢复和有限重试。
- V1 不依赖 Prefect；不要创建与领域状态重复的第二套工作流事实源。
- 状态与阶段分离：Run status 表示生命周期，phase 表示 PREPARING/REASONING/EXECUTING/VERIFYING/FINALIZING。
- Host/Sandbox 故障恢复同一 Run；终态 Run 的用户重试创建新 Run。
- Run 开始前固定 project_snapshot_id；恢复同一 Run 时不得切换到 Project 最新版本。
- 每个外部副作用先持久化意图并使用幂等键；崩溃恢复从已提交 checkpoint 与 Workspace 重建。
