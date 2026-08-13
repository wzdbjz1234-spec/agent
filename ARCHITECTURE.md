# DataHarness V1 Architecture

> 状态：V1 设计基线  
> 更新日期：2026-08-13  
> 定位：云端 LLM 驱动、本地持久化与隔离执行的数据分析 Agent Harness

## 1. 产品定位

DataHarness 面向单机、单租户、长时运行的数据分析任务。云端 LLM 负责理解目标、规划、生成 Python/SQL、观察结果与组织回答；真实代码在本地 OpenSandbox 中执行；任务状态、数据文件、产物、证据与隐私占位映射保存在本机。

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
- Sandbox 只能访问当前 Task 的 Workspace，默认不能联网。
- Runtime SQLite 与隐私映射库不得暴露给 Agent 或 Sandbox。
- 密码、API Token、私钥、Cookie、连接串等凭据不得发送给模型。
- 常见 PII 在出境视图中使用 Task 内稳定的类型化占位符。
- 长任务可暂停、取消、恢复；崩溃后不依赖进程内状态。
- 最终 Finding 必须绑定可检查的分析证据。

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
TaskService / RunService / LocalDurableExecutor
        |                         |
        |                         +--> Runtime SQLite
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
   | execute_python / execute_sql / workspace operations
   v
AnalysisRuntime --> AnalysisStep / Dataset / Artifact / Finding / Lineage
        |
        +--> VirtualWorkspace (LocalWorkspaceProvider)
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
 -> agent / capabilities / analysis
 -> domain + workspace/sandbox/model protocols
 -> providers / storage
```

`domain` 不依赖 FastAPI、PydanticAI、OpenSandbox、SQLite SDK 或遥测 SDK。第三方 SDK 只能出现在 Provider/Adapter 或明确的装配层。

## 5. 模块职责

### 5.1 `domain/`

定义 Session、Task、Run、AnalysisStep、Dataset、Artifact、Finding、Lineage、状态、值对象和领域错误。领域对象不执行 I/O。

### 5.2 `api/`

提供创建、查询、取消、恢复 Task 以及事件、Dataset、Artifact、Workspace 文件访问。API 只做输入校验、服务调用和错误映射。

V1 不提供 Webhook；SSE/WebSocket 可选，但必须复用相同事件流和脱敏规则。

### 5.3 `orchestration/`

负责 Task/Run 生命周期、本地持久队列、Worker lease/heartbeat、取消、恢复、重试和阶段切换。V1 不依赖 Prefect。

### 5.4 `agent/`

装配 PydanticAI Agent、ModelGateway、UsageLimits、Tools、Skills、Checkpoint 与 Compaction。不得实现第二套 Agent Loop。

### 5.5 `analysis/`

负责 AnalysisStep、代码/SQL 执行请求、输出发布、Dataset/Artifact 注册、轻量 Verification 和 Lineage。

### 5.6 `workspace/`

定义 VirtualWorkspace 与 WorkspaceBridge。V1 正式实现为受控本地目录；AgentFS 仅是未来可选 Provider，不是依赖。

### 5.7 `sandbox/`

定义 SandboxProvider、Sandbox lease、执行请求和执行结果。V1 唯一正式 Provider 是 OpenSandbox。

### 5.8 `privacy/`（新增）

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

### 5.9 `skills/`

只发现管理员预先安装的本地 Agent Skills。支持 `SKILL.md/scripts/references/assets` 渐进加载；不支持运行时下载、安装或自动更新。

### 5.10 `storage/`

Runtime SQLite 是 Task/Run/Step 与领域元数据的事实来源，并承载本地任务队列、lease、事件及幂等记录。它不保存大型文件或隐私原值。

## 6. 事实来源

| 内容 | 唯一事实来源 |
|---|---|
| Task/Run/Step 状态、重试、lease | Runtime SQLite |
| Dataset/Artifact/Finding/Lineage 元数据 | Runtime SQLite |
| Agent 消息与模型步骤 | PydanticAI checkpoint |
| 原始数据、代码、中间结果、正式产物 | Task Workspace |
| PLAN/PROGRESS/CONTEXT | Workspace `/state` 文件 |
| PII 占位映射 | Task 独立 Privacy SQLite |
| Sandbox | 临时计算资源，不是事实来源 |

`RUN.json` 是不可变复现清单，不复制可变业务状态。至少记录模型与设置、Sandbox image digest、Skill 内容 hash、输入/代码 hash、随机种子和隐私映射版本。

## 7. Workspace

V1 每个 Task 使用一个受控本地目录：

```text
runtime-data/tasks/{task_id}/
├── inputs/       # 原始输入，只读、不可变
├── working/      # 中间数据与代码
├── staging/      # 当前 Step 待发布输出
├── datasets/     # 正式派生数据
├── artifacts/    # 正式展示产物
└── state/
    ├── PLAN.md
    ├── PROGRESS.md
    ├── CONTEXT.md
    └── RUN.json
```

约束：

- 输入导入时规范化名称、识别真实格式、拒绝链接/设备/可执行文件并计算 hash。
- `/inputs` 对 Sandbox 只读；Agent 不能覆盖或删除原始文件。
- 所有路径经过规范化和真实路径校验，拒绝 `..`、宿主绝对路径和符号链接逃逸。
- Workspace 是 Task 级资源；不同 Run 复用已发布数据，但拥有独立 Checkpoint、预算和 Step 序列。

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
- 每个 AnalysisStep 在独立进程和独立 `/staging/{step_id}` 中执行。
- 不使用持久 REPL；Step 之间不能依赖 Python 变量、后台进程或 Sandbox 内存。
- 每步结束后清理残留进程；Sandbox 丢失后使用相同镜像 digest 和 Workspace 重建。
- 同一 Run 不允许静默切换镜像 digest。

### 8.3 工具面

V1 使用 PydanticAI 原生工具调用，不采用 CodeMode/Monty。核心工具为：

```text
execute_python
execute_sql
list_workspace
read_text
inspect_output
submit_finding
```

不向模型提供通用 Host Shell、动态安装包、外部 API、浏览器、邮件或在线数据库工具。Sandbox 镜像中的本地程序可由生成代码使用，但权限不超过 Sandbox 本身。

### 8.4 安全配置

- 固定且锁定 digest 的分析镜像。
- 默认网络完全关闭，V1 不允许 Agent 临时开放网络。
- Sandbox 无 Host 凭据、Runtime DB、Privacy DB、Docker socket 或额外 Host 路径。
- CPU、内存、磁盘、进程、执行时间和输出大小均有限制。
- 开发环境可放宽配置，但 `secure-analysis` 是默认配置。

## 9. 数据入口与数据库边界

V1 只分析导入 Workspace 的文件：CSV、Parquet、Excel、JSON，以及可选的 DuckDB/SQLite 快照。

- Runtime SQLite 对 Agent/Sandbox 完全不可见。
- V1 不连接外部在线数据库。
- Sandbox 内属于当前 Task 的 DuckDB/SQLite 可自由读写，用于临时和派生分析。
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
- 邮箱、手机号、银行卡、身份证及用户自定义明确规则：Task 内稳定的类型化占位。
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
History search    -> SQLite FTS5/BM25（按需）
```

V1 不提供向量记忆。面向用户文档的语义检索是未来可选 `SemanticIndexProvider`，不属于 MemoryCapability。

上下文窗口管理复用 PydanticAI/Pydantic AI Harness Compaction。DataHarness 只负责在压缩前持久化当前目标、计划、已完成步骤、Dataset/Artifact 引用、已验证 Finding 和未解决问题。大型 Tool Result 写入 Workspace，只向模型提供有界内容或引用。

## 13. 状态机

### 13.1 Task

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

### 13.2 Run

```python
RunStatus = QUEUED | RUNNING | WAITING | SUCCEEDED | FAILED | CANCELLED
RunPhase = PREPARING | REASONING | EXECUTING | VERIFYING | FINALIZING
```

`status` 表达生命周期，`phase` 表达当前工作。终态 Run 永不重新打开；用户重试创建新的 Run。补充信息或预算从 `WAITING` 恢复同一 Run。

### 13.3 AnalysisStep

```python
StepStatus = PENDING | RUNNING | SUCCEEDED | FAILED | TIMED_OUT | CANCELLED
StepFailureKind = MODEL_CORRECTABLE | RESOURCE_LIMIT | SANDBOX_ERROR |
                  INVALID_OUTPUT | POLICY_DENIED | INTERNAL_ERROR
```

失败 Step 不回到 RUNNING；重试创建新 Step，并通过 `retry_of_step_id` 关联。

### 13.4 Finding

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

Host/Sandbox 故障恢复同一 Run；Agent 修正代码属于同一 Run 的新 Step；只有终态 Run 的用户重试才创建新 Run。

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
- Artifact/Dataset 注册记录与文件一致；
- Run manifest 信息齐全。

### EvidenceGate

- 每个最终 Finding 是结构化对象；
- 至少引用一个属于当前 Task/Run 的有效证据；
- 证据可追溯到 AnalysisStep、输入 Dataset、代码或 Artifact；
- 内容 hash 未变化。

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
POST /tasks
GET  /tasks/{id}
POST /tasks/{id}/cancel
POST /tasks/{id}/resume
POST /tasks/{id}/retry
GET  /tasks/{id}/events
GET  /tasks/{id}/artifacts
GET  /tasks/{id}/datasets
GET  /tasks/{id}/files
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
导入 CSV/Parquet/Excel/JSON
 -> 创建 Task/Run/Workspace
 -> LocalDurableExecutor 领取 Run
 -> 创建 OpenSandbox lease
 -> PydanticAI 经 ModelGateway 调用云模型
 -> 凭据阻断、PII 占位
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
- PII 占位不修改本地原始数据，并能在 Task 内稳定恢复；
- 原始输入不可变；
- Host 重启后已完成 Step 不重复执行；
- 每个 VERIFIED Finding 有有效证据链；
- 取消与预算耗尽不会留下运行进程或发布半成品。

达到这些条件后，DataHarness V1 才可称为：

> A controllable, local-first, sandboxed, long-horizon data analysis agent harness using cloud LLM APIs.
