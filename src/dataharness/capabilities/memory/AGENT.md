# Memory Capability

- V1 不使用向量数据库保存 Agent Memory。
- 工作状态来自 Workspace state 文件；业务状态来自 Runtime SQLite；对话来自 PydanticAI checkpoint。
- 历史检索按需使用 SQLite FTS5/BM25，并返回来源与有界片段。
- Compaction 摘要不是事实源，必须保留 Dataset/Artifact/Finding 的稳定引用。
- 面向文档的语义检索属于未来 `SemanticIndexProvider`，不得混入 MemoryCapability。

