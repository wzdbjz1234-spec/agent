# Phase 07 Completion Report: Durable orchestration and recovery

- Status: `COMPLETED`
- Date: `2026-08-14`
- Plan phase: `Phase 07`
- Commit/revision: `working tree`（基于既有 `5782038` / `e2fed90`，本次加入真实 Provider 复验并修复恢复分支）
- 关联报告: [phase-07-orchestration-20260813.md](phase-07-orchestration-20260813.md)（BLOCKED 基线）；本报告记录解除阻塞后的复验与修复。

## 1. Objective and scope

本阶段实现长任务的领取、执行、等待、取消、有限重试和 Host/Sandbox 故障恢复。
原阶段报告中的 SQLite durable executor、Run/Task service、checkpoint metadata、lease
fencing、取消清理和有限重试实现保持有效；本次补齐原报告因真实 Sandbox 前置 Gate 未通过
而保留的阻塞项，并在真实 Docker OpenSandbox 上复验恢复链路。

本次范围包括：真实 `OpenSandboxProvider` 接入 `LocalDurableExecutor`，WAITING checkpoint
恢复、旧 Sandbox 销毁后的 lease 重建、固定 ProjectSnapshot/digest 校验，以及对应集成测试。
不包含跨主机队列、Prefect、Webhook、分布式 lease 或生产云模型。

## 2. Detailed changes

- `src/dataharness/providers/durable/executor.py`：恢复 Sandbox 前先生成并缓存当前 Run 的
  `SandboxSpec`，校验 checkpoint 中的镜像 digest 与恢复规格一致；将旧 Provider lease 无法
  重连时的 `SandboxLostError`/`SandboxPolicyError` 都收敛为 `REBUILD_SANDBOX`，然后只按原
  Run 的 ProjectSnapshot、Task、Run 和固定 digest 创建新的 Sandbox。这样 Provider 重启或
  旧 Sandbox 已被销毁时不会把“本地没有旧 lease”误判为不可恢复的策略拒绝。
- `tests/integration/test_opensandbox_live.py`：新增真实 durable orchestration 场景，覆盖
  SQLite Run claim、真实 Python Step、checkpoint/WAITING、旧 Sandbox terminate、新 worker
  领取同一 Run、旧 lease 重连失败后的真实 Sandbox 重建和恢复 Step 成功；同时保留既有真实
  create/attest/execute/cancel/parallel/digest/AnalysisRuntime 复验。
- 既有 Phase 07 文件（`orchestration/`、`providers/durable/`、Runtime SQLite migration
  和 Workspace cleanup）本次未重写；其 fake/SQLite Gate 与真实 Provider Gate 组合验收。

## 3. Interface and invariant changes

- `LocalDurableExecutor` 对外接口不变；`_restore_sandbox` 的内部恢复策略增加了对 Provider
  本地 lease 丢失的可恢复分类。
- checkpoint 继续必须匹配 Run 和 ProjectSnapshot；新增显式要求：若 checkpoint 带有
  `sandbox_image_digest`，它必须等于当前 `SandboxSpec.image_digest`，否则 `PolicyDeniedError`
  并 fail closed，不会用其他镜像重建。
- 恢复顺序保持：先尝试同一 Sandbox 的 `connect`；连接失败后销毁旧句柄语义并以同一
  Snapshot/digest `create`；新 lease 认证成功后才调用 handler。
- Run 的 status/phase 分离、终态不可重开、WAITING 必须携带 `wait_reason`、lease owner/epoch
  fencing、重试分类/上限/退避和已发布资源保护均未改变。

## 4. Storage and migration impact

None。没有新增 schema 或 migration。真实复验使用已有 Runtime SQLite schema v3、
`checkpoint_metadata` 和 `run_retry_attempts`；Workspace 只用于当前 Task 的受控目录。
恢复仍使用创建 Run 时固定的 `project_snapshot_id`，不读取 Project 最新版本替换输入。

## 5. Security and privacy impact

- 恢复不会接受 checkpoint 中未经校验的镜像 digest；digest 不一致直接拒绝。
- 旧 Sandbox 无法重连时不会降级到 Host 执行，而是重新通过 `OpenSandboxProvider.create` 的
  attestation；真实测试继续验证非 root、NoNewPrivs、CapEff=0、deny-all egress、挂载和资源限制。
- 代码载荷仍只经 `SandboxProvider` 进入真实 Sandbox；Runtime/Privacy SQLite、凭据和其他
  Task 路径没有进入 mount。
- checkpoint、lease 和 retry 仍只保存稳定 ID、hash、epoch、phase 与错误分类，不保存模型
  消息、secret、PII 或完整执行输出。

## 6. Dependency changes

None。本次未增加或升级 Python 依赖；`opensandbox==0.1.15`、`uv.lock`、secure-analysis
镜像依赖、镜像 digest、SBOM 和 OSV 扫描证据沿用 Phase 05/06 已完成的锁定结果。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv lock --check --offline` | `PASS` | 129 packages resolved，lock 一致 |
| `uv run --offline ruff format --check .` | `PASS` | 183 files already formatted |
| `uv run --offline ruff check .` | `PASS` | All checks passed |
| `uv run --offline pyright` | `PASS` | 0 errors, 0 warnings, 0 informations |
| `uv run --offline python -m dataharness.tooling.dependency_check` | `PASS` | 依赖方向无违规 |
| `uv run --offline pytest tests/integration/test_orchestration.py tests/integration/test_agent_phase08.py -q --tb=short` | `PASS` | 11 passed |
| `uv run --offline pytest tests/unit/test_opensandbox_sdk_client.py tests/integration/test_opensandbox_provider.py -q --tb=short` | `PASS` | 16 passed |
| `uv run --offline pytest -q --tb=short` | `PASS` | 216 passed，7 条 live 测试在未设置 live 开关时显式 skipped |
| `$env:DATAHARNESS_LIVE_SANDBOX='1'; uv run --offline pytest tests/integration/test_opensandbox_live.py::test_live_create_attest_execute_terminate -vv -s` | `PASS` | 1 passed；真实 create/attest/execute/terminate/rebuild |
| `$env:DATAHARNESS_LIVE_SANDBOX='1'; uv run --offline pytest tests/integration/test_opensandbox_live.py::test_live_sql_runner_reads_project_tables -vv -s` | `PASS` | 1 passed；真实 DuckDB SQL runner/schema/statistics |
| `$env:DATAHARNESS_LIVE_SANDBOX='1'; uv run --offline pytest tests/integration/test_opensandbox_live.py::test_live_cancel_interrupts_running_step -vv -s` | `PASS` | 1 passed；真实 cancel 后 Sandbox 可继续使用 |
| `$env:DATAHARNESS_LIVE_SANDBOX='1'; uv run --offline pytest tests/integration/test_opensandbox_live.py::test_live_parallel_runs_are_isolated -vv -s` | `PASS` | 1 passed；并行 lease 隔离 |
| `$env:DATAHARNESS_LIVE_SANDBOX='1'; uv run --offline pytest tests/integration/test_opensandbox_live.py::test_live_attestation_fails_closed_on_wrong_digest -q --tb=short` | `PASS` | 1 passed；错误 digest 被 Docker/OpenSandbox 拒绝 |
| `$env:DATAHARNESS_LIVE_SANDBOX='1'; uv run --offline pytest tests/integration/test_opensandbox_live.py::test_live_analysis_runtime_runs_python_and_sql -vv -s` | `PASS` | 1 passed；真实 AnalysisRuntime Python/SQL 与正式发布 |
| `$env:DATAHARNESS_LIVE_SANDBOX='1'; uv run --offline pytest tests/integration/test_opensandbox_live.py::test_live_durable_executor_rebuilds_checkpointed_sandbox -q --tb=short` | `PASS` | 1 passed；同一 Run/Snapshot 的 checkpoint 恢复与真实 Sandbox rebuild |

## 8. Exit Gate evidence

1. **Host 重启后恢复同一非终态 Run，已提交 Step 不重复执行。** 既有
   `test_host_crash_recovers_same_run_and_does_not_reopen_terminal_run` 通过；新增真实测试
   将 Run 写入 checkpoint/WAITING 后由第二个 worker 领取同一 Run，断言 Run/Snapshot 不变，
   并验证旧 Sandbox 被终止后新 Sandbox 完成恢复 Step。
2. **恢复 Run 使用原 ProjectSnapshot。** 新增真实测试断言两次 handler context 都引用
   `live-snapshot`；恢复规格由原 Run 生成，且 checkpoint digest 与规格一致。
3. **终态 Run 不重新打开；用户重试创建新 Run。** 既有 orchestration 集成测试断言成功后
   再次 `run_once()` 无任务、终态没有出边；RunService 的重试入口仍创建新 Run。
4. **取消多次调用结果一致，已发布资源保留，未发布 staging 不可见。** 既有
   `test_cancel_is_idempotent_and_cleans_only_unpublished_staging` 通过；真实 cancel 测试
   验证运行中 Step 被中断并清理后同一 Sandbox 可继续执行。
5. **lease fencing 可阻止旧 Worker 或旧 Sandbox 提交。** 既有
   `test_old_worker_epoch_cannot_commit_after_reclaim` 通过，旧 epoch CAS 提交抛出
   `LeaseLostError`；真实恢复测试使用 worker-a/worker-b epoch 交接。
6. **所有自动重试有分类、次数上限和退避，无无限循环。** 既有重试集成测试验证两次重试
   后第三次终态失败，并检查 `run_retry_attempts` 的分类和次数；真实 Sandbox 丢失后的
   rebuild 路径已不再错误终止。

## 9. Architecture deviations and decisions

None。修复保持 `api -> orchestration -> agent/capabilities/analysis/projects -> domain +
boundary protocols -> providers/storage` 依赖方向，没有引入第二套队列、状态机或 Host 执行
路径。将 Provider lease 本地缺失视为可重建的 Sandbox loss，是既有恢复决策的实现补全。

## 10. Known issues and technical debt

- LocalDurableExecutor 仍是单机 SQLite 实现；跨主机 worker、外部队列和 Webhook 明确留给
  V1 之后，负责人为后续平台化阶段。
- 镜像 OSV 扫描仍有 Phase 05 addendum 记录的未修复上游漏洞，当前接受风险并持续监测，
  Phase 10 重新升级/扫描。
- OpenSandbox 服务必须保持 Docker runtime、`dns+nft`、路径白名单和无认证本地验收配置；
  生产环境需配置 API key。配置不满足时 Provider 继续 fail closed。
- 真实测试产生的本地服务日志位于 `runtime-data/`，不作为仓库事实或阶段证据文件提交。

## 11. Next-phase entry check

Phase 08 入口满足：durable `RunExecutionContext`/`RunOutcome`、固定 Snapshot、checkpoint
metadata、Sandbox lease rebuild、Workspace staging、lease fencing 和 fake recovery fixtures
均可使用；Phase 04/06 的 ModelGateway/AnalysisRuntime 安全边界也已满足。Phase 08 可按计划
完成 Agent、Skill、checkpoint/compaction 和可选历史检索验收。
