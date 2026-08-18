# Storage

- Runtime SQLite 是 Project/FileVersion/Snapshot/Coverage、Task/Run/Step、Dataset/Artifact/Finding/Lineage 元数据、事件、lease、幂等键和本地队列的事实源。
- 使用事务、外键、唯一约束、WAL 与显式 schema 版本；Repository 返回领域对象，不泄漏 ORM/SQL 行。
- 不在 Runtime DB 保存大文件、原始模型请求/工具载荷、凭据原值或 PII 映射；用户显式选择保留时，`conversation_messages` 只保存有界的可见 user/assistant 文本。
- 状态迁移采用 compare-and-set/版本字段，终态不可回退；事件与状态在同一事务提交。
- 文件发布记录与 Workspace 通过 reconciler 对账，不假设数据库和文件系统具有跨介质事务。
