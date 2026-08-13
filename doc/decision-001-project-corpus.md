# Decision 001: Project Corpus and Run-scoped Sandbox

- Status: Accepted
- Date: 2026-08-13
- Affects plan: 1.1

## Context

DataHarness 需要让用户将多份文件长期归入同一项目，并让 Agent 在不同 Task 中检索、整合和分析这些文件。原有 Task-scoped Workspace 无法稳定表达文件版本、跨任务复用、全项目覆盖或旧 Run 的输入复现。

## Decision

- 新增 Project、ProjectFile、ProjectFileVersion、ProjectSnapshot 和 ProjectCoverageReport。
- 新增 ProjectCorpus deep module，集中实现导入、提取、索引、Snapshot、检索和 Coverage。
- 文件更新创建不可变新版本；Run 开始时固定 ProjectSnapshot。
- 跨文件检索使用 RELEVANT 与 FULL_PROJECT 两种显式模式。
- V1 使用本地元数据 + SQLite FTS5/BM25；不因该能力引入向量数据库。
- Sandbox 保持 Run-scoped，每个 Step 使用独立进程；ProjectSnapshot 只读，Task working/staging 可写。

## Consequences

- Runtime SQLite、Workspace 布局、API、lineage、恢复和测试必须包含 project_id、file_version_id 与 snapshot_id。
- 同一 Project 可并行运行多个隔离 Task；取消一个 Task 不影响其他 Task。
- FULL_PROJECT 只有在 CoverageReport 完整时才能声称覆盖全部受支持文件；失败、不支持和跳过项必须披露。
- 增加本地文档提取依赖与索引构建成本，但避免把完整项目一次性发送给模型。

## Rejected alternatives

- Project-scoped 长期 Sandbox：会积累隐藏状态、妨碍并发和可复现恢复。
- 把文件直接复制到每个 Task：重复存储，失去统一版本和跨任务复用。
- 将 Project 检索归入 Agent Memory：混淆业务语料与对话/工作记忆的事实来源。
- V1 默认采用向量数据库：不是实现跨文件可追溯检索的必要条件。
