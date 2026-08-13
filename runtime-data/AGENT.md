# Runtime Data

本目录是本地运行数据根目录，不提交运行时内容。

- `runtime.db`：Task/Run/Step、领域元数据、事件、lease、幂等键与本地队列。
- `tasks/{task_id}/`：该 Task 的 `inputs/working/staging/datasets/artifacts/state`。
- `privacy/{task_id}.db`：Task 级占位映射；不得进入 Workspace、Sandbox、日志或 Runtime SQLite。
- `inputs` 只读且不可变；正式输出只能由 Host 从 `staging` 发布。
- 测试和清理不得使用宽泛路径或未解析变量；先校验目标位于本目录内。

