# Phase 13 Completion Report: One-command local deployment

- Status: `PARTIAL`
- Date: `2026-08-15`
- Plan phase: `Phase 13`
- Commit/revision: `working tree；未创建 checkpoint commit，保留既有 Phase 11/12 与用户原有修改`

## 1. Objective and scope

本阶段把个人本机应用固化为 PowerShell 7 可检查、启动、停止、诊断、备份和恢复的发布包。
已完成 API、Worker、OpenSandbox 三个独立宿主进程的受管生命周期，WebUI 同源静态托管
验证，固定镜像/锁文件/端口/配置预检，受管 PID 归属保护，密钥状态诊断和备份恢复工具。

本报告只能标记为 `PARTIAL`：当前验收会话没有真实模型 API Key，因而无法在干净环境中
从 WebUI 运行并完成一条真实 Agent 分析链路；没有伪造该证据，也没有把 WAITING 模式当作
真实模型成功。团队认证、多租户、TLS 和高可用不属于本阶段。

## 2. Detailed changes

- `setup.ps1`：在不复制秘密的前提下检查 PowerShell、uv、锁文件、Docker、端口、
  OpenSandbox 配置和 `runtime-data/projects` 的唯一允许挂载路径、前端构建物、镜像
  digest/SBOM/漏洞扫描证据；以 `uv sync --locked` 创建 Python 依赖，生成
  `.dataharness/config.toml` 与 setup 标记。
- `start.ps1`、`stop.ps1`、`status.ps1`：按 Sandbox → API → Worker 启动，按 Worker drain
  → Sandbox → API 停止；使用 PID、启动时间和命令指纹证明归属，重复启动不复制进程。状态
  仅显示密钥是否存在，所有服务固定回环地址。
- `scripts/deployment_common.ps1`：集中实现原子 JSON、脱敏日志、受管进程、端口和有界
  Docker/HTTP/TCP 检查。修复 JSON 时区反序列化导致的 PID 误判、Worker 事件订阅输出、
  单元素受管记录和 OpenSandbox `uvx` 包装器遗留子进程问题。
- `src/dataharness/worker.py`、`src/dataharness/cli.py`、`src/dataharness/api/services.py`、
  `src/dataharness/providers/durable/executor.py`：提供独立 Worker CLI、最小心跳与 run 状态
  投影，使诊断不读取 Runtime 原始载荷。
- `backup.ps1`、`restore.ps1`：备份完整 Runtime 数据根，逐文件记录/验证 SHA-256，拒绝
  reparse point 和路径穿越；恢复只写入用户明确指定的空目录，从不覆盖现有 `runtime-data`。
- `scripts/phase13_acceptance.ps1`、`scripts/phase13_lifecycle_acceptance.ps1`：分别覆盖
  静态/模拟边界和真实三进程异常恢复。生命周期演练的脱敏日志位于被忽略的
  `.dataharness/phase13-lifecycle-acceptance.log`。
- `README.md`、`doc/V1_OPERATIONS.md`：补充发布、故障、备份、恢复、升级和卸载操作边界。

## 3. Interface and invariant changes

- 发布入口为 `setup.ps1`、`start.ps1`、`stop.ps1`、`status.ps1`、`backup.ps1` 和
  `restore.ps1`；运行期不需要 Node.js，前端只由 FastAPI 同源提供。
- `start.ps1` 默认要求受管 TOML 中的 `[model].api_key` 存在；`-AllowMissingModelKey` 仅允许
  启动可诊断的 WAITING 模式，密钥值不进入参数、状态或日志。
- `stop.ps1` 只触碰启动时间与命令指纹均匹配的 PID。无法证明归属时 fail closed；默认
  drain 超时也不强杀，必须由用户显式传入 `-Force`。
- Worker 心跳是派生诊断信息，不替代 Runtime SQLite 的 Task/Run/Step 事实；恢复仍由
  LocalDurableExecutor 的 lease、checkpoint 和终态规则决定。
- 备份包含 Runtime SQLite、Project Workspace 和 Privacy SQLite；恢复不接受绝对/上级
  路径，且不会就地覆盖用户现有数据。

## 4. Storage and migration impact

没有新增 SQLite schema 或 migration。`.dataharness/` 只保存可重建的配置副本、PID、日志、
心跳、setup 标记和生命周期验收日志，已被 Git 忽略。Runtime DB、Privacy DB、Project
Workspace 和发布物仍位于既有 `runtime-data` 结构中。

备份 manifest 是版本 `1` 的发布辅助格式；损坏、不支持版本、重复路径、hash 不匹配、
符号链接或非空恢复目标都会拒绝恢复。回滚仅需保留旧数据根，并显式把配置改回旧目录。

## 5. Security and privacy impact

- API、Sandbox 都仅绑定 `127.0.0.1`；部署状态明确声明运行时不需要 Node.js，也不通过
  Docker 运行 API/Worker。
- Sandbox 启动时删除进程环境中的 API Key/secret/token/password/credential/private key
  类变量；实际 `.sandbox.toml` 仅允许 `runtime-data/projects` Host 路径，未允许 Docker
  socket、`.dataharness`、Runtime DB、Privacy DB 或 Host 凭据路径。
- 日志在内存中按敏感环境值替换为 `<redacted>`；PID 命令行只用于进程归属判断，不写入
  人类状态输出。状态页只显示密钥已配置/未配置。
- 备份包含用户业务数据和 Privacy SQLite，因此文档明确要求受控加密存储；脚本自身不
  输出明文 `api_key`，受管 TOML 位于被 Git 忽略的 `.dataharness` 目录。

## 6. Dependency changes

None。未新增 Python、Node.js 或容器运行时依赖；`uv.lock` 和 `web/pnpm-lock.yaml` 仍是
依赖事实源。发布包包含既有 `NOTICE` 与镜像 SBOM/漏洞扫描证据检查，不把短 Notice 伪装为
完整的第三方 License 清单。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `pwsh -NoProfile -File .\scripts\phase13_acceptance.ps1` | `PASS` | 20 项：所有部署入口语法、端口冲突、PID 防误杀、日志脱敏、有界超时、mock status、Runtime/Project/Privacy 备份恢复。 |
| `pwsh -NoProfile -File .\scripts\phase13_lifecycle_acceptance.ps1` | `PASS` | 17 项：密钥/配置 fail-closed、三进程启动、幂等启动、逐个异常定位与重启恢复、安全停止；脱敏 transcript 在 `.dataharness/phase13-lifecycle-acceptance.log`。 |
| `pwsh -NoProfile -File .\setup.ps1 -SkipImageBuild -SkipWebBuild` | `PASS` | Docker、uv、前端构建物、固定 digest、SBOM/漏洞扫描证据和配置检查通过；未配置 API Key 只输出状态。 |
| `uv run dataharness check --config .dataharness\config.toml` | `PASS` | 受管配置通过，Sandbox 网络为 `False`。 |
| `uv lock --check` | `PASS` | 131 个锁定包可复现解析。 |
| `uv run python scripts/release_check.py --require-image` | `PASS` | secure-analysis 镜像与发布证据通过。 |
| `uv run ruff format --check src tests scripts` | `PASS` | 182 files already formatted。 |
| `uv run ruff check src tests scripts` | `PASS` | 无 lint 错误。 |
| `uv run pyright` | `PASS` | 0 errors, 0 warnings, 0 informations。 |
| `uv run pytest -q` | `PASS` | 229 passed；7 个真实 OpenSandbox live 测试因未设置 `DATAHARNESS_LIVE_SANDBOX` 跳过。 |
| `pwsh -NoProfile -File .\status.ps1 -Json`（演练清理后） | `PASS` | 三角色均 `STOPPED`，8000/18080 不可达，无受管残留。 |

## 8. Exit Gate evidence

### 干净 Windows 环境可经 setup/start 打开 WebUI 并完成真实 Agent 分析

部分满足。`setup.ps1`、`start.ps1 -AllowMissingModelKey`、API `/readyz`、OpenSandbox TCP、
Worker `IDLE` 和同源 `web/dist` 均真实运行通过；但本会话没有 API Key，不能把真实 Agent
工具调用、Sandbox 分析和最终证据链标记为已验证。待具备受控 Key 后执行本节“遗留问题”
中的 smoke 流程。

### 任一 API/Worker/OpenSandbox 异常时可定位并恢复或给出终态

部分满足。生命周期脚本真实终止三个已证明归属的 PID，`status.ps1` 都报告相应角色
`STOPPED` 与 `NOT_READY`，随后 `start.ps1` 重启并回到 `READY_WAITING_MODEL_KEY`。没有正在
运行的真实 Agent Run，因此尚未以真实 checkpoint 验证 Worker 重启后的同一 Run 恢复。

### 重复 start 不复制进程，stop 不误杀外部进程且不删除用户数据

满足。生命周期验收比较三角色 PID，确认重复启动不变；静态验收验证命令指纹不匹配拒绝
管理；停止结论与状态输出均显示未执行数据删除。

### 无 Node 运行时、无公网绑定、Sandbox 不暴露 socket/密钥/Runtime/Privacy

满足。setup/status 明确 `node_required_at_runtime=false`；API/Sandbox 固定回环。Sandbox
子进程去除敏感环境变量，配置只允许 Workspace 数据根；现有安全/集成测试和发布检查继续
验证固定 digest、挂载与网络约束。

### 备份恢复、端口冲突、Docker 停止、缺密钥、错误 Sandbox 配置均有证据

部分满足。备份/恢复、端口冲突、缺密钥和错误配置由自动化脚本通过。Docker 未启动的路径
由 `setup.ps1` 中有界 `docker info` fail-closed 检查及运维手册的可复现步骤覆盖；为避免在
共享开发机会话中主动停止 Docker Desktop，未人为关停 daemon 进行破坏性演练。

## 9. Architecture deviations and decisions

None。实现符合 `doc/decision-002-local-agent-application.md`：个人版本保持回环、独立
Host 进程和 Docker-only Sandbox，不引入 Compose、API/Worker 容器化、公共监听、多租户或
第二套事实源。`uvx` 的包装器会留下实际 OpenSandbox 子进程这一 Windows 行为由启动器
显式解析和验证，未改变 Sandbox Adapter 边界。

## 10. Known issues and technical debt

- 真实模型端到端链路未验收：责任人是发布操作者，阻塞本阶段完成状态。需要仅在当前
  PowerShell 会话提供配置指定的 API Key，使用 WebUI 创建项目/文件/Snapshot/Session 并
  提交问题，验证至少一次受控工具调用、Sandbox Step、发布/Verification 和最终回答。
- 真实 Worker checkpoint 恢复未在活跃 Agent Run 上注入故障：责任阶段为 Phase 13 完成
  验收；当前只证明了进程级重启和 idle Worker 心跳恢复。
- Docker daemon 关闭路径未在本会话主动破坏 Docker Desktop：已有 fail-closed 实现和
  文档步骤，后续干净机发布验收应实际执行一次。
- 生命周期 transcript 与 `.dataharness` 状态均为可重建、被忽略的本机证据；正式发布
  流水线应将其作为外部验收 artifact 归档，不提交到仓库。

## 11. Next-phase entry check

不满足进入 Phase 14 的完成前提。Phase 13 必须先完成真实模型 Agent smoke、活跃 Run
checkpoint 恢复和 Docker-stop 演练，随后建立新的 `COMPLETED` addendum 并把计划状态更新
为 `COMPLETED`。在此之前，Phase 14 不开始，以免违反开发计划的顺序 Gate。
