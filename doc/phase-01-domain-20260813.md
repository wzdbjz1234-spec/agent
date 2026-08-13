# Phase 01 Completion Report: Domain model and state machines

- Status: `COMPLETED`
- Date: `2026-08-13`
- Plan phase: `Phase 01`
- Commit/revision: `b546e5c`（“补充领域单元测试”）

## 1. Objective and scope

建立不执行 I/O、不依赖外部框架的纯领域层，覆盖 Project 语料对象（Project/ProjectFile/
ProjectFileVersion/ProjectSnapshot/ProjectCoverageReport）、计算生命周期对象（Session/Task/
Run/AnalysisStep）与正式资源对象（Dataset/Artifact/Finding/Lineage），并用迁移表驱动状态机、
冻结值对象表达“终态不可回退、文件版本与 Snapshot 不可变”等不变量。

明确未包含：ID 生成（属于应用/编排层，领域层只接收显式 ID）、EvidenceGate 的正式证据校验
（Phase 09）、数据库行/文件路径/第三方 SDK 类型。

## 2. Detailed changes

### 工程骨架（支撑 Phase 01 的最小脚手架）

- `pyproject.toml`：项目元数据、`pydantic>=2.7` 运行时依赖与 dev 依赖组（pytest、
  pytest-asyncio、pytest-cov、hypothesis、ruff、pyright），hatchling 构建 + src 布局，
  Ruff/Pyright/pytest 配置。
- `uv.lock`：锁定全部直接与传递依赖。
- `.gitignore`、`.python-version`：忽略缓存/构建产物，固定 Python 3.12。
- `src/dataharness/__init__.py`：包入口。

### 领域层 `src/dataharness/domain/`（16 个文件）

- `enums.py`：FileVersionStatus、ProjectStatus、CoverageItemStatus、TaskStatus、WaitReason、
  RunStatus、RunPhase、StepStatus、StepFailureKind、FindingStatus（均为 StrEnum）。
- `ids.py`：13 个 `NewType` 字符串 ID + `ContentHash`。
- `hashes.py`：`compute_content_hash`（SHA-256 十六进制）。
- `clock.py`：`utcnow`（UTC 带时区）。
- `errors.py`：DomainError 及其子类 IllegalStateTransitionError、InvalidStateError、
  FileVersionImmutableError、InvalidEvidenceError。
- `state_machine.py`：PEP 695 泛型 `check_transition` 与 `TransitionTable` 别名。
- `project.py`：Project、ProjectFile、ProjectFileVersion、SnapshotEntry、ProjectSnapshot、
  CoverageItem、ProjectCoverageReport。
- `session.py`：Session。
- `task.py`：Task + `TASK_TRANSITIONS` 迁移表。
- `run.py`：Run + `RUN_TRANSITIONS`/`RUN_PHASE_TRANSITIONS`。
- `step.py`：AnalysisStep + `STEP_TRANSITIONS`。
- `artifact.py`：Dataset、Artifact。
- `finding.py`：EvidenceKind、EvidenceRef、FindingCandidate、Finding。
- `lineage.py`：ResourceKind、ResourceRef、Lineage。
- `__init__.py`：公共接口重导出。

所有实体均为 `frozen=True` 的 Pydantic 模型，状态迁移返回新实例（copy-on-write），不原地修改。

### 测试 `tests/unit/`（10 个文件）

`conftest.py`（固定时间 fixture）、`test_ids_hashes.py`、`test_project.py`、`test_task.py`、
`test_run.py`、`test_step.py`、`test_finding.py`、`test_artifacts_lineage.py`、
`test_domain_purity.py`（AST 源码级校验禁止导入）。

## 3. Interface and invariant changes

- 迁移表（均为 `dict[当前状态, frozenset[下一状态]]`）：
  - Task：`QUEUED→{ACTIVE,CANCELLED}`、`ACTIVE→{WAITING,COMPLETED,FAILED,CANCELLED}`、
    `WAITING→{ACTIVE}`；终态无出边。
  - Run：`QUEUED→{RUNNING,CANCELLED}`、`RUNNING→{WAITING,SUCCEEDED,FAILED,CANCELLED}`、
    `WAITING→{RUNNING}`；终态无出边。
  - Run phase：`PREPARING→REASONING→EXECUTING→VERIFYING→FINALIZING`（仅前向）。
  - Step：`PENDING→{RUNNING,CANCELLED}`、`RUNNING→{SUCCEEDED,FAILED,TIMED_OUT,CANCELLED}`。
  - Finding：`DRAFT→{VERIFIED,WARNING,REJECTED}`。
- 不变量：Task 必绑 Project（`project_id` 必填）；Run 固定 `project_snapshot_id`（必填、不可变）；
  FileVersion 定稿后不可变（`mark_ready/failed/unsupported` 仅从 IMPORTING）；
  Snapshot 冻结无更新方法；wait_reason 仅随 WAITING 存在；failure_kind 仅随 FAILED 存在；
  FindingCandidate 必须至少引用一条证据；cancel/archive 幂等。
- 错误：非法迁移统一抛 `IllegalStateTransitionError`；start/resume 语义分离（`_require_from`
  校验来源状态，避免共享同一目标状态导致混用）。

## 4. Storage and migration impact

`None`。本阶段无 Runtime SQLite schema、无迁移、无 Workspace 布局改动。

## 5. Security and privacy impact

- 本阶段不涉及网络、凭据、PII、Sandbox 或日志/trace。
- 新增 `test_domain_purity.py` 以源码 AST 方式证明 domain 不导入 FastAPI、PydanticAI、
  OpenSandbox、sqlite3、OpenTelemetry，也不反向导入内部模块，为后续隐私/Sandbox 边界奠定基础。

## 6. Dependency changes

- 新增直接依赖：`pydantic>=2.7`（运行时）；`pytest`、`pytest-asyncio`、`pytest-cov`、
  `hypothesis`、`ruff`、`pyright`（dev 组）。全部经 `uv.lock` 锁定。
- 未新增 Sandbox 镜像、未引入向量数据库/在线数据库客户端/Prefect 等。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv lock --check` | PASS | 锁文件与 pyproject 一致 |
| `uv run ruff format --check .` | PASS | 67 文件已格式化 |
| `uv run ruff check .` | PASS | 0 错误 |
| `uv run pyright` | PASS | 0 errors, 0 warnings |
| `uv run pytest -q` | PASS | 95 passed |
| `uv run pytest -q --cov=dataharness.domain --cov-report=term` | PASS | 领域覆盖率 100%（475 语句，0 遗漏） |

## 8. Exit Gate evidence

- **domain 不导入 FastAPI、PydanticAI、OpenSandbox、sqlite3 或 OpenTelemetry**
  `tests/unit/test_domain_purity.py::test_domain_does_not_import_forbidden_modules`（AST 源码扫描，通过）。
- **每个状态节点和边都有测试，非法转换返回稳定领域错误**
  `test_task.py`/`test_run.py`/`test_step.py`/`test_finding.py` 的 `test_legal_transition` 与
  `test_illegal_transition_raises` 表驱动参数化覆盖全部合法/非法边；非法迁移统一抛
  `IllegalStateTransitionError`。
- **相同输入产生稳定的 hash/幂等语义**
  `test_ids_hashes.py` 证明 SHA-256 稳定；`archive`/`cancel` 幂等测试证明重复调用返回自身。
- **领域 Interface 不暴露数据库行、文件路径或第三方 SDK 类型**
  ID 均为 `NewType(str)`，实体字段仅含值对象/枚举/时间，无路径、无 SQL 行、无 SDK 类型。
- **文件更新只能创建新版本；Snapshot 不提供原地更新操作**
  `test_finalized_version_is_immutable` 证明定稿后 `mark_*` 抛 `FileVersionImmutableError`；
  `ProjectSnapshot` 为冻结模型且无任何更新方法。

## 9. Architecture deviations and decisions

- 依据 `ARCHITECTURE.md` 第 13.2 节状态图，Task/Run 的 `WAITING` 没有 `→CANCELLED` 出边，
  实现严格遵循该图（`test_task.py` 显式断言 `WAITING→cancel` 非法）。若产品需要“等待中取消”，
  需先修订 `ARCHITECTURE.md` 再放开该边。
- `Run` 同时承载 `wait_reason`（架构在 Task 段落定义 WaitReason，但 Run 的 WAITING 同样需要原因，
  如预算耗尽），与 Task 保持一致，未改变架构语义。

## 10. Known issues and technical debt

- **Phase 00 未正式完成**：本阶段仅搭建了支撑 Phase 01 的最小工程骨架（pyproject/uv.lock/
  质量工具配置/包入口）。Phase 00 的其余交付物（配置模型、依赖方向检查通用 fixture、fake clock/
  ID factory、统一 CI/验证脚本）仍待完成，应在进入 Phase 02（storage）前补齐。
- Hypothesis 与 pytest fixture 混用会报 “fixture not found”，当前 @given 测试改用模块级常量时间
  规避；如需在 @given 测试中使用 fixture，需升级/调整 Hypothesis 的 pytest 集成。

## 11. Next-phase entry check

Phase 02（Runtime storage）依赖 Phase 00 与 Phase 01。领域层已就绪：`domain` 包可被导入，实体、
枚举、错误与 ID 值对象接口稳定，可为 storage repository 提供领域类型。进入 Phase 02 前需先完成
Phase 00 的配置模型与依赖方向检查基础设施，并把领域 ID 生成策略落为可注入的应用层组件。
