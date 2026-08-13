# Phase 06 Completion Report: Analysis runtime and agent capabilities

- Status: `PARTIAL`
- Date: `2026-08-13`
- Plan phase: `Phase 06`
- Commit/revision: `32dd801 feat(analysis): add auditable runtime capabilities`

## 1. Objective and scope

本阶段将模型生成的 Python/SQL 请求收敛为可审计、可幂等、可发布和可恢复的 `AnalysisStep`。本次实现覆盖 `AnalysisRuntime`、Project 窄能力、RELEVANT/FULL_PROJECT 入口、Workspace staging、Dataset/Artifact 注册、lineage 和 DRAFT Finding 提交。

阶段状态保持 `PARTIAL`：Phase 05 的真实 OpenSandbox 服务、镜像 digest、SBOM 和漏洞扫描仍受当前环境网络/Docker 不可用影响，因此本阶段的执行验证使用“不执行 code”的 deterministic fake provider，未宣称真实 Sandbox runner 已验收。

## 2. Detailed changes

- `src/dataharness/analysis/models.py` / `errors.py`：增加冻结的 `AnalysisRequest`、`InputReference`、`OutputSpec`、`AnalysisSummary`、`OutputInspection`、`FullProjectResult` 和稳定错误类型。请求显式包含输入版本/hash、预期输出、timeout、budget、mode 和 Task staging 引用；摘要包含有界 stdout/stderr、schema、statistics、resource stats、代码 hash 和输出引用。
- `src/dataharness/analysis/runtime.py`：实现 Run/Snapshot/lease 上下文校验、输入版本/hash 校验、规范化请求 hash、SQLite 幂等占位与完成、连续失败熔断、独立 Step 状态记录、受控 Sandbox 请求、staging 原子写入、Host 发布、Dataset/Artifact 注册和输入/代码 Step lineage。
- 同一 Runtime 重启后可通过 `analysis-summary.json` staging 摘要恢复已完成幂等请求；完整业务输出仍只在 staging/Workspace 保存，不进入摘要模型。
- `src/dataharness/capabilities/{analysis,projects,artifacts,lineage}/`：暴露窄的 Agent 能力和值对象，不暴露 Host shell、动态安装、网络、外部 API 或在线数据库。
- `src/dataharness/sandbox/models.py`：扩展 `ExecutionRequest` 的 input refs、staging ref、budget，并扩展 `ExecutionResult` 的 schema/statistics/resource stats；增加引用路径负向校验。
- `tests/contract/test_analysis_runtime_contract.py`：验证请求字段、禁止 Host 能力和 staging 路径注入。
- `tests/integration/test_analysis_runtime.py`：组合 SQLite、ProjectCorpus/FTS5、WorkspaceBridge 与 fake Sandbox，覆盖 Python/SQL、幂等恢复、staging/发布、lineage、Project 能力、FULL_PROJECT 缺口、Finding DRAFT 和熔断。

## 3. Interface and invariant changes

- `AnalysisRuntime.execute_python/execute_sql` 只接受结构化请求并下沉到 `SandboxProvider`；没有 Host fallback、shell、install 或 network 方法。
- `AnalysisRequest` 必须声明 `inputs`、`expected_outputs`、`timeout_seconds`、`budget_units`、`staging_ref` 和 `mode`；staging 只能绑定当前 Task 的逻辑引用。
- 输入必须属于 lease 固定的 READY `ProjectSnapshot`，且 `file_id` 与 content hash 同时匹配；拒绝跨 Snapshot 或漂移版本。
- 请求 hash 规范化包含 kind、代码 hash、输入引用/hash、输出声明、资源参数、mode 和镜像 digest；同一 Run 的相同规范请求可幂等恢复，连续三次同一请求失败后 fail closed。
- 输出先写当前 Step staging；只有 `WorkspaceBridge` 完成 Host 校验和发布后才标记 AVAILABLE 并注册正式 Dataset/Artifact。摘要/检查接口对文本和字节数有界。
- lineage 至少记录每个输入 `ProjectFileVersion -> Dataset/Artifact`，以及代码 `AnalysisStep(code_hash) -> Dataset/Artifact`；Finding 证据必须属于当前 Snapshot/Run 且 hash 匹配，初始状态固定为 `DRAFT`。
- `FULL_PROJECT` 只枚举固定 Snapshot，按 batch 执行并返回 CoverageReport ID、处理数和未覆盖数；UNSUPPORTED/FAILED/SKIPPED 不会被伪装成已处理。

## 4. Storage and migration impact

无新的 Runtime migration，继续使用 schema v2 的 AnalysisStep、幂等键、Coverage、Dataset/Artifact/Finding/Lineage 和 workspace publication 表。新增的 `analysis-summary.json` 位于当前 Task/Step staging，仅是可恢复摘要；原始输入、Runtime DB 和 Privacy DB 不复制到 Sandbox。已发布资源继续通过既有 STAGED -> AVAILABLE 协议落盘。

## 5. Security and privacy impact

- 生成代码只作为 `ExecutionRequest.code` 传给 Provider；Host 未导入或执行 `exec`/`eval`/shell/subprocess。
- 请求只携带稳定 ID/hash 和受控逻辑引用，不接受 Host 绝对路径、目录穿越、Runtime/Privacy DB 或 credential mount。
- Sandbox 生命周期、镜像和隔离规则由 Phase 05 的 `SandboxSpec`/Provider 约束；本阶段未添加任何降级执行路径。
- 摘要和输出检查有界，完整内容仍留在 Workspace；Finding 证据校验避免跨 Run/Snapshot 引用。
- 真实 OpenSandbox 隔离、镜像 digest、SBOM 和漏洞扫描仍未在本环境完成，不能将 fake 组合测试当作生产隔离证明。

## 6. Dependency changes

无新增或升级 Python 依赖，`pyproject.toml` 和 `uv.lock` 未改变。现有锁文件包含 DuckDB/PyArrow；当前环境没有 pandas/Pandera，且未在运行时安装。真实 secure-analysis 镜像依赖、OpenSandbox SDK 锁定和 SBOM 延续 Phase 05 阻塞项，不能用未验证依赖替代。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv lock --check --offline` | `PASS` | 30 packages resolved，lock 一致 |
| `uv run --offline ruff format --check .` | `PASS` | 148 files already formatted |
| `uv run --offline ruff check .` | `PASS` | All checks passed |
| `uv run --offline pyright` | `PASS` | 0 errors, 0 warnings, 0 informations |
| `uv run --offline pytest -q` | `PASS` | 191 passed |
| `uv run --offline pytest --cov=dataharness --cov-report=term-missing -q` | `PASS` | 191 passed，总覆盖率 91% |
| `uv run --offline python -m dataharness.tooling.dependency_check` | `PASS` | 依赖方向检查无违规 |
| `python -c` module probe | `PARTIAL` | DuckDB/PyArrow 可用；pandas/Pandera 不在当前环境，真实 runner 未启用 |

## 8. Exit Gate evidence

1. **工具 schema 无 shell/install/network/external API/online DB**：契约测试检查 `AnalysisRuntime` 无这些入口，`AnalysisRequest` 只声明 Python/SQL、输入、输出、超时、预算、staging 和 mode。
2. **Step 间无 Python 变量、后台进程或 Sandbox 内存依赖**：每次请求创建独立 `AnalysisStep`，跨步状态只通过 SQLite、Snapshot 和 Workspace 引用；fake provider 不执行代码。
3. **幂等与熔断**：集成测试覆盖同一规范请求的内存缓存和重建 Runtime 后的 staging 摘要恢复；三次 timeout 后第四次触发 `AnalysisCircuitOpenError`。
4. **正式输出需 Host 发布**：带 `WorkspaceBridge` 的测试断言发布后 `available=True`、正式资源和 lineage 已注册；无 bridge 的输出仍只留 staging。
5. **Finding 可追溯**：文件证据校验当前 Snapshot/hash，Step/Dataset/Artifact 证据校验当前 Run/hash；代码和输入均在 lineage 中记录。
6. **RELEVANT/FULL_PROJECT 覆盖语义**：RELEVANT 搜索复用 ProjectCorpus FTS5/BM25 并返回版本引用；FULL_PROJECT 测试披露 2 个文件中 1 个 UNSUPPORTED 缺口并生成 CoverageReport。
7. **真实 Sandbox/分析依赖证据**：未通过。Phase 05 的 Docker/OpenSandbox/镜像/SBOM 阻塞仍存在，pandas/Pandera 未锁定；本阶段不把 fake 验证升级为生产 Gate。

## 9. Architecture deviations and decisions

本次没有改变 `ARCHITECTURE.md` 的依赖方向。由于 Phase 05 的 SDK/服务不可用，AnalysisRuntime 依赖既有 `SandboxProvider` seam 和 fake adapter，不在 analysis 层导入 OpenSandbox SDK，也没有添加 Host fallback。摘要采用受控 staging 文件恢复，而不是把完整输出写入 Runtime SQLite，保持 Workspace 为大结果事实源。

## 10. Known issues and technical debt

- **Phase 05/06 真实执行阻塞**：需要恢复可访问的 OpenSandbox SDK/服务和 Docker 或等价隔离构建环境，锁定 secure-analysis 镜像 digest，生成 SBOM 并完成漏洞扫描，再运行真实 create/connect/execute/cancel/terminate 集成测试。
- **Sandbox runner 依赖**：DuckDB/PyArrow 已在锁文件中；pandas/Pandera 尚未锁定或装入镜像。负责人/后续阶段：Phase 05 恢复环境后补齐镜像构建和 runner 验证。
- **持久编排**：当前熔断和 staging 摘要是 Runtime 内部能力，lease 恢复、重试编排、Host crash recovery 仍由 Phase 07 负责。

## 11. Next-phase entry check

Phase 07 的接口前置条件部分具备：AnalysisRuntime 已有稳定请求/结果、Step、幂等、失败分类、Workspace staging 和 Sandbox seam。进入 Phase 07 前仍需完成 Phase 05 的真实 Sandbox Gate，并携带本报告的 191-test fixture、CoverageReport/lineage 断言和恢复摘要协议；在此之前计划中的 Phase 06/07 不能标记为 `COMPLETED`。
