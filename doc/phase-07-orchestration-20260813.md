# Phase 07 Completion Report: Durable orchestration and recovery

- Status: `BLOCKED`
- Date: `2026-08-13`
- Plan phase: `Phase 07`
- Commit/revision: `5782038 feat(orchestration): add durable execution and recovery`; follow-up `e2fed90 test(orchestration): cover crash recovery and cancellation`

## 1. Objective and scope

本阶段实现长任务的领取、执行、等待、取消、有限重试和 Host/Sandbox 故障恢复。
本阶段代码、SQLite 持久化边界和 fake Provider 集成验证已完成；但 Phase 06 报告明确记录的
真实 OpenSandbox/镜像 digest/SBOM Gate 仍未通过。根据全局阶段依赖规则，不能把依赖未通过的
Phase 07 计划状态标为 `COMPLETED`，因此本报告状态为 `BLOCKED`，不是对已完成代码的隐瞒。

本阶段不实现分布式队列、Prefect、第二套工作流事实源或新的 Agent loop；正式事实仍来自
Runtime SQLite、Workspace 和 checkpoint metadata。

## 2. Detailed changes

- `src/dataharness/orchestration/services.py`：新增 `TaskService` 和 `RunService`。Task 创建时
  建立隔离 Task Workspace；Run 创建时显式固定 `project_snapshot_id`；提供 WAITING/resume、
  幂等取消和终态 Task 收口。
- `src/dataharness/orchestration/models.py` / `errors.py` / `protocols.py`：增加结构化
  `RunOutcome`、`RecoveryDecision`、`FailureClass`、`RunExecutionContext` 和 handler 协议；
  将预算、资源、Sandbox 丢失、Host crash、策略拒绝和模型可修正错误映射为稳定策略输入。
- `src/dataharness/providers/durable/executor.py`：实现 `LocalDurableExecutor` 与可停止
  worker loop。每个 claim 使用 SQLite 原子 lease/epoch；恢复时读取最新 checkpoint，重连
  同一 Sandbox 或用同一 Run/Snapshot 规格重建；成功、等待、失败、取消均在有效 fencing token
  下提交。
- `src/dataharness/storage/migrations/0003_orchestration.sql`：新增 `next_attempt_at`、
  checkpoint 的 Snapshot/Sandbox/phase 关联字段和 `run_retry_attempts` 表。
- `src/dataharness/storage/store.py` / `repository.py` / `records.py`：增加可恢复领取、
  取消请求、WAITING resume、重试调度、重试次数、checkpoint 幂等和租约释放；终态/WAITING
  Run 释放 lease，旧 epoch 无法提交。
- `src/dataharness/domain/run.py`：增加耐久 `cancel_requested_at`，把取消意图和外部副作用
  清理后的终态收口分开。
- `src/dataharness/workspace/protocols.py` / `providers/workspace/local.py`：增加只清理当前
  Task 未发布 staging 的能力，已发布 datasets/artifacts 不受影响。
- `tests/integration/test_orchestration.py`：覆盖成功幂等、checkpoint、Host crash 重启、
  WAITING/resume、Sandbox loss 重建、有限重试、取消幂等、staging 清理和旧 worker fencing。
- `tests/integration/test_storage_sqlite.py`：同步 Runtime schema 从 v2 到 v3 的迁移验收。

## 3. Interface and invariant changes

- `Run.status` 与 `Run.phase` 继续分离；WAITING 必须携带 `wait_reason`，PREPARING →
  REASONING → EXECUTING → VERIFYING → FINALIZING 只能前进。
- `Run.cancel_requested_at` 是持久取消意图。RUNNING Run 不由请求线程直接结束，持有有效
  lease 的 worker 负责停止/销毁 Sandbox、清理未发布 staging 后再提交 CANCELLED。
- `LocalDurableExecutor` 的 handler 只能接收 `RunExecutionContext` 并返回结构化
  `RunOutcome`；上下文固定原 Run 的 ProjectSnapshot，不读取 Project 最新版本替换输入。
- 恢复决策包括 `START_FROM_BEGINNING`、`RESUME_FROM_CHECKPOINT`、`REBUILD_SANDBOX`、
  `CANCEL` 和 `TERMINAL`。checkpoint 必须匹配 Run、Snapshot、Sandbox ID 和镜像 digest。
- 自动重试只对模型可修正、资源限制和 Sandbox 丢失等可重试类别开放；每次写入分类、attempt、
  delay 和 next-attempt 时间，达到 `max_retries` 后终态失败，不会无限循环。
- 状态提交、心跳、重试调度和取消收口均使用 owner/epoch/未过期条件；旧 Worker 或旧
  Sandbox 不能越过 fencing 边界提交结果。
- 同一规范请求的已完成输出继续由 Phase 06 AnalysisRuntime 的幂等键和 Workspace 发布协议
  负责，Executor 不重复调用已终态 Run；已发布资源保留，未发布 staging 可清理。

## 4. Storage and migration impact

- Runtime schema 从 2 升级到 3。`runs.next_attempt_at` 支持重试时间；checkpoint metadata
  增加 `project_snapshot_id`、`sandbox_id`、`sandbox_image_digest`、`run_lease_epoch` 和
  `phase`；新增 `run_retry_attempts` 追加式审计表及索引。
- 所有新增字段属于 Runtime SQLite；Privacy SQLite、Workspace 和 Sandbox 不承载 Runtime
  状态或凭据。既有 Project/FileVersion/Snapshot 及正式资源表未改变。
- Workspace 取消清理只处理 `tasks/<task>/staging`；正式 datasets/artifacts 已经由发布协议
  移出 staging 后不会被删除。
- Migration 可从空库重放，也可从现有 schema v2 升级到 v3；本阶段未提供降级迁移，回滚应使用
  数据库备份和代码版本回退，不删除已写入的审计事件或正式资源。

## 5. Security and privacy impact

- 编排层不执行 Python/SQL，不提供 Host shell、安装、网络或外部 API；不可信载荷仍只能经
  `SandboxProvider` 进入 Sandbox。
- Sandbox 恢复只接受 checkpoint 中的稳定 ID，并再次校验 Run、Task、Project、Snapshot 和
  镜像 digest；上下文不匹配时 fail closed。
- 取消清理不遍历其他 Project/Task 的目录，也不触碰正式发布资源；Workspace 通过受控组件和
  解析后的根目录约束路径。
- checkpoint、retry 和事件只保存 ID、hash、epoch、phase、错误分类等元数据，不保存模型消息、
  prompt、secret、PII 或完整执行输出。
- 真实 OpenSandbox attestation、固定镜像 digest、SBOM 和漏洞扫描仍由 Phase 05/06 的阻塞
  项负责；本阶段 fake Sandbox 测试不能替代生产隔离证明。

## 6. Dependency changes

None. 未新增或升级 Python 依赖，`pyproject.toml` 和 `uv.lock` 未改变；实现继续使用现有
Python `sqlite3`、Workspace/Sandbox protocol 和既有依赖。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv lock --check --offline` | `PASS` | Resolved 30 packages，lock 一致 |
| `uv run --offline ruff format --check .` | `PASS` | 156 files already formatted |
| `uv run --offline ruff check .` | `PASS` | All checks passed |
| `uv run --offline pyright` | `PASS` | 0 errors, 0 warnings, 0 informations |
| `uv run --offline pytest -q` | `PASS` | 197 passed |
| `uv run --offline pytest --cov=dataharness --cov-report=term-missing -q` | `PASS` | 197 passed，总覆盖率 90% |
| `uv run --offline python -m dataharness.tooling.dependency_check` | `PASS` | 依赖方向检查无违规 |
| `uv run --offline python scripts/verify.py` | `PASS` | lock、format、ruff、pyright、pytest 全部通过 |
| `uv run --offline pytest tests/integration/test_orchestration.py -q` | `PASS` | 6 passed；成功/恢复/重试/取消/fencing |

## 8. Exit Gate evidence

1. **Host 重启恢复同一非终态 Run，已提交 Step 不重复执行**：集成测试先将 Run 写入
   checkpoint/WAITING，再使用新的 executor/worker 领取同一 Run；测试断言 Run ID、Snapshot
   不变，并通过 AnalysisRuntime 既有幂等协议避免重复正式输出。生产 OpenSandbox 恢复仍受前置
   Gate 阻塞。
2. **恢复使用原 ProjectSnapshot**：`test_waiting_resume_and_checkpointed_sandbox_loss_rebuild`
   与 `test_host_crash_recovers_same_run_and_does_not_reopen_terminal_run` 断言恢复上下文仍为
   原 Snapshot；RunService 不接受隐式最新版本替换。
3. **终态 Run 不重新打开**：成功后再次 `run_once()` 返回空；取消多次返回同一结果；领域状态机
   没有终态出边。
4. **取消幂等且保留已发布资源、清理未发布 staging**：取消请求只首次写入意图；queued Run
   立即终态，running Run 由 worker 收口；测试验证未发布 staging 被清理，正式发布协议仍保留
   AVAILABLE 资源。真实 Sandbox 进程清理仍需前置 Provider Gate。
5. **lease fencing 阻止旧 Worker/Sandbox 提交**：集成测试让 worker-a lease 过期并由 worker-b
   取得更高 epoch，旧 lease 的 CAS 提交抛出 `LeaseLostError`；checkpoint 恢复再次校验 Sandbox
   上下文和 digest。
6. **所有自动重试有分类、次数上限和退避**：`run_retry_attempts` 记录分类、attempt、delay
   和 next-attempt；测试以两次重试后第三次终态失败证明无无限循环，Host crash 也能由新 worker
   继续同一 Run。

本阶段本身的 fake/SQLite Exit Gate 证据齐全，但由于 Phase 05/06 的真实 Sandbox 前置 Gate
仍未通过，不能依据项目全局规则把本阶段报告或计划状态写为 `COMPLETED`。

## 9. Architecture deviations and decisions

- None。实现沿用 `api -> orchestration -> agent/capabilities/analysis/projects -> domain +
  boundary protocols -> providers/storage` 方向，没有引入 Prefect 或第二套状态机。
- `Run.cancel_requested_at` 和 `run_retry_attempts` 是 Phase 07 为既有 Runtime SQLite 增加的
  控制面事实；它们不复制 Project/Workspace/Privacy 的业务事实。
- checkpoint 只保存定位元数据，不保存 PydanticAI 原始消息载荷；模型 checkpoint 内容的实际
  持久化仍由后续 Agent 装配阶段负责。

## 10. Known issues and technical debt

- **Phase 05/06 真实 Sandbox Gate**：负责人/后续阶段为恢复 OpenSandbox 服务与 Docker/等价
  隔离环境，锁定 `secure-analysis` 镜像 digest，补齐 pandas/Pandera runner、SBOM、漏洞扫描
  和真实 create/connect/execute/cancel/terminate 测试；未完成前 Phase 07 保持 `BLOCKED`。
- **取消中的实时进程句柄**：当前 Executor 依赖 handler/checkpoint 提供可重连 Sandbox，取消时
  通过 Provider terminate；更细粒度的当前 Step cancel 句柄由 Agent/AnalysisRuntime 装配阶段
  接入，不能用 Host shell 替代。
- **SQLite 单机边界**：本阶段只实现 LocalDurableExecutor；跨主机 worker、外部队列、租户隔离和
  Webhook 不属于 V1。

## 11. Next-phase entry check

Phase 08 可复用的入口已准备：`RunExecutionContext`、`RunOutcome`、checkpoint metadata、
固定 Snapshot、Sandbox lease 关联、Workspace staging 和 fake recovery fixtures 均已写入仓库。
但在 Phase 05/06 真实 Sandbox Gate 通过、Phase 06 报告可改为 `COMPLETED`、本报告解除
`BLOCKED` 前，不应宣称 Phase 07/08 的正式阶段验收完成。
