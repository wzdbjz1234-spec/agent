# DataHarness 开发规范

## 架构目标

DataHarness 是云端 LLM 驱动、本地持久化与隔离执行的单机单租户数据分析 Agent Harness。项目实现领域状态、边界协议、分析运行时、隐私模型出口、Workspace 与组件编排；复用 PydanticAI、OpenSandbox、DuckDB、Pandera 和 OpenTelemetry 等上游机制。

## 技术栈与依赖基线

### 语言、工程与质量工具

| 类别 | 选择 | 项目用途 |
|---|---|---|
| 语言 | Python 3.12+ | Host、领域模型、Provider、测试与 Sandbox 分析脚本 |
| 包与环境 | uv + `pyproject.toml` + `uv.lock` | 依赖解析、锁定、可复现开发和构建 |
| 数据建模 | Pydantic 2 | DTO、配置、领域值对象及结构化模型输出校验 |
| 静态质量 | Ruff + Pyright | 格式、lint、导入约束与类型检查 |
| 测试 | pytest + pytest-asyncio + Hypothesis | 单元、异步、状态机和性质测试 |
| 覆盖率 | coverage.py/pytest-cov | 统计关键接口和安全分支覆盖 |

除 Python 最低版本外，本文件不写漂移的精确版本号；所有直接和间接 Python 依赖以 `uv.lock` 为准。升级依赖必须单独提交，记录兼容性、License、漏洞扫描和回归测试结果。

### 运行时技术栈

| 模块或 seam | 正式技术/Adapter | 用途与约束 |
|---|---|---|
| Agent | PydanticAI | 模型循环、原生工具、结构化输出、UsageLimits、checkpoint/compaction 集成 |
| HTTP | FastAPI + Uvicorn | 本地薄 HTTP 层，默认只监听 `127.0.0.1` |
| 模型出口 | PydanticAI Model/Provider + DataHarness ModelGateway | 所有云模型请求的唯一出口；先做 secret block 和 PII placeholder |
| Sandbox | OpenSandbox Python SDK | 唯一正式 Sandbox Adapter；固定镜像 digest、默认断网、Task-scoped 挂载 |
| 控制面存储 | Python `sqlite3` + 有序 SQL migrations | Runtime 状态、事件、队列、lease、幂等键和领域元数据 |
| 隐私映射 | 独立 SQLite | 每 Task 的占位映射；与 Runtime DB、Workspace、Sandbox 物理分离 |
| Workspace | 本地文件系统 Adapter | 只读 inputs、working、staging、datasets、artifacts、state |
| 分析引擎 | DuckDB + pandas + PyArrow | Sandbox 内 SQL、表格计算与 Parquet/Arrow 交换 |
| 文件读取 | openpyxl 等按格式最小依赖 | Excel 等受支持输入格式；不得在运行时安装 |
| 数据校验 | Pandera | DataFrame/schema 的轻量约束和 warning |
| 隐私检测 | 内建确定性规则；Presidio 可选 Adapter | V1 优先 secret 规则与常见 PII，占位策略参考 MemPrivacy |
| Secret 规则 | Gitleaks/detect-secrets 规则语料 | 用于规则设计、测试集和开发期扫描，不替代 ModelGateway |
| 遥测 | OpenTelemetry Python SDK | 脱敏后的 trace、metric、log 关联 |
| Skills | 本地 Agent Skills 格式 | 只加载管理员预装、内容 hash 固定、Sandbox 内执行 |

V1 不依赖 Prefect、AgentFS、CodeMode/Monty、向量数据库、在线数据库客户端、Webhook、MLflow、Great Expectations、Soda 或 Evidently。

### 源码模块清单

| 模块 | 核心职责 | 对外 Interface / 主要对象 |
|---|---|---|
| `domain` | 纯领域模型、状态机、不变量和错误 | Session、Task、Run、AnalysisStep、Dataset、Artifact、Finding、Lineage |
| `storage` | Runtime SQLite repository、事务、迁移和本地队列 | Repository、UnitOfWork、lease/CAS 操作 |
| `workspace` | Task 文件命名空间、路径策略和发布协议 | VirtualWorkspace、WorkspaceBridge、ResourceRef |
| `privacy` | Secret/PII 检测、占位映射和唯一模型出口 | ModelGateway、PrivacyPolicy、PlaceholderStore |
| `sandbox` | 隔离执行的稳定 seam | SandboxProvider、SandboxSpec、ExecutionRequest/Result |
| `providers` | 第三方和基础设施 Adapter | OpenSandbox、LocalWorkspace、OpenTelemetry、LocalDurable Adapter |
| `analysis` | Step 执行、输出注册、发布、验证和血缘协调 | AnalysisRuntime、FindingCandidate |
| `capabilities` | 面向 Agent 的窄工具能力 | execute_python/sql、workspace、artifact、lineage、memory |
| `skills` | 预安装 Skill 的发现、渐进加载与 hash 固定 | SkillRegistry、SkillDescriptor |
| `agent` | PydanticAI 装配、工具、预算、checkpoint 和 compaction | Agent factory/run facade |
| `orchestration` | Task/Run 生命周期、worker lease、取消和恢复 | TaskService、RunService、LocalDurableExecutor |
| `hooks` | 模型和工具生命周期的观察与收紧 | 隐私、安全、预算和遥测 Hook |
| `api` | 本地 HTTP 输入校验、DTO 和错误映射 | FastAPI routes，不直接访问基础设施 SDK |

模块要做成 deep module：调用方和测试只需理解一个小而稳定的 Interface，复杂策略留在 Implementation 内。只有存在生产 Adapter 与测试 Adapter 等真实变化时才建立 seam；测试以 Interface 的可观察结果为准，不穿透实现细节。

## 不可突破的边界

- LLM 生成的 Python/SQL、Skill 脚本和文件内代码一律视为不可信，只能在 OpenSandbox 中执行；Host 禁止 `exec`、`eval`、import 或等价动态执行。
- Runtime SQLite、Task Privacy SQLite、Host 凭据、Docker socket 和非 Task 路径不得暴露给 Agent 或 Sandbox。
- 所有云模型请求必须经过统一 ModelGateway。凭据命中即阻断；常见 PII 使用 Task 内稳定占位；其他业务数据允许发送给用户配置的云模型。
- Sandbox 默认断网，使用固定 image digest 和 Task-scoped 挂载。V1 不提供运行时装包、外部 API、浏览器、邮件或在线数据库工具。
- 原始输入位于只读 `/inputs`，Agent 只能在 `/working` 和当前 Step 的 `/staging` 写派生文件。
- 对话、Sandbox 内存和自然语言摘要不是事实来源。Task/Run/Step 与领域元数据写入 Runtime SQLite；文件写入 Workspace；模型步骤写入 PydanticAI checkpoint。
- 每个正式 Finding 必须是结构化对象，并通过 Execution、Integrity、Evidence Gate。

## 模块与依赖方向

`api -> orchestration -> agent/capabilities/analysis -> domain + boundary protocols -> providers/storage`

- `domain` 是纯领域层，不依赖 FastAPI、PydanticAI、OpenSandbox、SQLite SDK 或遥测 SDK。
- `agent` 只装配 PydanticAI、ModelGateway、Tools、Skills、UsageLimits、Checkpoint 和 Compaction，不实现第二套 Agent Loop。
- `workspace`、`sandbox` 和 `privacy` 定义稳定协议；第三方 SDK 只能出现在 Provider/Adapter 或明确装配层。
- `providers` 是 OpenSandbox、OpenTelemetry 等基础设施 SDK 的唯一落点。
- 禁止循环依赖、从内部模块反向导入 API，以及创建持有所有服务的巨型 Runtime/Capability/HookManager。

## V1 取舍

- OpenSandbox 是唯一正式 SandboxProvider；不复制容器生命周期。
- LocalWorkspaceProvider 是唯一正式 Workspace 实现；AgentFS 仅是未来参考。
- LocalDurableExecutor + Runtime SQLite 负责耐久执行；V1 不依赖 Prefect。
- 使用 PydanticAI 原生工具调用；不采用 CodeMode/Monty。
- Agent Memory 使用 Workspace、Checkpoint 与 FTS/BM25；不使用向量数据库。
- 只加载管理员预装的本地 Skill；禁止运行时下载、安装或更新。
- V1 只分析导入文件或数据库快照，不连接在线数据库。
- V1 不提供 Webhook；公网部署、认证、TLS 和多租户安全为非目标。

## 领域不变量

- Session 表示长期用户上下文；Task 表示用户目标；Run 表示一次执行尝试；AnalysisStep 表示一次独立、可审计的本地计算。
- Task 状态为 `QUEUED/ACTIVE/WAITING/COMPLETED/FAILED/CANCELLED`；等待细节使用 `wait_reason`，没有 `SUSPENDED`。
- Run 状态为 `QUEUED/RUNNING/WAITING/SUCCEEDED/FAILED/CANCELLED`，当前工作使用 `phase` 表达。终态 Run 不重新打开。
- Step 状态为 `PENDING/RUNNING/SUCCEEDED/FAILED/TIMED_OUT/CANCELLED`。失败重试创建新 Step，并设置 `retry_of_step_id`。
- Dataset、Artifact、AnalysisStep、Finding 和 Lineage 是一等领域对象；正式关系只能引用稳定 ID 与内容 hash，不能用裸路径替代。
- 每个 Run 使用一个可替换 Sandbox lease；每个 Step 独立进程。跨 Step 状态必须写入 Workspace。
- `RUN.json` 是不可变复现清单，不是业务状态副本。

## 开源复用规则

- 优先直接依赖和 Adapter，不复制或 fork 上游源码。
- Agent Skills 使用标准目录格式；OpenLineage/OpenTelemetry 采用兼容语义，不设计同名私有协议。
- MemPrivacy、LLM Guard、AgentFS、LangGraph、DBHub、gVisor/Firecracker 仅作为对应设计参考，除非架构文档明确升级为正式依赖。
- 所有第三方依赖锁定版本并核验 License/Notice；镜像使用 digest，并保留 SBOM/漏洞扫描流程。

## 开发与验收

- 文件变更前确定所属模块、事实来源、安全边界和幂等语义。
- 开发顺序、阶段退出条件和交付物以 `doc/DEVELOPMENT_PLAN.md` 为准；不得跳过前置 Gate 后宣称后续阶段完成。
- 每完成一个开发阶段，必须在 `doc/` 新建独立的阶段完成文档，然后才能把总计划中的阶段状态改为完成。禁止只修改旧日志或仅在提交信息中记录。
- 阶段文档命名为 `phase-XX-<slug>-YYYYMMDD.md`；同日重复验收追加两位序号，不覆盖既有文件。
- 阶段文档必须列出目标与范围、逐文件改动、Interface/不变量变化、数据库或迁移、安全影响、依赖变化、测试命令与结果、验收证据、架构偏差、遗留问题和下一阶段入口条件。
- 若阶段未通过全部退出条件，文档只能标记 `PARTIAL` 或 `BLOCKED`，总计划状态不得标记 `COMPLETED`。
- Provider 必须有 Integration Test；协议实现必须通过 Contract Test；隐私、Sandbox、恢复、取消和发布链路必须有 E2E Test。
- 测试覆盖失败、取消、超时、预算耗尽、重复调用熔断、幂等、Host 崩溃、Sandbox 重建和部分发布。
- 日志与 Trace 统一关联适用的 `trace_id/task_id/run_id/step_id/tool_call_id/sandbox_id`，默认只记录脱敏内容和元数据。
- 子目录 `AGENT.md` 会收紧对应模块规则；冲突时遵循更具体且更严格的约束。
