# Phase 10 完成报告：E2E hardening and V1 release

- Status: `COMPLETED`
- Date: `2026-08-14`
- Plan phase: `Phase 10`
- Commit/revision: `待 checkpoint commit（本报告与实现一起提交）`

## 1. Objective and scope

本阶段目标是证明 `ARCHITECTURE.md` 第 22 节的完整验收链路在真实本地 API、SQLite、
ProjectCorpus/Workspace、OpenSandbox、fake cloud 和故障/恶意输入下成立，并形成可从干净
环境复现的 V1 发布物。

本阶段完成了：

- 本地 API 的最终 Finding、lineage 和 Task answer 查询面；
- 真实 API + Runtime SQLite + ProjectCorpus/LocalWorkspace + fake Sandbox/fake cloud 的
  E2E harness；
- 真实 Docker/OpenSandbox 的 Python、SQL、取消、并行隔离、digest 拒绝、AnalysisRuntime
  发布和 durable recovery 复验；
- secret/PII、prompt-in-file、路径逃逸、符号链接、超限输入和恶意输出相关负向测试；
- 发布前结构检查、镜像/依赖/SBOM/漏洞证据检查、配置样例、NOTICE 和运维手册；
- fake cloud 边界扫描、文件导入、Step 启动、durable recovery 和资源上限性能基线。

真实云模型、Webhook、Prefect、AgentFS、公网认证、多租户和生产数据不属于 V1 验收范围。

## 2. Detailed changes

### API、回答和事实查询

- `src/dataharness/storage/repository.py` 新增按 Task 查询 Finding、按 Run 查询 lineage
  的受控 Repository 方法，避免 API 拼接 SQL 或暴露数据库行。
- `src/dataharness/api/services.py` 新增 `task_findings`、`get_finding`、`task_lineage` 和
  `task_answer`；回答只由 Runtime 中的 Finding、正式 Dataset/Artifact、lineage 和
  Coverage/质量披露事件组成。
- `src/dataharness/api/models.py` 新增冻结的 `TaskAnswer` DTO。
- `src/dataharness/api/app.py` 新增：
  - `GET /tasks/{task_id}/findings`
  - `GET /findings/{finding_id}`
  - `GET /tasks/{task_id}/lineage`
  - `GET /tasks/{task_id}/answer`
- API 文件导入现在把受控 Workspace 的超限、符号链接和完整性错误统一映射为稳定的
  `FILE_IMPORT_FAILED`，不再由内部异常落到未知 500。

### E2E、安全和基线

- `tests/e2e/test_phase10_v1.py` 使用真实 `ApiService.from_settings`、Runtime SQLite、
  LocalWorkspace、ProjectCorpus、TestClient、fake Sandbox 和 fake cloud，覆盖 CSV/JSON
  多文件导入、文件新版本、旧/新 Snapshot、RELEVANT 搜索、Python 发布、Finding Gate、
  lineage、最终回答和单 Task 取消隔离。
- 同一测试集验证 API 路径逃逸、资源大小上限、Workspace 符号链接拒绝、fake cloud 的
  PII 占位、secret fail-closed、Privacy DB 与 Runtime DB 物理分离，以及文件内 prompt
  只作为数据保存。
- `scripts/phase10_baseline.py` 输出 JSON 性能和资源基线；脚本不连接真实模型、不在 Host
  执行生成代码。
- `scripts/release_check.py` 检查锁文件、配置样例、运维文档、NOTICE、secure-analysis
  依赖锁和真实镜像 digest/SBOM/漏洞扫描证据；兼容标准 SPDX 与本地 Docker Scout SBOM
  格式。
- `scripts/verify.py` 将发布物结构检查纳入统一本地验证。
- `doc/V1_OPERATIONS.md` 固化启动、健康检查、镜像证据、故障恢复、取消、Privacy DB、
  诊断、已知限制和 V1 非目标。
- `NOTICE` 说明项目与第三方依赖的许可证/Notice 责任边界。

## 3. Interface and invariant changes

- 新增回答接口只返回稳定 ID、状态、Finding 证据引用、正式资源 hash、lineage 和披露项；
  不返回 Workspace 宿主路径、Runtime SQLite 行、模型原始消息、prompt、stdout/stderr
  或 Privacy 映射。
- `TaskAnswer` 的 Finding 不绕过 Host Verification Gate；DRAFT、VERIFIED、WARNING 和
  REJECTED 状态原样展示，Coverage/数据质量披露来自已持久化事件。
- Task 的 lineage 查询按 Run 归属聚合并按 lineage ID 去重；发布对象仍以 Workspace
  AVAILABLE 记录和 hash 为事实源。
- 文件导入失败统一为客户端可理解的 400，而内部路径和异常正文不穿过 API。
- 没有新增 Runtime migration；查询是既有 schema 的只读视图，旧数据库可直接升级。

## 4. Storage and migration impact

无 schema 或 migration 变化。新增 Repository 查询只读既有 `findings`、`finding_evidence`、
`lineage`、`datasets`、`artifacts` 和事件表。

Workspace 布局不变：Project sources/extracted/indexes 与 Task working/staging/state 仍由
既有 LocalWorkspace 管理。回答查询不读取宿主路径。回滚实现可使用上一版本代码；新增的
API 路由和脚本不改变既有 Runtime 数据。

## 5. Security and privacy impact

- 真实 Sandbox 使用已记录 digest：
  `sha256:11929d8dbf14021a638c51c0db8771d5f687e14431d82f97d3e75ac977868188`。
- OpenSandbox live 验收确认真实 create/attest/execute/terminate、SQL runner、cancel、
  同 Project 并行 lease、错误 digest fail-closed、AnalysisRuntime 发布和 durable
  Sandbox rebuild；服务配置使用非特权、无网络和白名单挂载。
- 生成代码只作为 `ExecutionRequest.code` 传给 Sandbox Provider；fake Provider 也只查表
  返回预置结果，不执行输入代码。现有静态检查和全套测试继续保证 Host 没有动态执行回退。
- Sandbox 挂载只包含 `/project`、`/task/working`、`/task/staging`；Runtime DB、Privacy DB
  和 credential 不进入挂载。Privacy SQLite 与 Runtime SQLite 使用不同根目录。
- secret 在 `ModelGateway` 前 fail-closed；PII 使用 Task-local 稳定占位，原始数据不被改写，
  恢复规则和跨 Task 隔离由既有 Privacy 测试覆盖。
- 新增安全测试覆盖 `../`、绝对/非法文件名、符号链接、超限文件、prompt-in-file、恶意
  fake cloud 输入和恶意 Sandbox 输出；所有失败只返回稳定错误，不回显内部路径或敏感正文。
- 运维手册明确规则型 PII 检测是 best-effort，不将其描述成完整数据安全边界。

## 6. Dependency changes

None。未新增或升级 Python 依赖；`uv.lock` 与 `sandbox-images/secure-analysis/requirements.lock`
保持锁定。现有 `build-evidence` 包含实际镜像 digest、Docker Scout SBOM 和漏洞扫描 JSON，
`scripts/release_check.py --require-image` 已验证其结构。许可证说明见 `NOTICE` 和现有
锁文件/镜像依赖元数据。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv run pytest -q tests/e2e/test_phase10_v1.py tests/integration/test_phase09_api.py` | `PASS` | 4 passed |
| `$env:DATAHARNESS_LIVE_SANDBOX='1'; uv run pytest -q tests/integration/test_opensandbox_live.py tests/e2e/test_phase10_v1.py` | `PASS` | 9 passed, 1 known `ipaddress`/asyncio RuntimeWarning, 188.82s |
| `uv run python scripts/phase10_baseline.py` | `PASS` | fake cloud 扫描 p50 13.197ms；文件导入 p50 57.485ms；fake Step p50 13.651ms；恢复 79.673ms |
| `uv run python scripts/release_check.py --require-image` | `PASS` | 锁文件、配置、NOTICE、运维文档、digest、SBOM、漏洞扫描证据通过 |
| `uv run ruff format --check src tests scripts` | `PASS` | 173 files formatted |
| `uv run ruff check src tests scripts` | `PASS` | All checks passed |
| `uv run pyright` | `PASS` | 0 errors, 0 warnings |
| `uv run python scripts/verify.py` | `PASS` | 223 passed, 7 explicitly skipped live tests；lock/release/format/lint/type 全通过 |

资源基线来自 `Settings` 默认 V1 配置：单文件 100MiB、单次输出 10MiB、内存 1024MiB、
磁盘 2048MiB、最多 32 个进程。真实 OpenSandbox live 测试使用同一锁定镜像和受控资源规格。

## 8. Exit Gate evidence

- **完整验收链路自动化证据**：新增 `tests/e2e/test_phase10_v1.py`，并结合真实
  `tests/integration/test_opensandbox_live.py`、`test_analysis_runtime.py`、
  `test_orchestration.py`、`test_privacy_sqlite.py` 和 Phase 08 Agent 测试；统一运行结果
  为 223 passed，真实 Sandbox 组合为 9 passed。
- **生成代码未在 Host 执行，Runtime/Privacy/credential 未入 Sandbox**：AnalysisRuntime 和
  Provider 仍只把代码交给 OpenSandbox；三挂载白名单和 OpenSandbox attestation live 通过；
  `test_privacy_sqlite.py` 与 sandbox model/provider 测试通过。
- **secret/PII 边界**：新增 E2E 断言 fake cloud 收不到 secret、只收到 PII placeholder；
  既有 Privacy SQLite 测试证明原文仅在 Task Privacy DB，原始输入 hash/内容保持不变。
- **版本、Snapshot、来源和覆盖**：新增 E2E 断言旧文件版本和旧 Snapshot 仍返回 alpha；
  既有 FULL_PROJECT 测试断言 CoverageReport、UNSUPPORTED 缺口和来源引用准确；真实 SQL
  live 测试确认 Snapshot 数据可被 Sandbox 查询。
- **并行 Task 和取消隔离**：新 E2E 断言取消旧 Task 不影响同 Project 新 Task；真实 live
  parallel test 断言销毁一个 lease 后另一 lease 仍可执行。
- **崩溃、Sandbox loss、超时、cancel、预算、重复与部分发布**：durable orchestration、
  AnalysisRuntime circuit breaker、Sandbox Provider cleanup、Agent budget、Workspace
  publication 和 idempotency 测试均在全量套件通过；真实 durable recovery 也通过。
- **Finding 和发布完整性**：新 E2E 通过 `VerificationService` 将带 Artifact hash 的 DRAFT
  Finding 晋级 VERIFIED；回答接口返回同一 hash 和 lineage；Gate 重新读取 AVAILABLE
  发布事实。
- **可复现发布**：`uv.lock`、secure-analysis requirements lock、配置样例、镜像 digest、
  SBOM、漏洞扫描、NOTICE、Skill hash 校验代码、`V1_OPERATIONS.md` 和 release check 均已
  固化；干净临时目录运行基线脚本成功。

## 9. Architecture deviations and decisions

None。回答层复用 Runtime 事实源和既有 Host Gate，没有引入第二套工作流、第二套数据库、
Host 执行回退或公网能力。

## 10. Known issues and technical debt

- OpenSandbox live 测试仍有一个已知 Python `ipaddress`/asyncio `RuntimeWarning`，不影响
  结果；后续可在升级 OpenSandbox SDK/Python runtime 时重新定位并清理。
- `tests/integration/test_opensandbox_live.py` 需要本机 OpenSandbox 服务和真实镜像证据，
  普通全量测试默认明确 skip；发布/CI 环境必须单独设置 `DATAHARNESS_LIVE_SANDBOX=1`。
- PII 识别仍是规则型 best-effort；V1 不承诺完整隐私识别或生产云账号验收。
- 本地 API 仍是单机单用户控制面，不提供公网认证、TLS、RBAC、多租户或 Webhook。

以上限制已写入 `doc/V1_OPERATIONS.md`，不阻塞本阶段 V1 Exit Gate。

## 11. Next-phase entry check

Phase 10 是当前开发计划的最后阶段；Phase 00–10 的报告、锁文件、测试和运维材料齐备，
可以进入 V1 发布审阅/后续产品化工作。后续若扩展公网、多租户、真实云账号、Webhook 或
更强隐私识别，必须先重新定义信任边界、认证、数据保留和对应 Gate，不能把本报告的 V1
本地能力直接外推为生产安全承诺。
