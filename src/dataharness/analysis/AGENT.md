# Analysis Runtime

- 将原生工具调用转换为 AnalysisStep、Sandbox 执行、输出校验、发布与 lineage。
- 每个 Step 使用独立进程和独立 `staging/{step_id}`；禁止依赖 REPL 或跨步内存。
- Python/SQL 只能在 OpenSandbox 执行；Host 不导入或运行生成代码。
- Sandbox 内 Task 自有临时 DuckDB/SQLite 可写；ProjectSnapshot 的原始文件和数据库快照只读，Host 数据库不可见。
- RELEVANT 分析记录实际使用的 ProjectFileVersion；FULL_PROJECT 分析必须生成 CoverageReport 并披露缺口。
- FindingCandidate 先进入 DRAFT，再由 Host 的 Execution/Integrity/Evidence Gate 判定。
