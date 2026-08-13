# DataHarness 开发规范

## 架构目标

DataHarness 是云端 LLM 驱动、本地持久化与隔离执行的单机单租户数据分析 Agent Harness。项目实现领域状态、边界协议、分析运行时、隐私模型出口、Workspace 与组件编排；复用 PydanticAI、OpenSandbox、DuckDB、Pandera 和 OpenTelemetry 等上游机制。

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
- Provider 必须有 Integration Test；协议实现必须通过 Contract Test；隐私、Sandbox、恢复、取消和发布链路必须有 E2E Test。
- 测试覆盖失败、取消、超时、预算耗尽、重复调用熔断、幂等、Host 崩溃、Sandbox 重建和部分发布。
- 日志与 Trace 统一关联适用的 `trace_id/task_id/run_id/step_id/tool_call_id/sandbox_id`，默认只记录脱敏内容和元数据。
- 子目录 `AGENT.md` 会收紧对应模块规则；冲突时遵循更具体且更严格的约束。
