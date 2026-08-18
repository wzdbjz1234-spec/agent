# DataHarness V1 Architecture

> 状态：V1 设计基线（Chat-first 修订见 `doc/decision-004-chat-first-agent.md`）
> 更新日期：2026-08-17
> 定位：面向本地数据的 Chat-first Agent 应用；长时分析通过显式 Analysis Job 使用隔离执行

## 1. 产品定位

DataHarness 面向单机、单租户、长期项目资料与本地数据分析。用户将文件导入持久 Project，先在 Conversation 中和 Agent 对话；Agent 按需进行跨文件检索和有界读取，普通回合返回自然语言。只有 Python/SQL、图表发布或长时计算才显式升级为 Analysis Job，由本地 OpenSandbox 执行；项目文件及索引、作业状态、产物、证据与隐私占位映射保存在本机。

项目的准确承诺是：

> DataHarness 通过本地持久化、隔离执行、统一模型出口和敏感信息误传防护，使云端 LLM 能够可控地驱动本地数据分析。

它不承诺“业务数据绝不离开本地”。用户允许 Agent 将读取到的业务数据或分析结果发送给其配置的云模型。系统只对明确凭据实行阻断，并对常见 PII 做尽力而为的可逆占位。

## 2. V1 威胁模型

### 2.1 信任假设

- 宿主操作系统、DataHarness Host 进程和本机管理员可信。
- V1 是单租户系统，不处理互不信任用户之间的隔离。
- 用户 Prompt、上传文件、文件内容、云模型输出、LLM 生成代码、预装 Skill 及其脚本均视为不可信输入。
- 云模型供应商是用户主动选择的数据接收方；普通业务数据允许发送给该供应商。

### 2.2 V1 主要防护目标

- LLM 生成代码不得在 Host 中执行或读取 Host 凭据。
- Sandbox 只能只读访问当前 Run 固定的 ProjectSnapshot，并写当前 Task Workspace，默认不能联网。
- Runtime SQLite 与隐私映射库不得暴露给 Agent 或 Sandbox。
- 密码、API Token、私钥、Cookie、连接串等凭据不得发送给模型。
- 常见 PII 在出境视图中使用当前 Agent scope 内稳定的类型化占位符。
- 长任务可暂停、取消、恢复；崩溃后不依赖进程内状态。
- 最终 Finding 必须绑定可检查的分析证据及实际使用的 ProjectFileVersion。

### 2.3 非目标

- 抵御恶意 root、内核漏洞、敌对管理员或高级侧信道。
- 多租户认证、RBAC、租户级密钥及资源隔离。
- 公网部署、TLS、CORS、反向代理和完整网络服务安全。
- 阻止普通业务数据发送给用户选择的云模型。
- 证明 PII 检测零漏报，或对抗恶意 Skill 主动编码外传。
- 通用结论真伪判定、自动因果推断或完整统计审查。

FastAPI 默认监听 `127.0.0.1`。部署者主动扩大监听范围后的网络安全不属于 V1 承诺。

## 3. 设计原则

### 3.1 Thin Harness

DataHarness 自研领域策略、生命周期、边界协议和组件编排，不重写通用基础设施：

```text
自研                                复用
Domain Model                        PydanticAI Agent Loop / Provider SDK
Task/Run/Step 状态与本地执行器       OpenSandbox Runtime
VirtualWorkspace / Workspace Bridge  DuckDB / pandas / PyArrow / Pandera
ProjectCorpus / Snapshot / Coverage  SQLite FTS5/BM25 / document parsers
Analysis Runtime                    Agent Skills 格式
ModelGateway / Privacy              OpenTelemetry
Publication / Verification / Lineage
```

### 3.2 Host 与 Sandbox 强隔离

```text
TRUSTED HOST
PydanticAI / ModelGateway / Runtime SQLite
Task Worker / Domain Services / Workspace Policy
Credentials / Privacy Mapping
================ SECURITY BOUNDARY ================
UNTRUSTED SANDBOX
LLM-generated Python / SQL
预装 Skill scripts
DuckDB / pandas / PyArrow / Pandera / plotting
```

Host 禁止 `exec`、`eval`、导入或等价执行 Workspace/Skill/模型生成代码。

### 3.3 状态显式持久化

对话历史和 Sandbox 内存都不是事实来源。跨步骤所需数据、代码和中间结果必须写入 Workspace；业务状态与领域元数据必须写入 Runtime SQLite。

### 3.4 少而硬的边界

V1 优先实现可确定验证的约束：执行域、路径范围、凭据出口、状态转换、内容哈希、证据引用、超时和资源限制。Prompt Injection 分类器、复杂隐私预算及自动统计规则不作为核心安全边界。

## 4. 总体架构

```text
User / Local Client
        |
        v
FastAPI (thin API)
        |
        v
ProjectService / TaskService / RunService / LocalDurableExecutor
        |                         |
        |                         +--> Runtime SQLite
        |
        +--> ProjectCorpus --> LocalWorkspace + FTS5/BM25
        v
PydanticAI Agent
   |            |
   |            +--> Context / Compaction / local Skills
   v
ModelGateway -----------------------------> Cloud LLM API
   |  secret block + PII placeholders
   |
   +<---------------- masked model response
        |
        v
Analysis Tools
   | project search / inspect / execute_python / execute_sql
   v
AnalysisRuntime --> AnalysisStep / Dataset / Artifact / Finding / Lineage
        |
        +--> ProjectSnapshot + TaskWorkspace (LocalWorkspaceProvider)
        |
        +--> SandboxProvider (OpenSandboxProvider)
                         |
                         v
                  per-Run Sandbox lease
                  per-Step isolated process
```

依赖方向：

```text
api
 -> orchestration
 -> agent / capabilities / analysis / projects
 -> domain + workspace/sandbox/model protocols
 -> providers / storage
```

`domain` 不依赖 FastAPI、PydanticAI、OpenSandbox、SQLite SDK 或遥测 SDK。第三方 SDK 只能出现在 Provider/Adapter 或明确的装配层。

## 5. 模块职责

### 5.1 `domain/`

定义 Project、ProjectFile、ProjectFileVersion、ProjectSnapshot、ProjectCoverageReport、Session、Task、Run、AnalysisStep、Dataset、Artifact、Finding、Lineage、状态、值对象和领域错误。领域对象不执行 I/O。

### 5.2 `api/`

提供 Project 创建与查询、文件导入与版本查询、项目检索，以及 Task 创建、查询、取消、恢复、事件、Dataset、Artifact 和受控文件访问。API 只做输入校验、服务调用和错误映射。

V1 不提供 Webhook；SSE/WebSocket 可选，但必须复用相同事件流和脱敏规则。

### 5.3 `orchestration/`

负责 Task/Run 生命周期、本地持久队列、Worker lease/heartbeat、取消、恢复、重试和阶段切换。V1 不依赖 Prefect。

### 5.4 `agent/`

装配 PydanticAI Agent、ModelGateway、UsageLimits、Tools、Skills、Checkpoint 与 Compaction。不得实现第二套 Agent Loop。

### 5.5 `projects/`（新增）

实现 ProjectCorpus deep module：项目生命周期、文件版本导入、格式提取、全文索引、ProjectSnapshot、跨文件检索与覆盖报告。对外保持 `import_files/create_snapshot/search/open_resource` 等小型 Interface；路径与索引细节隐藏在 Implementation。

### 5.6 `analysis/`

负责 AnalysisStep、代码/SQL 执行请求、输出发布、Dataset/Artifact 注册、轻量 Verification 和 Lineage。

### 5.7 `workspace/`

定义 Project/Task 文件命名空间、路径策略、WorkspaceBridge 与发布原语。它不承担文件版本、检索或项目覆盖语义。V1 正式实现为受控本地目录；AgentFS 仅是未来可选 Provider，不是依赖。

### 5.8 `sandbox/`

定义 SandboxProvider、Sandbox lease、执行请求和执行结果。V1 唯一正式 Provider 是 OpenSandbox。

### 5.9 `privacy/`

定义 SecretDetector、PIIDetector、PlaceholderStore、PrivacyPolicy、ModelGateway 与隐私审计元数据。它是所有云模型请求的唯一出口。

建议目录：

```text
src/dataharness/privacy/
├── detector.py
├── placeholders.py
├── policy.py
├── gateway.py
└── audit.py
```

### 5.10 `skills/`

只发现管理员预先安装的本地 Agent Skills。支持 `SKILL.md/scripts/references/assets` 渐进加载；不支持运行时下载、安装或自动更新。

### 5.11 `storage/`

Runtime SQLite 是 Project/FileVersion/Snapshot、Task/Run/Step 与领域元数据的事实来源，并承载本地任务队列、lease、事件及幂等记录。它不保存大型文件或隐私原值。

## 6. 事实来源

| 内容 | 唯一事实来源 |
|---|---|
| Project、ProjectFileVersion、ProjectSnapshot 与索引元数据 | Runtime SQLite |
| Task/Run/Step 状态、重试、lease | Runtime SQLite |
| Dataset/Artifact/Finding/Lineage 元数据 | Runtime SQLite |
| Agent 消息与模型步骤 | PydanticAI checkpoint |
| 项目原始文件、提取结果、索引文件、正式项目产物 | Project Workspace |
| 代码、中间结果与 staging | Task Workspace |
| PLAN/PROGRESS/CONTEXT | Task Workspace `/state` 文件 |
| PII 占位映射 | Task 独立 Privacy SQLite |
| Sandbox | 临时计算资源，不是事实来源 |

`RUN.json` 是不可变复现清单，不复制可变业务状态。至少记录 project_id、project_snapshot_id、全部输入 ProjectFileVersion ID/hash、索引版本、模型与设置、Sandbox image digest、Skill 内容 hash、代码 hash、随机种子和隐私映射版本。

## 7. Project Corpus 与 Workspace

### 7.1 目录布局

Project 是长期数据生命周期；Task/Run/Step 是计算生命周期。V1 使用：

```text
runtime-data/projects/{project_id}/
├── sources/{file_id}/{version_id}/  # 原始文件版本，只读、不可变
├── extracted/                       # 本地提取文本、表格元数据
├── indexes/                         # FTS5/BM25 与检索清单
├── datasets/                        # 项目级正式派生数据
├── artifacts/                       # 项目级正式展示产物
├── manifests/                       # 文件与 snapshot 清单
└── tasks/{task_id}/
    ├── working/                     # 当前 Task 中间数据与代码
    ├── staging/{step_id}/           # 当前 Step 待发布输出
    └── state/
        ├── PLAN.md
        ├── PROGRESS.md
        ├── CONTEXT.md
        └── RUN.json
```

约束：

- Host 根据 project_id 创建受控路径；Agent 不能指定或拼接任意 Host 文件夹。
- 输入导入时规范化名称、识别真实格式、拒绝链接/设备/可执行文件并计算 hash。
- 同一逻辑文件的更新创建新的 ProjectFileVersion；禁止覆盖或删除被 Snapshot 引用的版本。
- Project 的 sources/extracted/已发布 datasets 对 Sandbox 只读；Agent 不能覆盖或删除原始文件。
- 所有路径经过规范化和真实路径校验，拒绝 `..`、宿主绝对路径和符号链接逃逸。
- 不同 Task 可读取相同 ProjectSnapshot，但拥有独立 Sandbox、working、staging、Checkpoint、预算和 Step 序列。

### 7.2 文件导入、提取与索引

V1 支持 CSV、Parquet、Excel、JSON、PDF、DOCX、PPTX、Markdown 与纯文本。结构化文件提取 schema、工作表和统计元数据；文档提取带页码、段落或幻灯片定位的文本。图片 OCR、音视频和未知格式登记为 `UNSUPPORTED`，不假装已经分析。

```text
upload
 -> validate real type / size / path
 -> immutable ProjectFileVersion + SHA-256
 -> local extraction
 -> FTS5/BM25 + metadata index
 -> READY | FAILED | UNSUPPORTED
```

提取物和索引均绑定 source hash 与 extractor version，可删除后重建，不替代原始文件事实。

### 7.3 ProjectSnapshot

Task 创建时必须绑定一个 Project；Run 开始前创建不可变 ProjectSnapshot，固定文件版本、索引版本和已发布 Dataset 版本。Run 进行期间的新上传或新版本默认不进入该 Run。崩溃恢复必须使用同一 Snapshot；用户要求使用最新文件时创建新 Run/Snapshot。

### 7.4 跨文件检索与完整覆盖

Agent 有两种显式检索模式：

- `RELEVANT`：使用文件元数据过滤与 FTS5/BM25 找到相关片段，回答只能声称使用了实际引用的文件。
- `FULL_PROJECT`：枚举 Snapshot 中所有受支持文件，分批提取/分析并生成 ProjectCoverageReport。报告记录总数、成功、失败、不支持、跳过及原因；未完整覆盖时不得声称“分析了所有项目文件”。

V1 不需要向量数据库实现跨文件能力。结构化数据优先经 DuckDB 分析，文档使用本地全文检索；未来语义检索可作为独立 SemanticIndexProvider。

## 8. Sandbox 与代码执行

### 8.1 Provider

业务层只依赖 `SandboxProvider`。OpenSandbox SDK 只能出现在 `providers/sandbox/`。

```python
class SandboxProvider(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxLease: ...
    async def connect(self, sandbox_id: str) -> SandboxLease: ...
    async def execute(self, lease: SandboxLease, request: ExecutionRequest) -> ExecutionResult: ...
    async def terminate(self, lease: SandboxLease) -> None: ...
```

创建后必须核验实际运行配置；镜像 digest、网络、挂载或安全运行时不符合 `SandboxSpec` 时 fail closed，禁止静默降级。

### 8.2 生命周期

- 一个 Run 默认复用一个可替换的 Sandbox lease。
- Sandbox 只读挂载当前 Run 的 ProjectSnapshot，并只写当前 Task 的 working 与当前 Step staging；同一 Project 的并行 Run 使用独立 lease。
- 每个 AnalysisStep 在独立进程和独立 `/staging/{step_id}` 中执行。
- 不使用持久 REPL；Step 之间不能依赖 Python 变量、后台进程或 Sandbox 内存。
- 每步结束后清理残留进程；Sandbox 丢失后使用相同镜像 digest 和 Workspace 重建。
- 同一 Run 不允许静默切换镜像 digest。

### 8.3 工具面

V1 使用 PydanticAI 原生工具调用，不采用 CodeMode/Monty。核心工具为：

```text
execute_python
execute_sql
list_project_files
search_project
inspect_project_file
preview_project_table
query_project_tables
get_project_coverage
inspect_output
submit_finding
```

不向模型提供通用 Host Shell、动态安装包、外部 API、浏览器、邮件或在线数据库工具。Sandbox 镜像中的本地程序可由生成代码使用，但权限不超过 Sandbox 本身。

### 8.4 安全配置

- 固定且锁定 digest 的分析镜像；创建请求只携带 `<runtime>@sha256:<digest>`，docker daemon 无法解析未锁定镜像。
- 默认网络完全关闭（deny-all egress 策略，无放行规则），V1 不允许 Agent 临时开放网络。
- Sandbox 无 Host 凭据、Runtime DB、Privacy DB、Docker socket 或额外 Host 路径；Host 路径只存在于部署装配层的 mount resolver，SDK 请求只携带受控 resource 引用。
- CPU、内存、磁盘、进程、执行时间和输出大小均有限制。
- 开发环境可放宽配置，但 `secure-analysis` 是默认配置。

`root_read_only` 的 V1 语义是「根文件系统对执行用户不可写」：官方 OpenSandbox docker
后端不提供只读根挂载，等价保证由非 root `sandbox` 用户（uid 10001）+
`no_new_privileges` + drop capabilities（CapEff=0）提供。`OpenSandboxProvider` 的
attestation 在创建和重连时用有界运行时探测验证：user/uid、NoNewPrivs、CapEff、
根目录可写性、出站网络连通性、三项挂载的存在性与读写性、cgroup 内存上限；
任何一项与 `SandboxSpec` 不符都 fail closed，不静默降级。镜像 digest 在创建时由
docker daemon 对 digest 引用的解析强制，重连时通过与创建元数据（metadata 回写）比对复核。

## 9. 数据入口与数据库边界

V1 只分析导入 Project 的受支持文件及可选 DuckDB/SQLite 快照，不直接读取任意 Host 路径。

- Runtime SQLite 对 Agent/Sandbox 完全不可见。
- V1 不连接外部在线数据库。
- Project 原始 DuckDB/SQLite 快照只读；Sandbox 内属于当前 Task 的临时 DuckDB/SQLite 可自由读写。
- 未来接入在线数据库时应使用独立只读账号、超时、限行和限结果大小的 SQL Tool。

## 10. ModelGateway 与隐私占位

### 10.1 唯一模型出口

主 Agent、Compaction、摘要器、Skill 选择器以及未来辅助模型调用全部必须经过 ModelGateway。业务模块禁止直接调用模型 SDK。

```text
Model Request
 -> Secret/PII scan
 -> BLOCK or placeholder transform
 -> audit metadata
 -> cloud provider
```

只扫描新增内容，并按内容 hash 缓存扫描结果；不使用云模型检测隐私。

### 10.2 默认策略

- 密码、Token、私钥、Cookie、连接串：阻断请求，不建立映射。
- 邮箱、手机号、银行卡、身份证及用户自定义明确规则：当前 Agent scope 内稳定的类型化占位。
- 姓名、自然语言地址等 NER：可选增强，不作为 V1 默认路径。
- 其他业务数据允许发送给云模型。

### 10.3 占位语义

- 占位只修改云端视图，不修改本地 Dataset 或 Workspace。
- 同一 Task 内相同规范化值使用相同占位符；不同 Task 不能关联。
- 模型生成的工具参数和代码在进入 OpenSandbox 前，仅对当前 Task 已登记且类型匹配的占位符受控恢复。
- 返回云端的 stdout/stderr、Tool Result、异常和 Compaction 内容再次执行占位。
- 日志、Trace 和云端消息历史只保存脱敏版本；最终用户展示可按需恢复。

### 10.4 映射存储

```text
runtime-data/privacy/{task_id}.db
```

每个 Task 一个独立 SQLite，跨 Run 共用，随 Task 删除。不进入 Workspace、Sandbox、Runtime DB、日志或 Artifact。V1 依赖本机文件权限，不宣称静态加密。

## 11. Skills

- 只加载管理员预先安装的本地 Skill。
- 未激活 Skill 只暴露名称和描述；激活后加载完整 `SKILL.md`，其他资源按需读取。
- Skill 目录只读，版本以内容 hash 标识并写入 Run manifest。
- Skill 脚本与 LLM 生成代码具有完全相同的 Sandbox 权限。
- 禁止 Host import Skill 代码，禁止 Skill 运行时安装、联网或扩大 Workspace 范围。

## 12. Memory、Checkpoint 与 Compaction

Agent Memory 不依赖向量数据库：

```text
Working state     -> Workspace state files
Task/Run metadata -> Runtime SQLite
Conversation      -> PydanticAI checkpoint
Project retrieval -> metadata + SQLite FTS5/BM25
History search    -> SQLite FTS5/BM25（按需）
```

V1 不提供向量记忆。Project 文件检索属于 ProjectCorpus，不属于 MemoryCapability；面向用户文档的语义检索是未来可选 `SemanticIndexProvider`。

上下文窗口管理复用 PydanticAI/Pydantic AI Harness Compaction。DataHarness 只负责在压缩前持久化当前目标、计划、已完成步骤、ProjectSnapshot/FileVersion、Dataset/Artifact 引用、已验证 Finding 和未解决问题。大型 Tool Result 写入 Workspace，只向模型提供有界内容或引用。

## 13. 状态机

### 13.1 Project 与文件版本

Project 是长期容器，不随单个 Task 结束而删除；归档只禁止新 Task/文件版本，不破坏历史 Snapshot。ProjectFileVersion 的处理状态为：

```python
FileVersionStatus = IMPORTING | READY | FAILED | UNSUPPORTED
```

ProjectSnapshot 记录创建时每个逻辑文件的当前版本及处理状态；只有 `READY` 条目可供检索和挂载，但 `FULL_PROJECT` Coverage 必须列出 FAILED/UNSUPPORTED 条目。ProjectSnapshot 创建后不可变，不提供“更新 Snapshot”操作。

```python
ProjectStatus = ACTIVE | ARCHIVED
CoverageItemStatus = PROCESSED | FAILED | UNSUPPORTED | SKIPPED
```

ARCHIVED Project 不接受新文件版本或新 Task，但不删除既有 Snapshot、运行记录和正式产物。

### 13.2 Task

```text
QUEUED -> ACTIVE -> COMPLETED
             |  
             +-> WAITING -> ACTIVE
             +-> FAILED
             +-> CANCELLED
QUEUED ----------------> CANCELLED
```

```python
TaskStatus = QUEUED | ACTIVE | WAITING | COMPLETED | FAILED | CANCELLED
WaitReason = USER_INPUT | BUDGET_EXHAUSTED | RETRY_APPROVAL | MISSING_DEPENDENCY
```

删除 `SUSPENDED`；统一使用 `WAITING + wait_reason`。

每个分析 Task 必须绑定一个 Project；一个 Task 不能跨 Project 读取数据。跨 Project 分析留作未来显式导入/合并能力。

### 13.3 Run

```python
RunStatus = QUEUED | RUNNING | WAITING | SUCCEEDED | FAILED | CANCELLED
RunPhase = PREPARING | REASONING | EXECUTING | VERIFYING | FINALIZING
```

`status` 表达生命周期，`phase` 表达当前工作。终态 Run 永不重新打开；用户重试创建新的 Run。补充信息或预算从 `WAITING` 恢复同一 Run。

### 13.4 AnalysisStep

```python
StepStatus = PENDING | RUNNING | SUCCEEDED | FAILED | TIMED_OUT | CANCELLED
StepFailureKind = MODEL_CORRECTABLE | RESOURCE_LIMIT | SANDBOX_ERROR |
                  INVALID_OUTPUT | POLICY_DENIED | INTERNAL_ERROR
```

失败 Step 不回到 RUNNING；重试创建新 Step，并通过 `retry_of_step_id` 关联。

### 13.5 Finding

```python
FindingStatus = DRAFT | VERIFIED | WARNING | REJECTED
```

Agent 只能提交 FindingCandidate；只有 Host Verification Gate 能改变正式状态。

## 14. 本地耐久执行

V1 不依赖 Prefect。Runtime SQLite 与 LocalDurableExecutor 提供：

- 原子领取 QUEUED Run；
- `lease_owner/lease_epoch/lease_expires_at/heartbeat_at`；
- Host 崩溃后的 lease 回收；
- 取消、超时、幂等键和有限重试；
- 从最后已提交 Checkpoint 与 Workspace 恢复。

Host/Sandbox 故障恢复同一 Run，并固定使用原 ProjectSnapshot；Agent 修正代码属于同一 Run 的新 Step；只有终态 Run 的用户重试才创建新 Run。

## 15. 取消语义

```text
cancel_requested_at
 -> 停止新的模型/工具调用
 -> 请求终止当前进程
 -> 宽限期后销毁 Sandbox
 -> 取消当前 Step
 -> 清理未提交 staging
 -> Run/Task CANCELLED
```

不增加 `CANCELLING` 状态。已正式发布的 Dataset/Artifact 与审计历史保留；未提交 staging 不发布。取消接口幂等。

## 16. 输出发布与崩溃对账

Sandbox 只能写当前 Step 的 staging。Host 使用可恢复的发布协议：

```text
Sandbox writes staging
 -> validate type/size/hash
 -> write STAGED metadata in SQLite
 -> publish to formal Workspace path
 -> mark AVAILABLE and record lineage
```

使用 `run_id + step_id + output_name` 作为幂等键。Reconciler 处理遗留 staging、已发布但未标 AVAILABLE 的记录，以及记录存在但文件缺失的损坏状态。API 只暴露 AVAILABLE 输出。

## 17. Verification V1

V1 只有三个 Gate：

### ExecutionGate

- 进程正常结束或明确失败；
- 没有未处理的超时/取消/资源耗尽；
- 声明输出存在且位于允许目录。

### IntegrityGate

- 输入 Dataset、代码、镜像、Skill 与输出 hash 完整；
- ProjectSnapshot、ProjectFileVersion、提取器/索引版本及其 hash 完整；
- Artifact/Dataset 注册记录与文件一致；
- Run manifest 信息齐全。

### EvidenceGate

- 每个最终 Finding 是结构化对象；
- 至少引用一个属于当前 ProjectSnapshot 与 Task/Run 的有效证据；
- 证据可追溯到 ProjectFileVersion 的页码/段落/工作表/行范围，或 AnalysisStep、输入 Dataset、代码、Artifact；
- 内容 hash 未变化。

`FULL_PROJECT` 回答还必须绑定 ProjectCoverageReport；存在 FAILED、UNSUPPORTED 或 SKIPPED 项时，最终回答明确披露覆盖缺口。

行数异常变化、Join 膨胀、缺失值、类型转换失败与重复值仅生成 Warning，不自动阻断。V1 不做通用真伪判断、自动统计检验、因果判断或 LLM 自审。

## 18. 预算与失控控制

职责分层：

| 层 | 负责限制 |
|---|---|
| PydanticAI UsageLimits | 模型请求数、输入/输出/总 Token |
| DataHarness | AnalysisStep 数、连续失败、相同失败调用、Run 总时长 |
| OpenSandbox | CPU、内存、磁盘、单步超时、输出大小 |

相同工具名、规范化参数、输入 hash 和环境 digest 连续失败时触发熔断。正常预算耗尽进入 `WAITING(BUDGET_EXHAUSTED)`；不得无限自动重试。

## 19. Observability

默认使用 OpenTelemetry Adapter，记录：

```text
trace_id / task_id / run_id / step_id / tool_call_id / sandbox_id
```

- 默认只记录脱敏内容、大小、hash、状态、耗时和错误分类。
- Prompt、Tool Result、stdout/stderr、异常和模型响应先经过隐私处理。
- 原始输入和隐私映射不进入 Trace。
- 调试模式必须显式开启；凭据即使在调试模式也阻断。
- MLflow 不是 V1 依赖，未来可作为可选分析评估 Adapter。

## 20. V1 API

```text
POST /projects
GET  /projects
GET  /projects/{id}
POST /projects/{id}/files
GET  /projects/{id}/files
GET  /projects/{id}/files/{file_id}/versions
GET  /projects/{id}/search
GET  /projects/{id}/datasets
GET  /projects/{id}/artifacts
POST /projects/{id}/tasks
GET  /tasks/{id}
POST /tasks/{id}/cancel
POST /tasks/{id}/resume
POST /tasks/{id}/retry
GET  /tasks/{id}/events
GET  /tasks/{id}/artifacts
GET  /tasks/{id}/datasets
```

SSE/WebSocket 流式接口可选。V1 不提供 Webhook 和外部触发器。

## 21. 开源项目复用与参考

### 正式依赖或首选实现

| 能力 | 项目 | 用法 |
|---|---|---|
| Agent Loop、工具、结构化输出、Usage | [PydanticAI](https://github.com/pydantic/pydantic-ai) | 正式依赖 |
| Skills、Compaction 等能力 | [Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness) | 按需复用；不采用 CodeMode |
| Sandbox 平台 | [OpenSandbox](https://github.com/opensandbox-group/OpenSandbox) | V1 唯一 SandboxProvider |
| 数据计算 | [DuckDB](https://github.com/duckdb/duckdb)、pandas、PyArrow | Sandbox 内执行 |
| 项目全文检索 | SQLite FTS5/BM25 | 本地跨文件检索，不引入向量数据库 |
| 文档提取 | pypdf、python-docx、python-pptx、openpyxl | 本地生成可定位提取物 |
| DataFrame 约束 | [Pandera](https://github.com/unionai-oss/pandera) | 轻量验证 |
| PII 检测 | [Microsoft Presidio](https://github.com/microsoft/presidio) | 检测器参考/可选依赖 |
| 凭据规则 | [Gitleaks](https://github.com/gitleaks/gitleaks)、[detect-secrets](https://github.com/Yelp/detect-secrets) | 规则与测试语料参考 |
| 遥测 | [OpenTelemetry Python](https://github.com/open-telemetry/opentelemetry-python) | Adapter |
| API | [FastAPI](https://github.com/fastapi/fastapi) | 薄 API |

### 重点参考但不原样依赖

| 项目 | 参考内容 |
|---|---|
| [MemPrivacy](https://github.com/MemTensor/MemPrivacy) | 本地检测、类型化占位、云端处理、本地恢复；不复制其全局明文 SQLite 设计 |
| [LLM Guard](https://github.com/protectai/llm-guard) | Anonymize → model → Deanonymize 生命周期；仓库已归档，不作核心依赖 |
| [AgentFS](https://github.com/tursodatabase/agentfs) | Workspace 审计、快照、可移植思想；V1 使用本地目录 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Checkpoint、interrupt、幂等恢复和调用限制语义 |
| [Bytebase DBHub](https://github.com/bytebase/dbhub) | 未来只读 SQL Tool、超时和限行设计 |
| [gVisor](https://github.com/google/gvisor)、[Firecracker](https://github.com/firecracker-microvm/firecracker) | OpenSandbox 的增强隔离后端 |
| [OpenLineage](https://github.com/OpenLineage/OpenLineage) | 血缘概念与兼容事件结构，不要求 V1 部署服务 |

### 明确不进入 V1 的组件

- Prefect：不需要第二套工作流状态与重试系统。
- AgentFS：V1 不需要 SQLite/FUSE Workspace 层。
- CodeMode/Monty：不需要第二层 Python 编排沙箱。
- 向量数据库：不用于 Agent Memory；语义检索留作未来可插拔能力。
- Great Expectations、Soda、Evidently、MLflow：V1 验证与观测暂不需要其完整平台。

## 22. V1 验收链路

```text
创建 Project 并导入多个受支持文件
 -> 生成不可变 ProjectFileVersion
 -> 本地提取并建立 FTS5/BM25 索引
 -> 创建 Task/Run 并固定 ProjectSnapshot
 -> LocalDurableExecutor 领取 Run
 -> 创建独立 OpenSandbox lease，只读挂载 Snapshot
 -> PydanticAI 经 ModelGateway 调用云模型
 -> 凭据阻断、PII 占位
 -> RELEVANT 检索或 FULL_PROJECT 全量枚举
 -> execute_python / execute_sql
 -> 每步独立进程与 staging
 -> 发布 Dataset/Artifact，记录 hash 与 lineage
 -> 更新 PLAN/PROGRESS/CONTEXT
 -> Context Compaction
 -> 注入 Host 崩溃并恢复同一 Run
 -> 重建 Sandbox
 -> Execution/Integrity/Evidence Gate
 -> 输出结构化 Finding 与用户回答
```

必须证明：

- 生成代码从未在 Host 执行；
- Runtime DB、Privacy DB 和凭据从未进入 Sandbox；
- 凭据未越过 ModelGateway；
- PII 占位不修改本地原始数据，并能在当前 Agent scope 内稳定恢复；
- Project 原始文件版本不可变，新上传生成新版本；
- Run 恢复始终使用相同 ProjectSnapshot；
- 两个 Task 可并行分析同一 Project，且 Sandbox、working、staging 和取消互不影响；
- RELEVANT 回答披露实际使用的文件版本；FULL_PROJECT 回答具有覆盖报告并披露缺口；
- Host 重启后已完成 Step 不重复执行；
- 每个 VERIFIED Finding 有有效证据链；
- 取消与预算耗尽不会留下运行进程或发布半成品。

达到这些条件后，DataHarness V1 才可称为：

> A controllable, local-first, sandboxed, long-horizon data analysis agent harness using cloud LLM APIs.

## 23. 本机应用化目标（Plan 1.2）

Phase 00–10 的 V1 验收证明核心 Harness、控制面和安全边界可组合，但当前 `dataharness serve` 尚未装配真实模型、Worker 和用户界面。Plan 1.2 在不改写历史验收事实的前提下增加生产应用闭环，详细决策见 `doc/decision-002-local-agent-application.md`。

目标运行链路：

```text
WebUI 提交 Project-scoped Session 问题
 -> API 创建 Task/Run 并固定 ProjectSnapshot
 -> 独立 Worker 通过 LocalDurableExecutor 领取 Run
 -> AgentRunHandler 装配 ModelGateway、PydanticAI Agent 与 AnalysisRuntime
 -> Agent 使用窄工具检索文件，在 OpenSandbox 执行 Python/SQL
 -> Host 发布 Dataset/Artifact/ChartArtifact/Lineage
 -> Verification Gate 验证 Finding
 -> Runtime 事件通过 SSE 投影到 WebUI
 -> 最终 answer 只引用正式资源和稳定证据
```

### 23.1 单 Agent 与 Host Harness

- 只保留一个 PydanticAI Agent；不引入 Planner、Executor 或 Reviewer Agent。
- Agent 负责问题理解和工具选择；Host 负责 Task/Run 状态、预算、checkpoint、发布、验证、错误分类和恢复。
- 首个生产模型 Adapter 使用 OpenAI-compatible 协议，但模型请求、响应、摘要和 compaction 仍必须经过 ModelGateway。
- Agent 在预算内自主执行；歧义、输入缺失、策略阻断、预算耗尽或越界请求必须进入带稳定原因的 WAITING。

### 23.2 Session 与上下文

- Session 固定属于单一 Project，每条用户消息创建一个 Task，每个 Task 固定独立 ProjectSnapshot。
- 当前消息、结构化 goal/plan/progress、稳定领域引用和长期历史分层保存；ProjectCorpus 事实不得复制为对话记忆。
- 历史检索必须同时受 Project 与 Session 约束，不允许跨 Project 命中。
- checkpoint 在关键 AnalysisStep、发布、验证、WAITING、完成和 compaction 边界保存；摘要不是事实来源。

### 23.3 事件接口

- 用户操作使用普通 HTTP，任务状态使用 SSE；不引入 WebSocket。
- SSE 事件来自 Runtime SQLite 的有序事件事实，支持按最后事件序号断线补发。
- 事件可披露工具名称、稳定 ID、状态、耗时、大小和脱敏摘要，不披露隐藏思考过程、凭据、PII 原值或无界 stdout/stderr。

## 24. 本机 WebUI

WebUI 使用 React、TypeScript、Vite、Ant Design、TanStack Query、React Router 和 Vega-Lite。开发期使用 Vite；发布时由 FastAPI 在回环地址同源托管预构建资源。

MVP 页面限定为：

```text
/projects
/projects/{project_id}
/projects/{project_id}/sessions/{session_id}
/tasks/{task_id}
```

页面覆盖 Project、文件版本、Session 对话、Task 进度、Dataset、Artifact、Finding、证据、lineage 和最终回答。诊断抽屉只显示 Docker、OpenSandbox、Worker、模型配置、镜像和数据目录状态，不读取或回显 API Key。

### 24.1 图表安全边界

- Agent 输出 Dataset 引用和声明式 Vega-Lite JSON，不输出可执行 HTML 或 JavaScript。
- Host 在发布前校验 schema、Dataset ID/hash、大小、变换和外部数据源；前端只渲染通过 Gate 的正式 ChartArtifact。
- 禁止外部 URL、iframe、任意函数和未发布数据引用。
- PNG/SVG Artifact 作为失败兜底、报告导出和长期复现格式。

## 25. 本机部署拓扑

个人版要求 Docker Desktop 和 uv，通过脚本管理三个独立宿主进程：

```text
start.bat（启动器；引擎为 start.ps1）
  |- OpenSandbox Server :18080
  |- DataHarness API    :8000（同源托管 WebUI）
  `- DataHarness Worker      （按需创建 Sandbox 容器）
```

- `setup.bat`、`start.bat`、`stop.bat`、`status.bat` 是薄启动器，负责定位 pwsh 并调用等价的
  `setup.ps1`、`start.ps1`、`stop.ps1`、`status.ps1` 引擎脚本；`setup.ps1` 负责依赖、配置、
  镜像和端口预检，`start.ps1`、`stop.ps1`、`status.ps1` 负责幂等进程生命周期与诊断。
- Docker 只运行 Sandbox、execd 和 egress；API 与 Worker 不因个人版本而强制容器化。
- PID、日志、配置和运行数据使用明确目录；停止或卸载默认不删除用户数据。
- API Key 只从未纳入版本控制的本地 TOML 配置读取，不进入仓库、Workspace、日志、前端或 Sandbox。
- 最终用户不需要 Node.js；前端静态构建随发布包交付。

## 26. 团队版迁移边界

后续团队内部平台必须重新建立共享环境威胁模型，并补齐认证、RBAC、租户隔离、TLS、CSRF/CORS、密钥管理、配额、审计和高可用。个人版的回环地址、单用户文件权限、SQLite 和本地 TOML 密钥假设不得直接沿用。

迁移时保持 Domain、AgentRunHandler、API DTO 和 WebUI 稳定，通过 Adapter 逐步替换 Runtime SQLite、LocalWorkspace、本机 Worker 和脚本进程管理。候选目标为 PostgreSQL、对象存储、耐久队列、独立容器镜像和反向代理；这些在 Phase 14 完成前均不是已实现能力。
