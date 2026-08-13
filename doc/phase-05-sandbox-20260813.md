# Phase 05 Completion Report: OpenSandbox execution seam

- Status: `PARTIAL`
- Date: `2026-08-13`
- Plan phase: `Phase 05`
- Commit/revision: `eb1129b feat(sandbox): add isolated execution seam`

## 1. Objective and scope

本阶段已完成不可信 Python/SQL/Skill 执行的稳定 Sandbox 边界、严格安全规格、OpenSandbox
适配层、确定性 fake、per-Run lease、per-Step 清理、超时/取消/丢失/输出上限错误分类，以及
镜像构建清单与证据采集脚本。

尚未完成真实 OpenSandbox SDK/服务集成和实际 `secure-analysis` 镜像构建、digest、SBOM、
漏洞扫描证据。因此本报告为 `PARTIAL`，开发计划保持 `IN_PROGRESS`，不将 Phase 05 标记为
完成。

## 2. Detailed changes

- `src/dataharness/sandbox/`：新增不依赖 SDK 的 `SandboxProvider` 协议、`SandboxSpec`、
  `SandboxMount`、`SandboxResources`、`SandboxLease`、`ExecutionRequest/Result` 与稳定错误。
  规格只允许固定 digest、断网、非特权 `sandbox` 用户、只读根，且只能挂载固定 Snapshot
  的 `/project`（只读）和当前 Task 的 `/task/working`、`/task/staging`（可写）。
- `src/dataharness/providers/sandbox/opensandbox.py`：新增生产 `OpenSandboxProvider` 与只在
  Provider 层存在的 SDK 包装协议 `OpenSandboxClient`。创建、重连均以 SDK 实际 attestation
  比对完整安全规格；任何漂移 fail closed，创建后的漂移会尽力销毁 Sandbox。
- `src/dataharness/providers/sandbox/fake.py`：新增确定性 fake Provider。它仅将 code 当作数据
  记录，绝不使用 `exec`、`eval`、shell 或 subprocess；可模拟超时、取消、输出超限、Sandbox
  丢失、清理与并行 Run 的独立 lease。
- `src/dataharness/config.py`、`dataharness.example.toml`：配置层禁止启用 Sandbox 网络，新增
  `max_processes` 资源限制。
- `sandbox-images/secure-analysis/`：新增 Dockerfile、版本锁定依赖文件和 `build.ps1`。脚本
  强制传入不可变 base-image digest，构建后记录实际 image digest，并拒绝在没有 Docker 时伪造
  构建结果；README 说明 SBOM 与漏洞扫描产物的位置。
- `tests/`：新增 Sandbox spec 负向测试、Provider 契约测试、OpenSandbox 适配层 attestation
  集成测试，以及 AST 静态检查，验证边界源码没有 Host 执行原语。

## 3. Interface and invariant changes

- `SandboxProvider` 仅包含 `create/connect/execute/cancel/terminate`；没有 Host shell、动态装包、
  网络开关或任意额外挂载操作。
- `SandboxSpec` 的 `image_digest` 必须为 `sha256:<64 lower-case hex>`；只有三项精确受控的
  mount 可通过验证。Runtime DB、Privacy DB、Docker socket、Host credential 及其他 Task 既不
  在 mount 白名单中，也没有可接受的目标路径。
- `OpenSandboxProvider` 将每个 lease 绑定其 Run/Task/Project/Snapshot/digest；未知 Sandbox、
  伪造 lease、attestation 漂移或清理失败均转换为 fail-closed 错误。
- 每次 `execute` 都在 `finally` 调 SDK cleanup；取消也在 cleanup 后才返回。Sandbox 丢失可用
  同一 `SandboxSpec.image_digest` 重建，但本阶段不持久化重建编排，留待 Phase 07。
- `ExecutionResult` 仅含有界 stdout/stderr 和进程元数据；输出超过 `max_output_bytes` 时抛出
  `SandboxOutputLimitError`，不把完整输出回传给 Host。

## 4. Storage and migration impact

None。Phase 05 不增加 Runtime migration，也不将 Sandbox 过程状态作为新的 SQLite 事实源。
lease 仅为 Provider 内存中的临时资源；耐久 lease、恢复和取消编排仍由 Phase 07 的 Runtime
SQLite/worker 实现。Privacy DB、Runtime DB 与 Workspace 的既有物理边界未改变。

## 5. Security and privacy impact

- 源码静态测试禁止 sandbox/provider 边界导入 `subprocess`/`shlex` 或调用 `exec`、`eval`、
  `system`、`popen`、`run`、`Popen`，因而生成代码没有 Host 执行路径。
- 精确 mount 校验阻止 Runtime/Privacy/credential/Docker 与其他 Task 被带入 Sandbox；所有挂载
  使用无 Host 路径的内部 resource reference。
- 实际运行时 attestation 覆盖 digest、网络、特权、只读根、用户、全部 mounts 和资源限制；
  缺项或不一致直接拒绝，不存在宽松配置降级。
- fake 不执行代码，所有 fixture 均为合成字符串。真实镜像尚未构建，故尚不能证明实际
  OpenSandbox 服务的网络、文件系统、用户与进程隔离；这是本阶段未完成的安全证据。

## 6. Dependency changes

没有成功新增 Python 依赖，`uv.lock` 无变化。尝试通过 `uv add opensandbox` 引入官方 SDK 时，
环境的 PyPI 连接被拒绝，且 Docker CLI 不存在；为避免写入未解析依赖或伪造 SDK 调用，本阶段
使用 Provider 层的 `OpenSandboxClient` 窄包装协议。

镜像构建依赖在 `sandbox-images/secure-analysis/requirements.lock` 中显式固定已锁定的 DuckDB
和 PyArrow 版本。镜像实际 digest、SBOM 与漏洞扫描尚未生成，因而没有 License/镜像漏洞扫描
结论可记录。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv lock --check --offline` | PASS | 30 packages resolved；锁文件无漂移 |
| `uv run --offline ruff format --check .` | PASS | 136 files already formatted |
| `uv run --offline ruff check .` | PASS | All checks passed |
| `uv run --offline pyright` | PASS | 0 errors, 0 warnings, 0 informations |
| `uv run --offline pytest -q` | PASS | 185 passed in 11.32s |
| `uv run --offline pytest --cov=dataharness --cov-report=term -q` | PASS | 185 passed；总覆盖率 93% |
| `Get-Command docker` | BLOCKED | 本机未安装 Docker CLI，无法构建或运行 secure-analysis |
| `uv add opensandbox` | BLOCKED | PyPI 连接被拒绝，不能解析/锁定 SDK 依赖 |

## 8. Exit Gate evidence

1. **生成代码没有 Host 执行路径。** `test_sandbox_host_execution.py` 对 sandbox 与 provider
   源码 AST 断言不存在 Host 执行模块/调用；fake 只保存 code 字符串，OpenSandboxProvider 只将其
   转发给 `OpenSandboxClient.execute_step`。
2. **Sandbox 不可见 Runtime/Privacy/credential/Docker/其他 Task。** `SandboxSpec` 仅接受三项
   精确 mount，`test_sandbox_models.py` 覆盖 Runtime 引用和非白名单 target 的负向拒绝；实际
   OpenSandbox 服务的挂载结果仍待 integration environment 认证。
3. **取消/销毁不影响并行 Run。** `test_terminating_one_parallel_run_does_not_affect_another`
   证明两个 Run 使用独立 lease，销毁一方后另一方仍可执行。
4. **attestation 不符 fail closed。** `test_provider_fails_closed_and_terminates_on_attestation_drift`
   注入网络漂移，断言创建失败且适配层调用销毁；代码比对 digest、网络、特权、根、用户、挂载
   与资源全部字段。
5. **超时、取消和丢失不留 fake 残留。** Sandbox 契约测试验证每种路径都记录 cleanup；生产
   Adapter 使用 `finally` 调 SDK cleanup。真实进程的清理仍待 OpenSandbox 环境验证。
6. **镜像证据齐全。** BLOCKED：Docker 不可用，尚无实际 image digest、SBOM 和漏洞扫描结果。

## 9. Architecture deviations and decisions

未改变架构承诺。由于无法访问 PyPI，`OpenSandboxClient` 是临时的部署装配协议，而不是替代
SandboxProvider 的第二执行器；官方 SDK 只能在该 Provider 包内实现。真实 SDK 接入后应以
一次独立、可复现的依赖锁定提交替换该包装实现，不允许在 agent/analysis 层直接导入 SDK。

## 10. Known issues and technical debt

- **阻塞项：**缺少 Docker/OpenSandbox 服务与可访问的包源，无法生成真实镜像、SDK 锁、digest、
  SBOM 和漏洞扫描证据；影响 Phase 05 最终 Gate 与后续 Phase 06 的真实 Sandbox integration。
- `requirements.lock` 目前仅固定已锁定的 DuckDB/PyArrow；pandas、Pandera、绘图库和审核过的
  Skill 依赖应在恢复包源后与 Phase 06 的运行需求一同锁定、构建和扫描，不能在运行时安装。
- Provider lease 尚未落盘；这是 Phase 07 的耐久 executor 与恢复职责，不应在本阶段引入第二
  事实源。

## 11. Next-phase entry check

Phase 06 的接口前置条件（SandboxProvider、受控请求/结果、稳定错误、PII 受控恢复入口）已
具备，但其真实 Sandbox integration 与 Phase 05 最终验收仍被环境阻塞。恢复条件如下：

1. 在可访问 PyPI 或内部镜像源的环境锁定官方 OpenSandbox SDK，并实现/测试
   `OpenSandboxClient` SDK 包装；
2. 安装 Docker 或等价隔离构建器，使用 `build.ps1` 构建 `secure-analysis`，记录实际 digest；
3. 对该 digest 生成 SBOM、完成漏洞扫描和许可证审查，并把证据链接到本报告的 addendum；
4. 使用真实 OpenSandbox 执行 create/connect/execute/cancel/terminate integration tests，验证
   网络、用户、只读根、三项挂载、资源限制和无残留进程。
