# DataHarness

DataHarness 是一个本地优先的数据分析 Agent Harness：Project 文件在本地版本化保存，Task/Run/Step 状态写入 Runtime SQLite，生成的 Python/SQL 只允许进入 OpenSandbox，模型请求统一经过 ModelGateway 的 secret/PII 边界。

当前版本是 V1 本地控制面和 Sandbox 验收工程，默认单机、单租户、仅回环地址。它不是公网生产服务，也不是已经打包好的 docker compose up 一键产品。下面的流程以 Windows PowerShell 为例，Linux/macOS 只需要替换路径和环境变量语法。

## 1. 当前可用范围

已实现并验证：

- 本地 FastAPI 控制面：Project、文件导入/版本/检索、Task、事件、Dataset、Artifact、Finding、lineage 和最终 answer 查询；
- Runtime SQLite、每 Task 独立 Privacy SQLite、本地 Project/Task Workspace；
- OpenSandbox Python SDK Adapter、固定 digest 镜像、默认断网、只读 ProjectSnapshot；
- LocalDurableExecutor 的 lease、重试、取消、Host crash 恢复和 Sandbox 重建；
- PydanticAI Agent、ModelGateway、PII placeholder、Finding Verification Gate；
- Phase 11 生产 Agent Harness：受控 prompt、Project-scoped Session、OpenAI-compatible Provider、
  独立 Worker、AgentRunHandler、可恢复 checkpoint、SSE 事件回放和 Vega-Lite 安全校验；
- Phase 12 Local WebUI：React/TypeScript/Vite 工作台、项目/文件/Snapshot/Session/Task 页面、
  TanStack Query 状态恢复、SSE 断线重连、结构化证据结果、Vega-Lite/PNG/SVG 安全渲染、诊断抽屉；
- Phase 13 本机发布包：`setup.bat`、`start.bat`、`stop.bat`、`status.bat`（以及等价的
  `setup.ps1` 等 PowerShell 7 引擎脚本）管理独立的 OpenSandbox/API/Worker，固定镜像证据
  预检、PID 归属保护、Worker 心跳、故障诊断和校验备份/恢复；
- CSV、Parquet、Excel、JSON、PDF、DOCX、PPTX、Markdown、TXT 等受支持格式的导入链路。

当前限制，部署前必须了解：

1. 发布包推荐通过 `uv run dataharness run` 在一个前台 Python Supervisor 中管理 API、Worker 和 OpenSandbox；`start.bat` 仍作为兼容入口保留。
2. 真实模型首发只支持 OpenAI-compatible 协议；API Key 从未纳入版本控制的本地 TOML 配置读取，测试默认使用 fake cloud。
3. V1 不提供公网认证、TLS、RBAC、多租户、Webhook、在线数据库、通用 shell 或运行时安装依赖。

完整运维约束见 [doc/V1_OPERATIONS.md](doc/V1_OPERATIONS.md)，阶段验收证据见 [doc/phase-10-v1-release-20260814.md](doc/phase-10-v1-release-20260814.md)。

## 2. 运行架构

    浏览器/客户端
          │ HTTP，仅 127.0.0.1:8000
          ▼
    FastAPI API（当前可直接启动）
          │
          ├── Runtime SQLite：Task/Run/Step/事件/队列/资源元数据
          ├── Privacy SQLite：每 Task 的 PII 映射，与 Runtime 分离
          ├── LocalWorkspace：Project sources、索引、Task working/staging
          └── ProjectCorpus：提取、Snapshot、FTS5/BM25 检索

    独立的 OpenSandbox Server（127.0.0.1:18080，Docker backend）
          └── secure-analysis@sha256:<digest>
              ├── /project：当前 Snapshot，只读
              ├── /task/working：当前 Task，可写
              └── /task/staging：当前 Task，可写

Runtime DB、Privacy DB、凭据和 Docker socket 不得挂载进 Sandbox。所有生成代码都被视为不可信数据，只能由 OpenSandbox 执行，Host 不使用 exec/eval 执行模型代码。

## 3. 环境要求

发布运行必须安装：

- PowerShell 7、uv、Docker Desktop / Docker Engine；
- 已交付的 Python 环境由 `setup.bat` 根据 `uv.lock` 创建；
- 发布包内的 `web/dist` 已预构建，运行时不要求 Node.js/pnpm；它们仅用于开发期重建前端；
- 真实 Sandbox 验收需要 uvx 能安装 opensandbox-server==0.2.2；真实镜像 SBOM 需要 Docker Scout CLI plugin。

检查环境：

    python --version
    uv --version
    docker version
    docker info

## 4. 获取代码并安装依赖

在仓库根目录 C:\projects\research 执行：

    uv sync --locked

uv.lock 是依赖事实源。不要使用 pip install -r 替代锁文件安装，也不要在 secure-analysis 运行期安装 Python 包。

对于个人本机发布，优先使用下方 Phase 13 的 `setup.bat` 与 `dataharness run`；本节的 `uv sync`
仅用于开发调试。

安装后确认命令可用：

    uv run dataharness --version
    uv run dataharness check

## 5. 准备配置和数据目录

复制配置样例为本地配置。该文件不要提交到 Git；真实 API Key 直接填写在本地 TOML 中。

    Copy-Item .\dataharness.example.toml .\dataharness.local.toml

推荐在 dataharness.local.toml 中至少确认以下内容：

    [paths]
    runtime_data_root = "runtime-data"

    [model]
    provider = "openai"
    model = "gpt-4o-mini"
    api_key = "<你的 API Key>"
    # base_url = "https://api.openai.com/v1"

    [sandbox]
    endpoint = "http://127.0.0.1:18080"
    runtime = "secure-analysis"
    network_enabled = false

路径会派生为：

    runtime-data/runtime.db       Runtime SQLite
    runtime-data/privacy/          每 Task Privacy SQLite
    runtime-data/projects/         Project Workspace
    runtime-data/live-sandbox/     live 测试临时挂载目录

校验配置：

    uv run dataharness check --config .\dataharness.local.toml

注意：当前 model 配置主要用于声明和校验，真实模型 Provider 尚未由 serve 自动装配。

## 6. 构建 secure-analysis 镜像

OpenSandbox 必须使用不可变镜像 digest，不能直接使用 secure-analysis:latest 或未锁定的基础镜像标签。

### 6.1 准备带 digest 的基础镜像

如果本机已经有带 digest 的基础镜像，查询其完整引用：

    docker image inspect dockerproxy.net/library/python:3.12-slim --format '{{index .RepoDigests 0}}'

输出应类似：

    dockerproxy.net/library/python:3.12-slim@sha256:<64 位小写十六进制>

如果没有镜像，先拉取一个受信任镜像源，再重新查询 digest：

    docker pull dockerproxy.net/library/python:3.12-slim

### 6.2 构建并记录 digest

将上一步的完整输出填入 BaseImage：

    .\sandbox-images\secure-analysis\build.bat -BaseImage "dockerproxy.net/library/python:3.12-slim@sha256:<64 位小写十六进制>" -Tag "secure-analysis:1.0.0"

脚本会：

- 拒绝未锁定的基础镜像；
- 安装 sandbox-images/secure-analysis/requirements.lock 中的依赖；
- 删除运行期 pip 和缓存；
- 创建非 root sandbox 用户；
- 写入 sandbox-images/secure-analysis/build-evidence/image-digest.txt。

### 6.3 生成 SBOM 和漏洞扫描证据

build-evidence 被 .gitignore 忽略，因为证据必须绑定实际构建结果。每次镜像或依赖变化都要重新生成：

    docker scout version
    docker scout sbom secure-analysis:1.0.0 --format json | Out-File -Encoding utf8 .\sandbox-images\secure-analysis\build-evidence\sbom.spdx.json

    uv run python .\sandbox-images\secure-analysis\scan_vulns.py .\sandbox-images\secure-analysis\build-evidence\sbom.spdx.json .\sandbox-images\secure-analysis\build-evidence\vuln-scan.json

发布前检查：

    uv run python scripts/release_check.py --require-image

漏洞扫描发现漏洞并不代表脚本失败；必须阅读 vuln-scan.json，记录风险、是否有上游修复以及是否接受风险。不能删除或伪造扫描结果。

## 7. 配置并启动 OpenSandbox Server

OpenSandbox Server 不随本项目的 FastAPI 进程自动启动。它是独立服务，使用 Docker backend 创建分析 Sandbox。

### 7.1 服务配置文件

建议在用户目录创建：

    C:\Users\<用户名>\.sandbox.toml

最少需要确认这些配置：

    [server]
    host = "127.0.0.1"
    port = 18080

    [runtime]
    type = "docker"
    execd_image = "<已验证的 opensandbox/execd 镜像引用>"

    [storage]
    allowed_host_paths = ["C:/projects/research/runtime-data/projects"]

    [docker]
    network_mode = "bridge"
    no_new_privileges = true
    pids_limit = 4096

    [egress]
    mode = "dns+nft"
    image = "<已验证的 opensandbox/egress 镜像引用>"

allowed_host_paths 必须且只能允许 `runtime-data/projects`，不要填写整个 `runtime-data`、用户目录、
根目录或包含凭据的目录；这样 OpenSandbox Server 的策略层面也不能挂载 Runtime/Privacy DB。
dns+nft 很重要：只使用 dns 模式可能允许直连 IP 的非 53 端口流量。

完整配置可参考本机已验证的 $env:USERPROFILE\.sandbox.toml；不要把包含本机路径的用户配置文件提交到仓库。

### 7.2 启动服务

打开一个单独的 PowerShell 窗口执行：

    $env:OPENSANDBOX_INSECURE_SERVER = "YES"
    uvx --from opensandbox-server==0.2.2 opensandbox-server --config "$env:USERPROFILE\.sandbox.toml"

这里的 OPENSANDBOX_INSECURE_SERVER=YES 仅适用于本机自托管、未配置 API Key 的开发服务。共享环境或生产环境必须配置 Sandbox Server API Key，并在客户端配置对应密钥，不要依赖 insecure 模式。

确认服务监听：

    Test-NetConnection 127.0.0.1 -Port 18080

TcpTestSucceeded 必须为 True。

## 8. 启动 DataHarness API

在另一个 PowerShell 窗口执行：

    uv run dataharness serve --config .\dataharness.local.toml --host 127.0.0.1 --port 8000

V1 只允许回环地址；使用公网地址启动会被 CLI 拒绝。检查 API：

    Invoke-RestMethod http://127.0.0.1:8000/healthz
    Invoke-RestMethod http://127.0.0.1:8000/readyz

预期返回：

    {"status":"ok"}
    {"status":"ready"}

当前 API 进程启动时会自动创建/迁移 Runtime SQLite，但不会启动 OpenSandbox、Worker 或真实模型 Provider。
如果已执行 WebUI 构建，``dataharness serve`` 会自动从仓库根目录的 ``web/dist`` 同源托管页面；
生产运行不要求 Node.js。

## 8.1 启动和验收 Local WebUI

开发期在仓库根目录分别启动 API 和 Vite：

    uv run dataharness serve --config .\dataharness.local.toml --host 127.0.0.1 --port 8000
    & "C:\Users\<用户名>\.cache\codex-runtimes\...\pnpm.cmd" --dir web dev

Vite 只代理回环 API、Task SSE 和诊断请求，不承载业务状态。提交问题、刷新页面或 SSE
断线后，页面都会从 API/Runtime 重新读取 Task、Event、Answer 和正式资源，不依赖浏览器内存。

构建同源发布物并检查契约：

    pnpm --dir web install --ignore-scripts
    pnpm --dir web openapi:check
    pnpm --dir web lint
    pnpm --dir web test
    pnpm --dir web build
    uv run dataharness serve --config .\dataharness.local.toml --host 127.0.0.1 --port 8000

Playwright 关键流程：

    pnpm --dir web test:e2e

WebUI 不显示 prompt secret、模型原文、隐藏思考、Host 路径或隐私映射；图表只接受 Host
已校验的 Vega-Lite JSON，并在内容不可渲染时使用正式 PNG/SVG 资源或 fail closed。

## 9. API 最小操作流程

### 9.1 创建 Project

    $project = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/projects -ContentType application/json -Body '{"name":"demo-project"}'
    $project
    $projectId = $project.id

### 9.2 导入文件

API 接收原始 body，文件名通过 X-File-Name 传入：

    @("id,name", "1,alpha", "2,beta") | Set-Content -Path .\demo.csv -Encoding utf8

    curl.exe -X POST "http://127.0.0.1:8000/projects/$projectId/files" -H "X-File-Name: demo.csv" -H "Content-Type: application/octet-stream" --data-binary "@demo.csv"

查询文件和版本：

    Invoke-RestMethod "http://127.0.0.1:8000/projects/$projectId/files"

同名文件再次上传会生成新的 ProjectFileVersion，不会修改旧版本。

### 9.3 创建 Snapshot

当前 API 没有 Snapshot 路由，因此使用同一 Runtime 数据目录通过 Python 调用 ProjectCorpus：

    $snapshotId = uv run python -c "from pathlib import Path; from dataharness.api import ApiService; from dataharness.config import load_settings; from dataharness.domain import ProjectId; s=ApiService.from_settings(load_settings(Path('dataharness.local.toml'))); print(s.corpus.create_snapshot(ProjectId('$projectId')).id)"
    $snapshotId

Snapshot 创建后不可变。Run 必须显式绑定 Snapshot；文件后续更新不会改变旧 Run 的输入视图。

### 9.4 创建 Task/Run

    $taskBody = @{ project_snapshot_id = $snapshotId } | ConvertTo-Json
    $task = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/projects/$projectId/tasks" -ContentType application/json -Body $taskBody
    $task

查询 Task、事件和最终回答：

    $taskId = $task.task.id
    Invoke-RestMethod "http://127.0.0.1:8000/tasks/$taskId"
    Invoke-RestMethod "http://127.0.0.1:8000/tasks/$taskId/events"
    Invoke-RestMethod "http://127.0.0.1:8000/tasks/$taskId/answer"

Task/Run 创建后由独立 Worker 领取并执行；API 重启不会替代 Worker 的耐久恢复边界。

取消、恢复和重试：

    Invoke-RestMethod -Method Post "http://127.0.0.1:8000/tasks/$taskId/cancel"
    Invoke-RestMethod -Method Post "http://127.0.0.1:8000/tasks/$taskId/resume"
    Invoke-RestMethod -Method Post "http://127.0.0.1:8000/tasks/$taskId/retry"

## 10. LLM 配置和真实模型现状

配置样例中的声明是：

    [model]
    provider = "openai"
    model = "gpt-4o-mini"
    api_key = "<你的 API Key>"
    # base_url = "https://api.openai.com/v1"

真实 Provider 从未纳入版本控制的 `dataharness.local.toml` 读取 Key；不要把它写入
Runtime DB、Workspace、提交到 Git 或输出到日志：

    [model]
    api_key = "<你的 API Key>"

兼容 OpenAI API 的网关可以配置：

    [model]
    provider = "openai-compatible"
    model = "你的模型名"
    api_key = "<你的 API Key>"
    base_url = "https://你的网关/v1"

Phase 11 已提供 OpenAI-compatible CloudModelProvider；真实请求入口仍是：

    PydanticAI FunctionModel
      -> ModelGateway
      -> OpenAICompatibleCloudModelProvider

默认测试仍使用 fake cloud；真实 Worker 运行前必须同时配置 API Key、OpenSandbox endpoint 和固定镜像 digest。

## 11. 完整验收流程

### 11.1 不启动真实 Sandbox 的本地验证

    uv lock --check
    uv run python scripts/release_check.py
    uv run python scripts/verify.py
    uv run python scripts/phase10_baseline.py

默认测试会明确跳过真实 OpenSandbox live 测试，不会把 fake Sandbox 冒充真实服务。

### 11.2 启动真实 OpenSandbox 后的验收

确认 OpenSandbox Server 已监听 18080、镜像 digest 证据存在后：

    $env:DATAHARNESS_LIVE_SANDBOX = "1"
    $env:OPEN_SANDBOX_ENDPOINT = "http://127.0.0.1:18080"

    uv run pytest -q tests/integration/test_opensandbox_live.py
    uv run pytest -q tests/e2e/test_phase10_v1.py

真实测试覆盖：

- create、attestation、terminate；
- Python 和 SQL runner；
- Step cancel、超时、输出限制；
- 同 Project 并行 Sandbox 隔离；
- 错误 digest fail-closed；
- AnalysisRuntime 发布、hash、lineage；
- durable Run 恢复和 Sandbox rebuild。

### 11.3 发布前检查

    uv run python scripts/release_check.py --require-image
    git status --short

确认工作树干净，并归档以下构建证据：

    sandbox-images/secure-analysis/build-evidence/image-digest.txt
    sandbox-images/secure-analysis/build-evidence/sbom.spdx.json
    sandbox-images/secure-analysis/build-evidence/vuln-scan.json

这些文件默认被 .gitignore 忽略，发布流水线必须将它们作为与镜像 digest 绑定的构建 Artifact 保存。

## 12. Phase 13 一键本机启动、停止与数据备份

在仓库根目录、Docker Desktop 已启动且 `web/dist` 已随发布包交付时，首次或升级后执行：

    # 先在 dataharness.local.toml 的 [model].api_key 填写密钥
    .\setup.bat
日常启动推荐使用一个前台 Python 进程：

    uv run dataharness run

也可以直接使用已同步的虚拟环境：

    .\.venv\Scripts\python.exe -m dataharness run

它会在当前窗口实时显示 `[sandbox]`、`[api]`、`[worker]` 日志；按 `Ctrl+C` 时先请求
Worker drain，再关闭 API 和 OpenSandbox，不需要另开窗口执行关闭脚本。浏览器访问
`http://127.0.0.1:8000`。

Worker 日志中的 `[agent]` 记录模型的有界思考/回答、工具调用参数、工具结果摘要、模型
错误码和重试信息；内容经过隐私脱敏并限制长度。API 的 GET/POST access log 默认关闭，
避免把前端轮询和静态资源请求淹没在执行诊断中。

`dataharness run` 默认在密钥缺失时 fail closed，不会启动半套服务；仅用于诊断时才可传
`--allow-missing-model-key`，此模式不构成真实 Agent 验收。

`.bat` 文件仍是兼容启动器，只负责定位 PowerShell 7 并调用等价的 `.ps1` 引擎脚本；
推荐入口不再依赖它们。旧的 `start.bat` 会保持控制台窗口打开，停止仍使用 `stop.bat`。
兼容入口的 pwsh 定位顺序：
`DATAHARNESS_PWSH` 环境变量（完整路径）→ PATH 中的 `pwsh` → 标准安装目录
`%ProgramFiles%\PowerShell\7\pwsh.exe` → 开发机回退路径；都找不到时打印安装指引并
退出 1。自动化场景可直接使用 `uv run dataharness run`，日志和生命周期都由同一前台
Python 进程负责。

前台入口停止使用 `Ctrl+C`，不会删除 `runtime-data`；遗留的受管后台进程仍可使用
`.\stop.bat`。备份和恢复使用显式目录：

    .\backup.bat -DestinationPath "D:\DataHarnessBackups\before-upgrade"
    .\restore.bat -BackupPath "D:\DataHarnessBackups\before-upgrade" -DestinationRoot "D:\DataHarnessRestore\runtime-data"

恢复始终写入空目录并核验每个文件的 SHA-256；不会覆盖当前数据根。升级或卸载的安全边界、
Docker/端口/密钥/配置故障排查见 [doc/V1_OPERATIONS.md](doc/V1_OPERATIONS.md)。

## 13. 故障排查

### dataharness check 失败

检查 TOML 格式、路径是否可写，以及 [sandbox].network_enabled 是否为 false。V1 禁止开启 Sandbox 网络。

### .bat 报缺少 pwsh

`.bat` 是启动器，必须能找到 PowerShell 7 才能调用引擎脚本。按顺序检查：是否设置了
`DATAHARNESS_PWSH` 且路径存在、`where pwsh` 是否命中、`%ProgramFiles%\PowerShell\7\pwsh.exe`
是否存在。都找不到时按提示安装 PowerShell 7，或设置 `DATAHARNESS_PWSH` 指向已有
`pwsh.exe`（例如开发机 codex-runtime 内捆绑的 pwsh）。

### API 能启动但 Task 不执行

确认是通过 `dataharness run`（或兼容的 `start.bat`）而不是单独 `dataharness serve` 启动，并运行 `status.bat` 检查 Worker
心跳、OpenSandbox TCP、固定镜像 digest 和模型 Key 状态。密钥缺失时 Worker 会给出明确的
WAITING/MISSING_DEPENDENCY，而不是绕过 ModelGateway 发起调用。

### OpenSandbox 创建失败

按顺序检查：

1. Test-NetConnection 127.0.0.1 -Port 18080；
2. docker info；
3. image-digest.txt 是否与本地 docker image inspect 的 digest 一致；
4. .sandbox.toml 的 allowed_host_paths 是否包含 runtime-data；
5. egress 是否为 dns+nft；
6. execd 和 egress 镜像是否可拉取。

### 模型调用失败

首发生产装配使用 OpenAI-compatible CloudModelProvider；设置 Key 后仍应通过 WebUI 创建
Project、Snapshot、Session 和问题来触发 Worker，不能绕过 ModelGateway 直接调用 SDK。Provider
错误会映射为脱敏的稳定分类。

### 如何判断服务是否健康

    Invoke-RestMethod http://127.0.0.1:8000/healthz
    Invoke-RestMethod http://127.0.0.1:8000/readyz
    Test-NetConnection 127.0.0.1 -Port 18080

API 健康不代表 Worker、OpenSandbox 或真实 LLM 已就绪；三者必须分别检查。

## 14. 相关文档

- [开发计划](doc/DEVELOPMENT_PLAN.md)
- [架构说明](ARCHITECTURE.md)
- [本机 Agent 应用、WebUI 与部署决策](doc/decision-002-local-agent-application.md)
- [配置样例](dataharness.example.toml)
- [V1 运维手册](doc/V1_OPERATIONS.md)
- [Phase 10 发布报告](doc/phase-10-v1-release-20260814.md)
- [secure-analysis 镜像说明](sandbox-images/secure-analysis/README.md)
