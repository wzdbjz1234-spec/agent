# Phase 02 Completion Report: Runtime storage and durable primitives

- Status: `COMPLETED`
- Date: `2026-08-13`
- Plan phase: `Phase 02`
- Commit/revision: working tree implementation on the current repository revision

## 1. Objective and scope

本阶段建立 Runtime SQLite 事实源与 orchestration 后续可直接使用的原子耐久原语。实际完成范围包括：

- 有序、单向、事务化 migration runner、schema version、WAL、外键与完整性约束；
- Project/File/FileVersion/Snapshot/Coverage、Session、Task/Run/Step、Dataset/Artifact、
  Finding/Lineage、event、checkpoint metadata 与幂等记录 Repository；
- 显式 `UnitOfWork`、基于 `row_version + expected status` 的 CAS、状态与事件同事务；
- 原子 Run queue claim、lease owner/epoch、heartbeat、过期回收与旧 epoch fencing；
- Runtime DB 与 per-Task Privacy DB 的路径及连接工厂物理隔离；
- migration、回滚、并发 claim、终态保护、不可变 Snapshot/FileVersion 与安全负向测试。

不在本阶段范围内：Privacy DB 业务 schema（Phase 04）、Workspace 文件发布与 reconciler
（Phase 03）、LocalDurableExecutor worker loop 与 checkpoint 内容实现（Phase 07）。

## 2. Detailed changes

- `src/dataharness/storage/__init__.py`：导出 storage 稳定入口、值对象和错误，不向调用方返回
  SQLite row/cursor。
- `src/dataharness/storage/database.py`：新增 `RuntimeConnectionFactory` 与
  `PrivacyConnectionFactory`；每连接启用 foreign keys、busy timeout、FULL synchronous，文件库启用 WAL。
- `src/dataharness/storage/errors.py`：新增稳定的 not-found、CAS conflict、lease lost、幂等冲突、
  migration 与不安全元数据错误层级。
- `src/dataharness/storage/migrate.py`：发现连续编号 SQL、核对已应用版本、逐版本事务执行并在失败时回滚。
- `src/dataharness/storage/migrations/0001_runtime.sql`：新增 Runtime V1 schema、索引、外键、
  CHECK/UNIQUE 约束与不可变 trigger。
- `src/dataharness/storage/migrations/__init__.py`：将 migration 目录作为可随 wheel 分发的资源包。
- `src/dataharness/storage/records.py`：新增 `StoredRecord`、`RunLease`、`ClaimedRun`、
  `EventRecord`、`CheckpointMetadata` 与 `IdempotencyRecord` 冻结值对象。
- `src/dataharness/storage/repository.py`：实现全部 Phase 02 元数据的写入/重建、CAS、事件、
  checkpoint 与幂等接口；事件 payload 限制为小型元数据并拒绝明显 secret/PII/模型载荷字段。
- `src/dataharness/storage/uow.py`：新增显式提交/异常回滚事务边界，支持 `BEGIN IMMEDIATE`。
- `src/dataharness/storage/store.py`：新增生产 `SqliteRuntimeStore`、原子 queue claim、heartbeat 与
  expired lease recovery；使用递增 epoch 作为 fencing token。
- `tests/contract/test_storage_contract.py`：验证领域元数据 round-trip、CAS、事务事件、终态保护、
  幂等冲突和事件安全边界。
- `tests/integration/test_storage_sqlite.py`：验证空库/逐版本/失败迁移、WAL/外键、双 Worker claim、
  heartbeat、lease 回收、旧 epoch 拒绝、数据库不可变 trigger 与 Runtime/Privacy 物理隔离。

## 3. Interface and invariant changes

新增 Interface：

- `SqliteRuntimeStore.unit_of_work()`：创建一次性事务边界；Repository 操作和事件只一起提交或回滚。
- `RuntimeRepository`：接收/返回领域对象；带状态对象由 `StoredRecord.version` 暴露 CAS token。
- `SqliteRuntimeStore.claim_next_run(owner, now, lease_duration)`：原子领取 QUEUED Run 或回收过期
  RUNNING lease，返回领域 `Run` 与 `RunLease`，不返回 SQL row。
- `SqliteRuntimeStore.heartbeat(lease, now, lease_duration)`：只有当前、未过期 owner/epoch 可续租。
- `RuntimeRepository.save_run(..., lease, lease_checked_at)`：带租约提交必须同时匹配 owner、epoch、
  未过期条件和 CAS 版本。
- `reserve_idempotency`/`complete_idempotency`：相同 key/hash 可重放，不同请求或结果稳定冲突。

新增/下沉的不变量：

- ProjectSnapshot、SnapshotEntry 与 Snapshot Dataset 关联只能追加，数据库 trigger 禁止更新/删除。
- ProjectFileVersion 只能由 IMPORTING 定稿一次，定稿后 trigger 禁止更新。
- Run 的 task/project/project_snapshot_id 创建后不可改写；恢复固定原 Snapshot。
- Task/Run/Step/Finding 状态写入同时经过领域迁移表防御、预期状态和 `row_version` CAS。
- CoverageItem 必须属于报告所绑定 Snapshot；Project/Task/Run/资源归属由复合外键约束。
- 事件只保存有界、脱敏元数据；checkpoint 表只保存引用/hash/sequence，不保存模型消息或载荷。

性能/顺序特征：SQLite 维持单 writer；普通 UnitOfWork 使用 `BEGIN`，queue claim、heartbeat 使用
`BEGIN IMMEDIATE`，候选读取与 lease 更新处于同一 writer reservation 中。领取顺序为 QUEUED 优先，
再按创建时间与 ID；过期 RUNNING lease 次之。

## 4. Storage and migration impact

- 当前 Runtime schema version 为 `1`，应用记录位于 `schema_migrations`。
- 新增 21 个业务/迁移表（SQLite 内部表不计）：projects、project_files、
  project_file_versions、project_snapshots、snapshot_entries、snapshot_datasets、sessions、tasks、runs、
  analysis_steps、datasets、artifacts、coverage_reports、coverage_items、findings、finding_evidence、lineage、
  events、checkpoint_metadata、idempotency_keys、schema_migrations。
- migration 文件一经本报告验收后保持不可变；Phase 03 及以后只能追加 `0002_*.sql` 等新版本。
- 逐版本升级保留既有数据；失败版本的 DDL/DML 和 version 记录在同一事务回滚。
- Workspace 布局无变化。Runtime DB 只保存元数据；文件内容继续由后续 Workspace 模块负责。
- Privacy DB 仅建立独立路径/连接工厂，未与 Runtime migration 或 schema 合并。

## 5. Security and privacy impact

- Runtime 与 Privacy 路径分别解析并验证，禁止 Privacy 根包含 Runtime DB；每个 Task 使用独立 DB 文件。
- Runtime schema 无 BLOB、secret、password、api_key、PII mapping 或 model payload 字段；集成测试对 schema
  做负向断言。
- event payload 最大 16 KiB，并拒绝名称明显表示 secret/token/password/api_key/PII/prompt/response/
  raw payload 的字段；完整 Secret/PII 检测仍属于 Phase 04。
- Runtime DB、Privacy DB 路径未进入领域对象、Workspace 或 Sandbox Interface。
- migration SQL 使用随包固定资源；Repository SQL 值均参数化。唯一动态表名来自内部封闭集合。
- 测试只使用临时目录、固定时间、合成 ID/hash，不访问公网、真实账号、真实 secret 或生产数据。

## 6. Dependency changes

None。实现仅使用 Python 3.12 标准库 `sqlite3`、已有 Pydantic 与现有开发工具；
`pyproject.toml`/`uv.lock` 未新增或升级依赖，因此无新增 License 或漏洞扫描项。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv lock --check` | PASS | `Resolved 20 packages`，锁文件一致 |
| `uv run ruff format --check .` | PASS | `94 files already formatted` |
| `uv run ruff check .` | PASS | `All checks passed!` |
| `uv run pyright` | PASS | `0 errors, 0 warnings, 0 informations` |
| `uv run pytest -q` | PASS | `132 passed in 2.10s` |
| `uv run pytest -q --cov=dataharness --cov-report=term-missing` | PASS | `132 passed`，总覆盖率 `93%`，storage repository `80%` |
| `uv run python scripts/verify.py` | PASS | lock/format/lint/type/pytest 全通过，`132 passed in 2.00s` |
| `uv build --out-dir C:\projects\research\.build-check` | PASS | sdist 与 wheel 构建成功；wheel 包含 `dataharness/storage/migrations/0001_runtime.sql`；临时产物已清理 |
| `git diff --check` | PASS | 无空白错误 |

## 8. Exit Gate evidence

- **空库、逐版本升级和失败回滚测试通过。**
  `test_empty_database_upgrade_replay_and_wal` 从空文件升级至 v1 并重放；
  `test_progressive_custom_migration_and_failed_version_roll_back` 以 v1→v2→失败 v3 证明既有数据保留、
  半迁移表与 v3 version 均回滚。
- **两个 Worker 不能同时持有同一有效 lease；旧 epoch 不能提交状态。**
  `test_two_workers_cannot_claim_same_effective_lease` 使用两个真实线程/独立 SQLite 连接并发 claim，只有
  一个 winner；`test_expired_lease_is_reclaimed_and_old_epoch_cannot_commit` 证明回收产生 epoch+1 且旧
  owner/epoch 被 `LeaseLostError` 拒绝；heartbeat 过期边界亦有负向测试。
- **状态与对应事件在同一事务成功或失败。**
  `test_cas_transition_and_event_commit_or_rollback_together` 在状态与事件写入后注入异常，重开连接验证两者
  均未提交；正常路径两者一起出现。
- **Runtime DB 中不存在大文件、原始模型载荷、secret 或 PII 映射字段。**
  schema 无 BLOB 和上述敏感列；事件接口限制字段及 16 KiB 大小；checkpoint 只保存 ref/hash/sequence；
  `test_runtime_schema_has_no_blob_secret_or_pii_mapping_columns` 与
  `test_event_interface_rejects_raw_model_or_oversized_payload` 提供负向证据。
- **Snapshot 与文件版本关联不可修改；Run 的 project_snapshot_id 受 fencing/CAS 保护且恢复时一致。**
  schema trigger 阻止 Snapshot/SnapshotEntry/Snapshot Dataset 与定稿 FileVersion 改写；Run identity trigger、
  Repository 身份检查、CAS row_version 和 lease epoch 共同保护 `project_snapshot_id`；回收同一 Run 时
  `test_expired_lease_is_reclaimed_and_old_epoch_cannot_commit` 验证仍返回原 Run/Snapshot。

Cross-phase Gate 证据：依赖锁、format/lint/type、全量测试均通过；新增 SQLite production Adapter 使用真实
临时文件数据库完成 contract + integration 测试。此 seam 没有第二种生产实现，测试直接复用同一
Repository Interface 与真实 SQLite，而不是维护行为可能漂移的内存 fake；这是标准库本地基础设施
Adapter 的有意选择。

## 9. Architecture deviations and decisions

None。实现遵循 `ARCHITECTURE.md`：Runtime SQLite 是领域元数据与本地队列事实源，Privacy DB 物理分离，
不引入 ORM、Prefect、在线数据库或新第三方依赖。`LocalDurableExecutor` 仍按计划留给 Phase 07；本阶段只
提供其所需原子存储原语。

## 10. Known issues and technical debt

- Privacy DB 尚无 placeholder schema；负责人/阶段：Phase 04 privacy。当前连接工厂只保证物理隔离。
- checkpoint 只保存 metadata，PydanticAI checkpoint 内容与恢复决策未接线；负责人/阶段：Phase 07/08。
- SQLite 单 writer 适合 V1 单机单租户；若未来变为多机/多租户，需要新的 durable Provider 和契约验证，
  不能通过放宽当前 fencing 语义实现。
- migration v1 已作为验收基线，后续修订必须新建 migration，禁止原地编辑该 SQL。

## 11. Next-phase entry check

Phase 03 前置条件已满足：Project/File/FileVersion/Snapshot/Coverage Repository、不可变关联、事务、稳定 ID/
hash 元数据与幂等键已经可用；Workspace/ProjectCorpus 可在此基础上实现导入、索引、发布意图和 reconciler。
Phase 03 必须携带以下约束：

- 只追加 `0002_*.sql` migration，不修改 `0001_runtime.sql`；
- 文件本体进入 Workspace，Runtime DB 只写 hash、状态、版本和资源引用；
- 发布幂等键使用架构规定的 `run_id + step_id + output_name`；
- Snapshot 只引用既有不可变 FileVersion/Dataset，任何后续上传不得改变已有 Run 数据视图；
- 复用本阶段的临时 Runtime factory、并发 lease fixtures 与失败回滚模式。

