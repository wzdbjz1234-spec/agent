# Phase 09 Completion Report: Verification, HTTP and observability

- Status: `COMPLETED`
- Date: `2026-08-14`
- Plan phase: `Phase 09`
- Commit/revision: `checkpoint commit（本报告与实现同一次提交）`

## 1. Objective and scope

本阶段完成 Host 侧 Finding Verification Gate、本地 FastAPI 控制面和隐私安全的
OpenTelemetry Adapter。范围覆盖 Project/文件/检索/Task 生命周期/事件/正式资源/受控
文件读取，以及 FULL_PROJECT Finding 的 CoverageReport 事实引用。

明确不包含公网认证、TLS、RBAC、多租户、Webhook；SSE 仍为可选能力，本阶段使用已有
Runtime 事件序列作为唯一事件源。

## 2. Detailed changes

- `src/dataharness/analysis/verification.py`：新增 ExecutionGate、IntegrityGate、
  EvidenceGate 和 `VerificationService`。Agent 产生的 Finding 保持 DRAFT，只有 Host
  服务按当前 Run/Snapshot/Workspace 事实 CAS 晋级为 VERIFIED、WARNING 或 REJECTED。
- `src/dataharness/analysis/warnings.py`：新增行数异常、Join 膨胀、缺失值、类型转换和
  重复值的结构化轻量告警检测；告警只使用数值统计，不读取原始输出。
- `src/dataharness/analysis/runtime.py`、`src/dataharness/domain/finding.py`：Finding
  草稿支持可选 CoverageReport 引用，新增 API 不改变原有 DRAFT 提交流程。
- `src/dataharness/api/`：新增薄 FastAPI 工厂、DTO、统一错误映射和应用服务。路由不直连
  SQLite、Workspace 路径、OpenSandbox 或模型 SDK；文件 body 经短生命周期临时文件进入
  既有 ProjectCorpus 校验链。
- `src/dataharness/providers/observability/`：新增 OpenTelemetry Adapter，关联
  task/run/step/tool_call/sandbox ID，仅记录安全元数据；文本属性必须经过 ModelGateway
  TRACE 脱敏，后端故障降级，隐私故障闭锁。
- `src/dataharness/storage/repository.py`、`0004_finding_coverage.sql`：新增 schema 4，
  持久化 FULL_PROJECT CoverageReport 引用，并补充 Project/Artifact/Run 的受控查询。
- `src/dataharness/cli.py`、`pyproject.toml`、`uv.lock`：新增本地 `serve` 命令和 FastAPI、
  Uvicorn、OpenTelemetry API 直接依赖；`serve` 拒绝非回环监听地址。
- `tests/unit/test_phase09.py`、`tests/integration/test_phase09_api.py`：新增 Gate、数据
  Warning、观测隐私和本地 API 组合测试；存储迁移回归断言更新为 schema 4。

## 3. Interface and invariant changes

- `FindingCandidate.coverage_report_id` 是可选稳定 ID；FULL_PROJECT 草稿可以绑定 CoverageReport，
  验证时必须属于同一 ProjectSnapshot。覆盖缺口会进入 Finding 事件序列，供回答层披露，
  不会被自然语言“分析完成”覆盖。
- `VerificationService.verify()` 是唯一应用级终态收口入口，重复验证终态 Finding 只返回
  已持久化结果，不重复推进状态。
- IntegrityGate 会重新打开 Snapshot 原件、校验输入 hash，并通过 Workspace/发布桥校验
  输出 hash、大小、Step、Run 归属；EvidenceGate 校验 FILE/STEP/DATASET/ARTIFACT 的
  当前归属和可复核性。
- API 创建 Task 时必须显式给出 `project_snapshot_id`；取消、恢复和重试都沿用既有领域
  状态机，恢复不会自动切换到最新 Snapshot。
- 运行中异常和未知 API 异常统一为稳定错误 DTO；错误响应不回显 SQL、宿主路径、第三方
  SDK 正文或隐私原值。

## 4. Storage and migration impact

- Runtime schema 从 3 升至 4：`findings.coverage_report_id` 可空外键和索引。
- 既有 Finding、Runtime、Workspace 数据可由有序 migration 升级；新字段为空时保持旧草稿
  语义。回滚应通过备份恢复，不能删除已提交的 migration 或直接改写 Finding。
- 新增 `FINDING_COVERAGE_NOTICE` 和 `FINDING_DATA_WARNINGS` 脱敏事件，仅含 CoverageReport
  ID、未覆盖文件数量或告警数量，不含原始数据。

## 5. Security and privacy impact

- HTTP 默认由 CLI 绑定 `127.0.0.1`；非回环 host 启动会失败。没有新增公网认证或 Webhook
  承诺。
- API 不接受宿主路径；导入文件名经 `normalize_filename`，受控文件读取必须绑定 Project、
  FileVersion 和显式 Snapshot。
- Finding Gate 重新检查当前 Snapshot 原件和发布资源，哈希漂移、跨 Run/Task/Snapshot
  证据、缺失 Step 或非 AVAILABLE 资源均 fail closed。
- OpenTelemetry 只写关联 ID、hash、大小、状态、耗时、错误分类和告警数量；文本必须调用
  ModelGateway.sanitize_trace。观测后端异常不影响业务，隐私处理异常不被吞掉。
- 未把 Runtime DB、Privacy DB、Docker socket、Workspace 路径或模型 SDK 暴露到 API DTO。

## 6. Dependency changes

- 新增直接依赖：`fastapi>=0.115`、`uvicorn>=0.30`、`opentelemetry-api>=1.25`。
- `uv.lock` 已由 `uv lock` 更新，解析 131 个包；未新增模型、Sandbox 或外部在线服务依赖。
- 依赖许可证和镜像证据沿用前置阶段；本阶段未修改 Sandbox 镜像 digest。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv lock --check` | `PASS` | lock 与 `pyproject.toml` 一致 |
| `uv run ruff format --check .` | `PASS` | 194 files already formatted |
| `uv run ruff check .` | `PASS` | All checks passed |
| `uv run pyright` | `PASS` | 0 errors, 0 warnings, 0 informations |
| `uv run python -m dataharness.tooling.dependency_check` | `PASS` | 依赖方向无违规 |
| `uv run pytest tests/unit/test_phase09.py tests/integration/test_phase09_api.py -q --tb=short` | `PASS` | 5 passed |
| `uv run pytest -q --tb=short` | `PASS` | 221 passed，7 条 live 测试在未设置开关时显式 skipped |
| `$env:DATAHARNESS_LIVE_SANDBOX='1'; uv run --offline pytest tests/integration/test_opensandbox_live.py -q --tb=short` | `PASS` | 7 passed，真实 Docker/OpenSandbox create/attestation/execute/cancel/parallel/digest/AnalysisRuntime/durable recovery 链路通过；1 条依赖 warning |

## 8. Exit Gate evidence

1. **只有 Host Gate 能把 Finding 标为 VERIFIED/WARNING/REJECTED。**
   `AnalysisRuntime.submit_finding*` 只创建 DRAFT；`VerificationService.verify()` 统一执行
   Gate 后通过 Runtime CAS 和事件落库，新增单元与组合测试覆盖失败 Step、数据告警和 API 边界。
2. **每个 VERIFIED Finding 至少有一条当前有效证据链。**
   `FindingCandidate` 构造保证 evidence 非空；EvidenceGate 重新检查当前 Snapshot FILE、
   当前 Run STEP、同 Run 的 Dataset/Artifact 归属和 Workspace 发布状态，并由 IntegrityGate
   重算输入/输出 hash。
3. **FULL_PROJECT Finding 具有 CoverageReport，覆盖缺口可见。**
   Finding 草稿持久化 `coverage_report_id`；CoverageReport 与 Snapshot 不匹配会拒绝，缺口
   记录为 `FINDING_COVERAGE_NOTICE`，payload 仅含报告 ID 和未覆盖数量。
4. **HTTP 层不直接访问 SQLite、OpenSandbox、Workspace 路径或模型 SDK。**
   路由只调用 `ApiService`；组合测试使用真实 `ProjectCorpus`/Workspace 服务验证 Project、
   文件、Snapshot、Task 和事件链路。
5. **默认绑定本机且不误写 V1 公网能力。**
   CLI `serve` 默认 `127.0.0.1`，并拒绝其他地址；API 路由没有认证、Webhook 或公网触发器。
6. **观测后端故障不破坏状态，隐私故障 fail closed。**
   `OpenTelemetryAdapter` 在 exporter/SDK 故障时降级；没有 Task+ModelGateway 的文本观测会
   抛出 `ObservabilityPrivacyError`，单元测试覆盖该边界。

## 9. Architecture deviations and decisions

None。FastAPI、Uvicorn 和 OpenTelemetry SDK 均停留在 api/provider 装配边界；Domain、Analysis
协议和 Sandbox 协议没有引入第三方 SDK。SSE 未实现，沿用计划中的可选项和 Runtime 事件源。

## 10. Known issues and technical debt

- API 当前不负责启动 Worker；Task/Run 创建与状态控制已完成，实际耐久执行仍由既有
  `LocalDurableExecutor` 装配。Phase 10 应补真实本地服务入口的进程编排和全链路 E2E。
- OpenTelemetry 默认使用 SDK ProxyTracerProvider；实际 exporter/collector 装配留给部署层，
  Adapter 已验证无 exporter 时不阻断业务。
- live 测试由现有 OpenSandbox 组合链路证明，运行中出现一条 `ipaddress`/asyncio 依赖 warning，
  不影响 7 项通过；Phase 10 可随依赖升级复查。
- API 暂不提供 Findings 专用查询路由；Finding 事实可由 Runtime/Task 事件和下一阶段 UI
  服务读取，Phase 10 负责补充面向用户的最终回答 DTO。

## 11. Next-phase entry check

满足。下一阶段可直接复用：schema 4 migration、`VerificationService`/GateReport、CoverageReport
事件、ApiService/统一错误 DTO、HTTP 事件序列、OpenTelemetry 关联字段，以及现有 Docker live
fixtures。Phase 10 需要在不放宽 API/Sandbox/Privacy 边界的前提下完成全链路 E2E hardening、
Worker 入口和 release 证据。
