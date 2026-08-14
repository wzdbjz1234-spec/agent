# Phase 06 Completion Report: Analysis runtime and agent capabilities（真实 Sandbox 复验）

- Status: `COMPLETED`（在原 PARTIAL 报告基础上完成真实 Sandbox runner 验证）
- Date: `2026-08-14`
- Plan phase: `Phase 06`
- Commit/revision: `9f511c3 feat(sandbox): unblock phase 05/06 with real OpenSandbox execution`
- 关联报告: [phase-06-analysis-20260813.md](phase-06-analysis-20260813.md)（PARTIAL 基线）、
  [phase-05-sandbox-20260814-addendum-01.md](phase-05-sandbox-20260814-addendum-01.md)

## 1. Objective and scope

原报告第 11 节明确：进入 Phase 07 前需完成 Phase 05 的真实 Sandbox Gate，并携带既有
191-test fixture、CoverageReport/lineage 断言和恢复摘要协议。本报告完成该复验：在真实
OpenSandbox 服务 + 锁定 digest 的 `secure-analysis` 镜像上执行 Python/SQL Step，验证
AnalysisRuntime 的完整数据流（staging 写入、Host 发布、schema/statistics 回传、幂等、
熔断语义不变）。原报告全部代码交付保持有效，未改动 AnalysisRuntime 接口。

## 2. Detailed changes

本阶段新增/修改（相对原 PARTIAL 基线）：

- `tests/integration/test_opensandbox_live.py::test_live_analysis_runtime_runs_python_and_sql`：
  AnalysisRuntime + 真实 `OpenSandboxProvider` 端到端——ProjectCorpus（LocalWorkspace +
  SQLite）导入 CSV、创建 Snapshot、固定 lease；`execute_python` 产出 `report.txt`
  （ARTIFACT）经 `WorkspaceBridge` 发布为 AVAILABLE；`execute_sql` 在真实 Sandbox 的
  DuckDB runner 上执行 `SELECT count(*) FROM "data"` 并返回结果。
- `sandbox-images/secure-analysis/sql_runner.py`：镜像内置 SQL runner（/project 表注册、
  查询执行、stdout CSV 载荷、schema/statistics sidecar）。
- `src/dataharness/providers/sandbox/opensandbox_sdk.py`：`execute_step` 的 SQL 分支调用
  内置 runner；sidecar 有界读取（range bytes=0-262143）映射到
  `ExecutionResult.output_schema/statistics`。
- `sandbox-images/secure-analysis/requirements.lock`：补齐 pandas、pandera（原报告第 10 节
  的 runner 依赖缺口）。

## 3. Interface and invariant changes

- `AnalysisRuntime` 接口不变；`ExecutionRequest.kind` 语义不变（PYTHON=直接执行，
  SQL=内置 runner）。
- 新增事实：SQL Step 的 stdout 是 CSV 载荷（V1 约定，`AnalysisSummary.stdout` 即输出
  数据）；schema/statistics 通过 `<step>.sql.schema.json` sidecar 有界回传。
- 真实执行验证的既有不变量：Step 之间无 Python 变量/后台进程/Sandbox 内存依赖
  （每次请求独立进程）；同一规范请求幂等；输出只有经 Host 发布才 AVAILABLE。

## 4. Storage and migration impact

None。Runtime schema v2 不变；staging/发布协议不变；`analysis-summary.json` 恢复协议不变。

## 5. Security and privacy impact

- 真实 SQL/Python 只在锁定 digest 的 secure-analysis 镜像内执行；Sandbox 断网
  （dns+nft deny-all）、非 root、根文件系统对执行用户不可写（attestation 逐项探测）。
- 生成代码不落 Host；Host 只校验引用、记录 Step、写有界摘要与发布 staging。
- 无新增隐私面：sidecar 只含列名/类型/行数，不含原始数据。

## 6. Dependency changes

- Python 依赖：无新增（opensandbox SDK 已在 Phase 05 addendum 锁定）。
- 镜像依赖：pandas==2.2.3、pandera==0.21.1 加入 requirements.lock（构建时安装，运行时
  无 pip；SBOM 146 包已含，OSV 扫描结果见 Phase 05 addendum 第 10 节）。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv lock --check` | PASS | 锁文件一致 |
| `uv run --offline ruff format --check .` / `ruff check .` | PASS | 179 files / All checks passed |
| `uv run --offline pyright` | PASS | 0 errors, 0 warnings |
| `uv run --offline pytest -q` | PASS | 216 passed, 6 skipped（live 显式跳过） |
| `DATAHARNESS_LIVE_SANDBOX=1 uv run pytest -q` | PASS | 222 passed，总覆盖率 89% |
| `DATAHARNESS_LIVE_SANDBOX=1 uv run pytest tests/integration/test_opensandbox_live.py -v` | PASS | 6/6，含 runtime e2e |
| `uv run --offline python -m dataharness.tooling.dependency_check` | PASS | 依赖方向无违规 |
| `docker run --rm secure-analysis:1.0.0 -c "import duckdb, pyarrow, pandas, pandera; ..."` | PASS | 镜像依赖齐全；pip 不存在（find_spec('pip') is None） |

## 8. Exit Gate evidence

1. **工具 schema 无 shell/install/network/external API/online DB。** 契约测试不变；
   真实链路中模型只见 `execute_python/execute_sql` 等窄工具，执行只下沉 Sandbox。
2. **Step 间无 Python 变量、后台进程或 Sandbox 内存依赖。** 真实 e2e 中 Python 步与
   SQL 步为两次独立进程执行，跨步数据只经 staging/Workspace。
3. **幂等与熔断。** 原集成测试（fake provider 路径）不变且通过；真实路径不改变
   请求 hash/熔断逻辑。
4. **正式输出需 Host 发布。** 真实 e2e 断言 `outputs[0].available is True`
   （WorkspaceBridge STAGED→AVAILABLE 后才是正式资源）。
5. **Finding 可追溯。** 原 191-test fixture 的 Finding/lineage 断言全部通过（完整套件
   222 passed）。
6. **RELEVANT/FULL_PROJECT 覆盖语义。** 原测试不变且通过；真实 Sandbox 不改变
   ProjectCorpus 检索与 CoverageReport 逻辑。
7. **真实 Sandbox/分析依赖证据。** 本报告与 Phase 05 addendum 提供：真实 create/
   attest/execute/cancel/terminate 6/6、AnalysisRuntime 真实 Python+SQL e2e、镜像
   digest/SBOM/OSV 扫描证据、pandas/Pandera 已装入镜像并验证可导入。

## 9. Architecture deviations and decisions

None 新增。真实 runner 的 SQL 语义（/project 文件主干=表名、stdout=CSV 载荷、sidecar=
schema/statistics）是对 Phase 06 交付「Sandbox SQL/Python runner」的具体化，符合既有
「stdout 是唯一输出载荷」的 V1 约定，已在本文档第 3 节记录。

## 10. Known issues and technical debt

- SQL runner 只注册 parquet/csv/json 顶层文件为表（V1 范围）；嵌套目录、xlsx、多 sheet
  与跨表 join 的高级形态留给 Phase 10。
- SQL 结果行数以 `fetchdf()` 全量载入内存（预览查询有 LIMIT 上限约束）；超大结果集的
  流式截断未实现，输出仍受 `max_output_bytes` Host 侧兜底。
- 剩余镜像漏洞（34 个，均为无上游修复的最新版）见 Phase 05 addendum 第 10 节。
- 真实环境的运行期依赖（OpenSandbox 服务、Docker、锁定镜像）是验收前提；live 测试
  用 `DATAHARNESS_LIVE_SANDBOX=1` 显式启用并在未设置时跳过（不掩盖失败）。

## 11. Next-phase entry check

Phase 07 入口满足：AnalysisRuntime 稳定接口 + 真实 Sandbox 执行验收完成 + 191/222-test
fixture 与 CoverageReport/lineage 断言可携带。Phase 07/08 的 `BLOCKED` 状态解除
（见 DEVELOPMENT_PLAN.md 状态更新）；进入 Phase 07 复验（耐久 executor 与恢复）时
以真实 provider 替换 fake 重新跑 orchestration 集成测试。
