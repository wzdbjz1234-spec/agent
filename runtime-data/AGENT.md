# Runtime Data

本目录是本地运行数据根目录，不提交运行时内容。

- `runtime.db`：Task/Run/Step、领域元数据、事件、lease、幂等键与本地队列。
- `projects/{project_id}/`：版本化 sources、extracted、indexes、datasets、artifacts、manifests 及 `tasks/{task_id}`。
- Project 原始文件版本只读且不可变；Task 仅拥有 working、当前 Step staging 和 state。
- `privacy/{task_id}.db`：Task 级占位映射；不得进入 Workspace、Sandbox、日志或 Runtime SQLite。
- 正式输出只能由 Host 从 Task staging 发布到 Project datasets/artifacts；Snapshot 引用的文件版本不得原地删除。
- 测试和清理不得使用宽泛路径或未解析变量；先校验目标位于本目录内。
