# Storage Migrations

- 迁移按版本单向追加、可重复检测，不修改已经发布的迁移文件。
- 每个迁移在事务中更新 schema version；失败不得留下半迁移状态。
- 约束优先在数据库表达：外键、唯一幂等键、合法状态、版本/lease epoch。
- ProjectFileVersion 与 ProjectSnapshot 关联采用追加式 schema；已被 Snapshot 引用的版本不可被原地更新或级联删除。
- 迁移测试覆盖空库升级、已有数据升级、失败回滚及当前 schema 重放。
- Privacy SQLite 使用独立 schema 与迁移序列，禁止与 Runtime SQLite 合库。
