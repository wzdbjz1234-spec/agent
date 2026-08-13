# Analysis Runtime

- 将原生工具调用转换为 AnalysisStep、Sandbox 执行、输出校验、发布与 lineage。
- 每个 Step 使用独立进程和独立 `staging/{step_id}`；禁止依赖 REPL 或跨步内存。
- Python/SQL 只能在 OpenSandbox 执行；Host 不导入或运行生成代码。
- Sandbox 内 Task 自有 DuckDB/SQLite 可写；原始 `inputs` 和 Host 数据库不可写、不可见。
- FindingCandidate 先进入 DRAFT，再由 Host 的 Execution/Integrity/Evidence Gate 判定。

