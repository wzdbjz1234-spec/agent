# DataHarness V1 运维手册

本文档是 Phase 10 发布验收的一部分。V1 是本机单用户控制面：API 只绑定回环地址，
Runtime SQLite、每 Task Privacy SQLite、LocalWorkspace 和 OpenSandbox 服务必须位于同一
受控开发环境。普通业务数据可能发送到用户配置的云模型；模型凭据只写入未纳入版本控制的
本地 TOML，不写入 Runtime、Workspace 或日志。

## 发布前检查

在仓库根目录执行：

```powershell
uv lock --check
uv run python scripts/release_check.py --require-image
uv run python scripts/verify.py
```

镜像证据由 `sandbox-images/secure-analysis/build.bat` 生成（等价引擎脚本
`build.ps1`）。构建参数必须是带
`@sha256:<64 位 digest>` 的基础镜像引用；运行服务必须使用
`build-evidence/image-digest.txt` 中的 digest。每次镜像、锁文件或 Skill 正文变化都要
重新生成 SBOM 和漏洞扫描证据。

## 启动与健康检查

1. 启动 Docker Engine，并确认 OpenSandbox 服务监听 `127.0.0.1:18080`。
2. 确认服务端 egress 为 `dns+nft`，网络关闭、非特权用户和三项白名单挂载保持开启。
3. 使用 `dataharness check --config dataharness.example.toml` 校验配置。
4. 使用 `dataharness serve --config dataharness.example.toml` 启动本地 API。
5. 只从本机访问 `/healthz` 和 `/readyz`；V1 没有公网认证、TLS、RBAC、多租户或 Webhook。

真实 OpenSandbox 验收：

```powershell
$env:DATAHARNESS_LIVE_SANDBOX = "1"
uv run pytest -q tests/integration/test_opensandbox_live.py
```

没有真实服务时，普通 `uv run pytest -q` 会明确跳过 live 测试，不把 fake 测试冒充真实
服务验收。

## Phase 13 本机发布包

发布包由一个前台 Python Supervisor 管理三个独立宿主进程：OpenSandbox Server、
DataHarness API 和 DataHarness Worker。Docker 仅被 OpenSandbox 用于创建受控 Sandbox、
execd 与 egress 容器；API 和 Worker 不容器化。发布运行时只消费预构建的 `web/dist`，
不需要 Node.js。

推荐入口是：

```powershell
uv run dataharness run
# 等价：.\.venv\Scripts\python.exe -m dataharness run
```

它在当前窗口显示 `[sandbox]`、`[api]`、`[worker]` 日志，按 `Ctrl+C` 先 drain Worker
再关闭 API 和 OpenSandbox。`.bat` 文件仍作为兼容入口保留；`start.bat` 适合旧流程，
但不再是推荐入口。

首次或升级后执行：

```powershell
# 先在 dataharness.local.toml 的 [model].api_key 填写密钥
.\setup.bat
uv run dataharness run
.\status.bat
```

`setup.bat`（引擎 `setup.ps1`）校验 PowerShell、uv、锁文件、Docker daemon、端口、OpenSandbox 配置、WebUI
构建物、固定镜像 digest、SBOM 和漏洞扫描证据，并生成 `.dataharness/config.toml`。本地
`[model].api_key` 会随受管配置使用，但不会写入部署状态、日志或诊断响应。`dataharness run` 默认要求该
配置项存在；
`-AllowMissingModelKey` 只用于故障验收或 UI 可用性检查，Worker 会进入可诊断的 WAITING
模式，不能被视为真实 Agent 成功。

`status.bat -Json` 是只读自动化诊断：显示 Docker、WebUI、回环端口、Worker 心跳和受管
PID 的归属状态，但只显示密钥“已配置/未配置”。旧 `start.bat` 重复运行只复用启动时间和
命令指纹均匹配的 PID；若 PID 已重用或命令不匹配则拒绝管理。前台 Supervisor 按 `Ctrl+C`
停止；旧的受管后台进程使用：

```powershell
.\stop.bat
```

停止先创建 Worker drain 标记、为活动 Task 写取消意图、等待受管 Worker 收口，再停止
Sandbox 和 API。默认超时会保留服务并提示人工处置；只有显式 `-Force` 才终止已验证归属
的 PID。所有脚本均不删除 Runtime、Privacy、Project 或已发布产物。

### 备份、恢复、升级与卸载

停止服务后，使用校验备份脚本保存完整 Runtime 数据根（含 Runtime SQLite、Project
Workspace 和每 Task Privacy SQLite）：

```powershell
.\backup.bat -DestinationPath "D:\DataHarnessBackups\before-upgrade"
.\restore.bat -BackupPath "D:\DataHarnessBackups\before-upgrade" -DestinationRoot "D:\DataHarnessRestore\runtime-data"
```

备份清单逐文件记录 SHA-256，恢复会先验证所有 hash、拒绝路径穿越，并且只写入一个用户
明确指定的空目录；不会就地覆盖 `runtime-data`。备份含业务数据及 Privacy SQLite，应放入
受控加密介质。恢复完成后，先保留旧数据不变，显式修改配置中的 `runtime_data_root` 指向
恢复目录，再执行 `dataharness check`、`setup.bat` 和 `uv run dataharness run`。

升级顺序是：`Ctrl+C`（或 `stop.bat`）、`backup.bat`、替换发布包、`setup.bat`、
`uv run dataharness run`、`status.bat`。
卸载只允许先执行 `stop.bat`，再由用户按需删除可重建的 `.dataharness` 和程序目录；不要
把删除 `runtime-data` 作为默认步骤。用户数据只应在已验证备份、用户明确确认之后单独处理。

### 常见故障

| 现象 | 可复现诊断与修复 |
|---|---|
| Docker Desktop 未启动 | `setup.bat` 会在 `docker info` 处失败关闭；启动 Docker Desktop 后重试，不要手工启动 API 伪装为完整就绪。 |
| 8000 或 18080 被占用 | `dataharness run`/`setup.bat` 拒绝覆盖；用 `status.bat` 和系统端口工具定位外部进程，改端口或停止该进程。 |
| 模型 API Key 缺失 | `dataharness run` 在启动任何服务前失败；在 `dataharness.local.toml` 的 `[model].api_key` 填写后重新执行 `setup.bat`。 |
| OpenSandbox 配置错误 | `dataharness run --sandbox-config <路径>` 会在连接前拒绝不存在的配置；检查回环监听、Docker backend、`dns+nft` 和最小 `allowed_host_paths`。 |
| 任一服务异常退出 | 前台窗口会显示对应角色退出和日志路径；重新执行 `uv run dataharness run`，Worker 依赖既有 checkpoint/lease 恢复或写入明确终态。 |
| .bat 报缺少 pwsh | `.bat` 是启动器，必须先定位 PowerShell 7：检查 `DATAHARNESS_PWSH` 是否指向存在的 `pwsh.exe`、`where pwsh` 是否命中、`%ProgramFiles%\PowerShell\7\pwsh.exe` 是否存在；仍找不到则按提示安装。 |

## 故障恢复

- API 进程重启：保留 Runtime DB、Privacy DB 和 Workspace 根目录，再重新启动 API/Worker。
  过期 Run lease 由 `LocalDurableExecutor` 回收；恢复仍使用 Run 创建时的 Snapshot。
- OpenSandbox 丢失：旧 lease 不能重连时，按 checkpoint 中已核对的 Snapshot、任务和镜像
  digest 重建 Sandbox；已提交的 AnalysisStep 依靠 Runtime 幂等记录和 Workspace 摘要避免重复发布。
- 取消：先记录取消意图，再终止当前 Run 的 Sandbox，最后清理未发布 staging；已发布的
  Dataset/Artifact 不删除。
- 失败或预算耗尽：自动重试受上限约束；预算耗尽进入 `WAITING/BUDGET_EXHAUSTED`，不会
  生成半成品正式资源。
- Privacy DB 读写失败：隐私出口必须 fail closed。不要把 Privacy DB 合并回 Runtime DB，
  也不要手工编辑占位符映射；应从备份恢复后重试当前 Task。

## 诊断清单

Agent 执行诊断日志会额外记录有界、脱敏的模型思考/回答片段、工具名称与参数、工具结果
摘要、模型错误码、重试和最终失败分类，便于定位 ReAct 链路问题。它不记录 HTTP GET/POST
访问明细，也不记录完整 prompt、完整模型原始回复、完整 stdout/stderr、凭据、PII 原文或
宿主路径；日志中的代码、SQL、回答和结果均经过隐私扫描并限制长度。

确认：

- Runtime DB 与每 Task Privacy DB 位于不同根目录；Sandbox 只有 `/project`、
  `/task/working`、`/task/staging` 三项挂载。
- `build-evidence/image-digest.txt`、SBOM、漏洞扫描结果与实际运行镜像一致。
- Finding 的 `Execution/Integrity/Evidence Gate` 结果和 Coverage 披露已进入最终回答。
- 取消、超时、Sandbox 丢失后没有残留进程，也没有 `STAGED` 但未完成发布的资源。

## V1 非目标与已知限制

V1 不提供 Prefect、AgentFS、Webhook、向量数据库、公网安全边界、通用 shell、任意
Host 执行、生产级 PII 完整识别或真实云账号集成测试。隐私检测是规则型 best-effort；
业务数据是否允许发送到用户配置的云模型由用户配置和部署策略决定。镜像依赖的漏洞若
暂无上游修复，必须在扫描证据中保留风险记录并持续监测，不能删除或伪造扫描结果。
