# DataHarness Development Plan

> Status: Approved baseline
> Plan version: 1.2
> Architecture source: `ARCHITECTURE.md`
>
> Chat-first 修订：旧 Phase 08–12 条目描述的是兼容的 Task/Run 分析作业路径；普通对话、Message 持久化和自然语言模型输出以 `decision-004-chat-first-agent.md` 为当前入口。

## 1. Purpose

本计划把 DataHarness V1 架构转化为可按 Gate 验收的开发顺序。目标不是尽快堆出所有目录，而是逐步建立小而稳定的 Interface，将复杂行为隐藏在 deep module 的 Implementation 中，并用生产 Adapter 与测试 Adapter 在真实 seam 上验证。

完成定义同时包含代码、测试、文档和可恢复性证据。某阶段代码可运行但未通过退出 Gate，仍不算完成。

## 2. Global delivery rules

1. 按阶段依赖顺序开发。可以提前做调研或 spike，但不能在前置阶段未通过时宣称依赖它的阶段完成。
2. 每次变更先确定所属模块、事实来源、Interface、不变量、安全影响、幂等语义和测试层级。
3. 每个阶段完成时，在 `doc/` 新建一份阶段报告，命名为 `phase-XX-<slug>-YYYYMMDD.md`。
4. 阶段报告不得覆盖旧文件；同日重复验收使用 `-01`、`-02` 后缀。
5. 阶段报告必须使用 `PHASE_REPORT_TEMPLATE.md` 的全部章节，并附真实测试命令和结果。
6. 报告写完且全部 Gate 有证据后，才可把本计划状态改为 `COMPLETED` 并链接报告。
7. 架构偏差必须先修改 `ARCHITECTURE.md` 与相关 `AGENT.md`；不能让实现静默改变安全承诺。
8. 所有开发阶段均不得使用真实秘密、生产数据或真实云账号作为默认测试条件。

## 3. Stage map

| Phase | Name | Primary modules | Depends on | Status | Completion report |
|---:|---|---|---|---|---|
| 00 | Engineering foundation | packaging, config, CI | — | COMPLETED | [phase-00-foundation-20260813.md](phase-00-foundation-20260813.md) |
| 01 | Domain model and state machines | domain | 00 | COMPLETED | [phase-01-domain-20260813.md](phase-01-domain-20260813.md) |
| 02 | Runtime storage and durable primitives | storage | 01 | COMPLETED | [phase-02-storage-20260813.md](phase-02-storage-20260813.md) |
| 03 | Project corpus, workspace, import and publication | projects, workspace, providers/workspace | 01–02 | COMPLETED | [phase-03-project-corpus-20260813.md](phase-03-project-corpus-20260813.md) |
| 04 | Privacy and ModelGateway | privacy, hooks | 00–02 | COMPLETED | [phase-04-privacy-20260813.md](phase-04-privacy-20260813.md) |
| 05 | OpenSandbox execution seam | sandbox, providers/sandbox, images | 00, 03 | COMPLETED | [phase-05-sandbox-20260813.md](phase-05-sandbox-20260813.md) + [addendum-20260814](phase-05-sandbox-20260814-addendum-01.md) |
| 06 | Analysis runtime and agent capabilities | analysis, capabilities | 01–05 | COMPLETED | [phase-06-analysis-20260813.md](phase-06-analysis-20260813.md) + [复验-20260814](phase-06-analysis-20260814.md) |
| 07 | Durable orchestration and recovery | orchestration, providers/durable | 01–06 | COMPLETED | [phase-07-orchestration-20260814.md](phase-07-orchestration-20260814.md) |
| 08 | Agent assembly, Skills and context | agent, skills, memory | 04, 06–07 | COMPLETED | [phase-08-agent-20260814-01.md](phase-08-agent-20260814-01.md) |
| 09 | Verification, HTTP and observability | analysis, api, observability | 03–08 | COMPLETED | [phase-09-api-verification-20260814.md](phase-09-api-verification-20260814.md) |
| 10 | E2E hardening and V1 release | all modules | 00–09 | COMPLETED | [phase-10-v1-release-20260814.md](phase-10-v1-release-20260814.md) |
| 11 | Production Agent Harness | agent, orchestration, model provider, api | 04–10 | COMPLETED | [phase-11-production-agent-harness-20260815.md](phase-11-production-agent-harness-20260815.md) |
| 12 | Local WebUI | web, api | 11 | COMPLETED | [phase-12-local-webui-20260815.md](phase-12-local-webui-20260815.md) |
| 13 | One-command local deployment | scripts, api, worker, OpenSandbox | 11–12 | IN_PROGRESS | [partial report](phase-13-local-deployment-20260815.md) |
| 14 | Team deployment preparation | architecture, deployment, security | 13 | IN_PROGRESS | [partial report](phase-14-team-deployment-preparation-20260815.md) |

Critical path:

`00 -> 01 -> 02 -> 03 -> 05 -> 06 -> 07 -> 08 -> 09 -> 10`

本机应用化关键路径：

`10 -> 11 -> 12 -> 13`

Phase 14 只准备从个人本机工具迁移到团队内部平台的边界，不在个人版中提前实现多租户或分布式基础设施。

Phase 04 可在 Phase 03 期间并行实现，但 Phase 06 和 Phase 08 的验收都依赖它。

Version 1.1 将 ProjectCorpus、不可变文件版本、ProjectSnapshot、RELEVANT/FULL_PROJECT 跨文件处理纳入 V1 基线；决策记录见 `decision-001-project-corpus.md`。

Version 1.2 在已发布的核心 Harness 后追加生产 Agent 闭环、本机 WebUI 和一键部署阶段；不修改 Phase 00–10 的历史完成事实。决策记录见 [decision-002-local-agent-application.md](decision-002-local-agent-application.md)。

## 4. Cross-phase quality gates

以下要求适用于每个阶段：

- `uv lock --check` 或等价锁文件一致性检查通过。
- `ruff format --check .`、`ruff check .` 和 `pyright` 通过。
- 受影响层级的 pytest 全部通过；禁止用跳过测试掩盖失败。
- 新增 Interface 有行为测试；测试从 Interface 观察结果，不依赖私有 Implementation。
- 新增 Provider 至少具有 production Adapter 与 fake/test Adapter，或有文档说明为何暂不建立 seam。
- 任何日志、trace、异常和 fixture 不包含真实 secret 或 Privacy 映射原值。
- 新依赖已锁定并检查 License；Sandbox 镜像变化记录 digest、SBOM 和扫描结果。
- 阶段报告已经新建，退出 Gate 逐项给出证据。

## 5. Detailed phases

### Phase 00 — Engineering foundation

Objective：建立所有后续模块共用且可复现的 Python 工程骨架。

Deliverables:

- 创建 `pyproject.toml`、`uv.lock`、包入口和最小 CLI/应用启动入口。
- 配置 Python 3.12、Ruff、Pyright、pytest、pytest-asyncio、Hypothesis 和覆盖率。
- 建立配置模型：runtime-data/Project 根、Workspace 根、Privacy 根、模型 Provider、OpenSandbox endpoint、提取/索引、预算与资源限制。
- 建立依赖方向检查，至少禁止 domain 导入外部框架和内部模块反向导入 api。
- 建立 fake clock、ID factory、临时目录和 synthetic data 测试 fixtures。
- 建立基础 CI 命令或本地统一验证脚本，不接入真实模型和公网。

Exit Gate:

- 全新环境可仅凭锁文件安装并运行测试。
- 最小应用可启动并读取经过 Pydantic 校验的本地配置。
- lint、format、type-check、unit test 可由一组记录明确的命令完成。
- 依赖方向的正例和负例测试均通过。

Required report: `doc/phase-00-foundation-YYYYMMDD.md`

### Phase 01 — Domain model and state machines

Objective：把领域语言、不变量和合法状态迁移实现为不执行 I/O 的 deep module。

Deliverables:

- 实现 Project、ProjectFile、ProjectFileVersion、ProjectSnapshot、ProjectCoverageReport、Session、Task、Run、AnalysisStep、Dataset、Artifact、Finding、Lineage 和领域错误。
- 实现 Task 必须绑定单一 Project、FileVersion 不可变、Snapshot 创建后不可变和 Run 固定 Snapshot 的规则。
- 实现 FileVersion `IMPORTING/READY/FAILED/UNSUPPORTED` 状态及 Coverage 项语义。
- 实现 Project `ACTIVE/ARCHIVED` 与 CoverageItem `PROCESSED/FAILED/UNSUPPORTED/SKIPPED`；归档不删除历史 Snapshot。
- 实现 Task/Run/Step/Finding 状态机、Run phase、WaitReason 和 StepFailureKind。
- 实现终态不可回退、重试创建新 Step、Finding 只能经验证 Gate 晋级等规则。
- 定义稳定 ID、时间戳、内容 hash、资源引用和幂等键值对象。
- 使用表驱动和 Hypothesis 测试所有合法/非法迁移。

Exit Gate:

- domain 不导入 FastAPI、PydanticAI、OpenSandbox、sqlite3 或 OpenTelemetry。
- 每个状态节点和边都有测试，非法转换返回稳定领域错误。
- 相同输入产生稳定的 hash/幂等语义。
- 领域 Interface 不暴露数据库行、文件路径或第三方 SDK 类型。
- 文件更新只能创建新版本；Snapshot 不提供原地更新操作。

Required report: `doc/phase-01-domain-YYYYMMDD.md`

### Phase 02 — Runtime storage and durable primitives

Objective：建立 Runtime SQLite 事实源及可供 orchestration 使用的原子持久化原语。

Deliverables:

- 建立有序 SQL migrations、schema version、外键、唯一约束和 WAL 配置。
- 实现 Project/File/FileVersion/Snapshot/Coverage、Task/Run/Step、Dataset/Artifact/Finding/Lineage、event、checkpoint metadata repository。
- 实现 UnitOfWork、CAS 状态更新、幂等键、原子 queue claim、lease epoch、heartbeat 和过期回收。
- 实现 Runtime DB 与 Privacy DB 路径/连接工厂的物理隔离。
- 建立 migration、事务回滚、并发 claim 和终态保护测试。

Exit Gate:

- 空库、逐版本升级和失败回滚测试通过。
- 两个 Worker 不能同时持有同一有效 lease；旧 epoch 不能提交状态。
- 状态与对应事件在同一事务成功或失败。
- Runtime DB 中不存在大文件、原始模型载荷、secret 或 PII 映射字段。
- Snapshot 与文件版本关联不可修改；Run 的 project_snapshot_id 受 fencing/CAS 保护且恢复时保持一致。

Required report: `doc/phase-02-storage-YYYYMMDD.md`

### Phase 03 — Project corpus, workspace, import and publication

Objective：实现长期 Project 语料、不可变文件版本、本地跨文件索引、受控 Task 写入和可崩溃恢复的正式输出发布。

Deliverables:

- 实现 ProjectCorpus、VirtualWorkspace/WorkspaceBridge 和 LocalWorkspace Adapter。
- 原子创建 Project sources/extracted/indexes/datasets/artifacts/manifests 与 Task working/staging/state。
- 实现 CSV、Parquet、Excel、JSON、PDF、DOCX、PPTX、Markdown、TXT 及可选 DuckDB/SQLite snapshot 导入。
- 实现格式嗅探、规范化文件名、大小/类型/hash 校验、不可变 ProjectFileVersion 和输入只读策略。
- 实现带页码/段落/幻灯片/工作表定位的本地提取，以及 FTS5/BM25 + 元数据索引。
- 实现不可变 ProjectSnapshot，记录全部当前文件版本/处理状态，并固定 READY 条目的索引版本和项目 Dataset 版本。
- 实现路径规范化、真实路径检查、符号链接/设备文件/目录穿越拒绝。
- 实现 STAGED -> publish -> AVAILABLE 协议、幂等键与 reconciler。

Exit Gate:

- Agent/Sandbox 无法覆盖或删除 ProjectFileVersion；更新同名逻辑文件会创建新版本。
- `..`、Host 绝对路径、符号链接和跨 Task 引用均被拒绝。
- 提取物和索引绑定 source hash 与 extractor/index version，删除后可以重建。
- 不支持或损坏文件显式标记 UNSUPPORTED/FAILED，不会被假装处理。
- Snapshot 创建后保持不可变；后续上传不改变已有 Run 的数据视图。
- 在发布各断点注入崩溃后，reconciler 能收敛到 AVAILABLE、可重试或明确损坏状态。
- API/上层只能看到 AVAILABLE 输出，正式对象有稳定 ID 和 hash。

Required report: `doc/phase-03-project-corpus-YYYYMMDD.md`

### Phase 04 — Privacy and ModelGateway

Objective：建立所有云模型调用的唯一出口以及面向误放 secret/PII 的保护。

Deliverables:

- 实现 SecretDetector、PIIDetector、PrivacyPolicy、PlaceholderStore 和 ModelGateway。
- secret 初版规则覆盖密码、API token、私钥、Cookie、连接串，命中即 BLOCK。
- PII 初版覆盖邮箱、手机号、银行卡、身份证和用户显式规则。
- 实现 Task 内稳定、跨 Task 不关联的类型化占位，以及严格类型匹配恢复。
- 实现只扫描新增内容和按内容 hash 缓存检测结果。
- 对 request、response、tool result、异常、compaction、log 和 trace 统一再扫描。

Exit Gate:

- 任何模型 Adapter 都不能绕过 ModelGateway；依赖检查或测试能证明。
- secret 测试语料不会到达 fake cloud Adapter。
- PII 到达 fake cloud 时只有占位；Project 原始文件和 hash 不变。
- Privacy DB 不进入 Runtime DB、Workspace、Sandbox、Artifact、log 或 trace。
- 明确记录检测是 best-effort，不对普通业务数据实施默认阻断。

Required report: `doc/phase-04-privacy-YYYYMMDD.md`

### Phase 05 — OpenSandbox execution seam

Objective：使所有不可信 Python/SQL 和 Skill 脚本只能在经验证的 OpenSandbox 中执行。

Deliverables:

- 定义小型 SandboxProvider Interface 和稳定 ExecutionResult/错误分类。
- 实现 OpenSandbox Adapter 与 deterministic fake Adapter。
- 构建并锁定 `secure-analysis` 镜像，预装批准的数据分析依赖。
- 实现 per-Run lease、per-Step 独立进程、超时、取消、输出上限和残留进程清理。
- 创建/重连时校验 digest、断网、非特权用户、只读根、挂载和资源限制。
- Sandbox 只读挂载 Run 固定的 ProjectSnapshot，并只写当前 Task working/staging；同项目并行 Run 使用独立 lease。
- 明确禁止 Host fallback、通用 Host shell、运行时装包和网络开关。

Exit Gate:

- 生成代码没有任何 Host 执行路径。
- Sandbox 看不到 Runtime DB、Privacy DB、Host credential、Docker socket 或其他 Task。
- 一个 Run 取消或销毁时，不影响同 Project 的另一个并行 Run。
- attestation/配置不符时 fail closed，不发生静默降级。
- 超时、取消和 Sandbox 丢失后无残留进程，且可用相同 digest 重建。
- 镜像 digest、依赖锁、SBOM 和漏洞扫描证据齐全。

Required report: `doc/phase-05-sandbox-YYYYMMDD.md`

### Phase 06 — Analysis runtime and agent capabilities

Objective：把模型的窄工具调用转换为可审计、可发布、可恢复的 AnalysisStep。

Deliverables:

- 实现 AnalysisRuntime 及 `execute_python`、`execute_sql`。
- 实现 `list_project_files`、`search_project`、`inspect_project_file`、`preview_project_table`、`query_project_tables`、`get_project_coverage`、`inspect_output` 和 `submit_finding` 的窄 Interface。
- 实现 RELEVANT 检索模式：元数据过滤 + FTS5/BM25，结果携带文件版本和页/段/幻灯片/表格定位。
- 实现 FULL_PROJECT 模式：枚举 Snapshot 中所有文件，分批处理并生成 ProjectCoverageReport。
- 每个执行请求声明输入引用、预期输出、超时、预算和 staging 位置。
- 建立 Sandbox SQL/Python runner，使用 DuckDB、pandas、PyArrow、Pandera。
- 返回有界摘要/schema/统计/资源引用，完整结果写 Workspace。
- 实现 Dataset/Artifact 注册、代码与输入 hash、初步 lineage 和 FindingCandidate。

Exit Gate:

- 工具 schema 中不存在 shell、install、network、external API 或在线 DB 能力。
- Step 之间不依赖 Python 变量、后台进程或 Sandbox 内存。
- 相同规范化请求可以幂等识别；连续相同失败触发熔断。
- 输出只有通过 Host 校验和发布后才成为正式资源。
- 每个 FindingCandidate 可追溯到 ProjectFileVersion、Step、输入、代码或 Artifact。
- RELEVANT 回答只声称使用实际引用的文件；FULL_PROJECT 存在 FAILED/UNSUPPORTED/SKIPPED 时明确披露覆盖缺口。

Required report: `doc/phase-06-analysis-YYYYMMDD.md`

### Phase 07 — Durable orchestration and recovery

Objective：实现长任务的领取、执行、等待、取消、重试和崩溃恢复。

Deliverables:

- 实现 TaskService、RunService、LocalDurableExecutor 和 Worker loop。
- 实现 Run status 与 phase 分离、WAITING + wait_reason、有限重试和退避。
- 实现 PydanticAI checkpoint metadata、project_snapshot_id、Sandbox lease 关联和恢复决策。
- 实现幂等取消：停止新调用、终止进程、宽限后销毁 Sandbox、清理未提交 staging。
- 实现 Host crash、lease expiry、Sandbox loss 和部分发布的恢复路径。
- 实现预算耗尽、resource limit、policy denied、model-correctable 等错误分类。

Exit Gate:

- Host 重启后恢复同一非终态 Run，已提交 Step 不重复执行。
- 恢复 Run 使用原 ProjectSnapshot，不因上传新文件或新版本而改变输入。
- 终态 Run 不重新打开；用户重试创建新 Run。
- 取消多次调用结果一致，已发布资源保留，未发布 staging 不可见。
- lease fencing 可阻止旧 Worker 或旧 Sandbox 提交。
- 所有自动重试有分类、次数上限和退避，无无限循环。

Required report: `doc/phase-07-orchestration-YYYYMMDD.md`

### Phase 08 — Agent assembly, Skills and context

Objective：在既有安全与耐久模块上装配 PydanticAI，而不实现第二套 agent loop。

Deliverables:

- 装配 PydanticAI Agent、ModelGateway、原生工具、UsageLimits 和结构化最终输出。
- 实现本地 SkillRegistry：只发现预装 Skill、渐进加载、只读目录和内容 hash。
- Skill 脚本统一经 AnalysisRuntime/OpenSandbox 执行。
- 实现 checkpoint/compaction：持久化目标、计划、进度、ProjectSnapshot/FileVersion、领域引用和未解决问题。
- 实现 SQLite FTS5/BM25 的可选历史检索；不建立向量记忆。
- 建立 fake model 对工具循环、预算、等待和恢复进行确定性测试。

Exit Gate:

- 所有模型调用包括 compaction/summary 均经过 ModelGateway。
- Agent 只能看到已注册的窄工具和已激活 Skill。
- Skill 运行期间被修改时拒绝继续或要求新 Run。
- Compaction 后恢复仍引用正确 Dataset/Artifact/Finding，不把摘要当事实源。
- Project 跨文件检索属于 ProjectCorpus；MemoryCapability 不复制项目索引或引入向量记忆。
- 不存在 CodeMode/Monty、在线 Skill registry 或运行时依赖安装。

Required report: `doc/phase-08-agent-YYYYMMDD.md`

### Phase 09 — Verification, HTTP and observability

Objective：完成正式 Finding Gate、本地控制面和脱敏可观测性。

Deliverables:

- 实现 ExecutionGate、IntegrityGate 和 EvidenceGate。
- 实现轻量数据 warning：行数异常、join 膨胀、缺失值、类型转换和重复值。
- 实现 FastAPI Project 创建/查询、文件导入/版本/检索、Project 内 Task 创建/查询/取消/恢复/重试、事件、Dataset、Artifact 和受控文件接口。
- 实现统一错误 DTO、事件序列和可选 SSE；不实现 Webhook。
- 实现 OpenTelemetry Adapter 与 trace/task/run/step/tool/sandbox 关联。
- 确保日志、异常、事件和 trace 全部经过隐私处理。

Exit Gate:

- 只有 Host Gate 能把 Finding 标为 VERIFIED/WARNING/REJECTED。
- 每个 VERIFIED Finding 至少有一条 hash 未变且属于当前 ProjectSnapshot/Task/Run 的有效证据链。
- FULL_PROJECT Finding 具有 CoverageReport；未覆盖文件会出现在回答和事件中。
- HTTP 层不直接访问 SQLite、OpenSandbox、Workspace 路径或模型 SDK。
- 默认绑定 `127.0.0.1`，公网认证、多租户和 Webhook 未被误写为 V1 能力。
- 观测后端故障不破坏业务状态，隐私处理故障则 fail closed。

Required report: `doc/phase-09-api-verification-YYYYMMDD.md`

### Phase 10 — E2E hardening and V1 release

Objective：证明架构承诺在完整链路、故障和恶意输入下成立，并形成可复现 V1。

Deliverables:

- 建立真实本地 API + SQLite + ProjectCorpus/Workspace + OpenSandbox + fake cloud model 的 E2E harness。
- 覆盖多文件多格式导入、文件版本、Snapshot、RELEVANT/FULL_PROJECT、隐私出口、SQL/Python、发布、lineage、Finding 和回答。
- 注入 Host crash、Sandbox loss、timeout、cancel、budget exhaustion、重复调用和部分发布。
- 建立 secret/PII、prompt-in-file、路径逃逸、符号链接、资源耗尽和恶意输出安全测试集。
- 覆盖同 Project 并行 Task、文件更新后新旧 Run 使用不同 Snapshot、单 Task 取消不影响同项目其他 Task。
- 完成性能基线：模型出口扫描延迟、文件导入、Step 启动、恢复时间和资源上限。
- 固化依赖锁、镜像 digest、Skill hash、SBOM、License/Notice、配置样例和操作手册。

Exit Gate:

- `ARCHITECTURE.md` 第 22 节的完整验收链路全部通过并有自动化证据。
- 生成代码从未在 Host 执行；Runtime/Privacy DB 与 credential 从未进入 Sandbox。
- secret 从未到达 fake cloud；PII 占位不修改本地原始数据且可在 Task 内恢复。
- Project 文件版本不可变，旧 Run 可按原 Snapshot 复现；RELEVANT 与 FULL_PROJECT 的来源/覆盖声明准确。
- 同一 Project 的并行 Task 隔离，取消其中一个不影响其他 Task。
- Host 重启后不重复已完成 Step；取消/失败不发布半成品。
- 每个 VERIFIED Finding 有有效证据链；所有发布对象 hash 与 lineage 一致。
- 已知限制、非目标、恢复流程和运维检查清单已记录，可从干净环境复现。

Required report: `doc/phase-10-v1-release-YYYYMMDD.md`

### Phase 11 — Production Agent Harness

Objective：把 Phase 04–10 已有的模型边界、Agent 工厂、上下文、耐久执行、Sandbox 和验证组件装配成可由用户问题驱动的生产运行闭环。

Deliverables:

- 扩展 Task 创建输入，持久化用户 `prompt` 的受控 Workspace 载荷；Runtime SQLite 只保存稳定引用、hash 和状态，不保存原始模型载荷。
- Session 固定绑定一个 Project；同一 Session 的每次用户消息创建新 Task，每个 Task 固定创建时的 ProjectSnapshot，不跨 Project 检索历史。
- 实现首个 OpenAI-compatible `CloudModelProvider`，从本地配置读取 `model`、`base_url`、超时和 `api_key`；所有模型、摘要和压缩调用继续经过 ModelGateway。
- 实现 `AgentRunHandler`，按 Run 装配 AgentRunner、AnalysisRuntime、ContextCheckpointManager、SkillRegistry、可选 MemoryCapability 和 Sandbox lease，并把结构化 Agent 输出映射为 RunOutcome。
- 保持单 PydanticAI Agent；计划、执行、发布、验证、预算、重试和恢复由确定性 Host Harness 管理，不新增 Planner/Reviewer Agent 或第二套 agent loop。
- 补齐 Agent 工具输入/输出声明：ProjectFileVersion/Dataset 受控输入、ExpectedOutput、表格预览、Project Coverage、Python/SQL、输出检查和 Finding 提交。
- 完成 Dataset、Artifact、ChartArtifact、Lineage 和 Finding 的发布与验证闭环；图表使用受控 Vega-Lite JSON，且可生成 PNG/SVG 兜底产物。
- 上下文分为当前消息、结构化工作状态、稳定事实引用和 Session 历史四层；在关键 AnalysisStep、发布、验证、WAITING、完成及压缩边界保存 checkpoint。
- 自动估算上下文预算并触发 compaction；保留当前问题、系统约束、Snapshot、稳定资源引用、未解决问题和最近工具调用，摘要不得成为事实来源。
- 实现 Project + Session 作用域的历史存储与检索，修复当前全局历史搜索可能跨边界命中的问题。
- 新增 Worker CLI 和生命周期装配；预算内允许 Agent 自动检索与执行，歧义、缺少输入、策略阻断、预算耗尽或不支持能力时进入带稳定原因的 WAITING。
- 新增基于 Runtime 事件序号的 SSE Task event stream；断线重连可补发事件，SSE 不作为事实源，也不暴露隐藏思考过程或敏感原文。

Exit Gate:

- 通过 HTTP 提交真实问题后，Worker 自动领取 Run，模型至少调用一个受控项目工具，并最终产生 COMPLETED 或有明确原因的 WAITING。
- 真实 Provider 不可绕过 ModelGateway；缺少 API Key、服务超时、无效响应和额度错误映射为稳定且脱敏的错误分类。
- Python/SQL 只在通过 attestation 的 OpenSandbox 中运行；输入绑定固定 Snapshot，输出只有经 Host 发布后可见。
- Worker 或 Host 在关键 checkpoint 后崩溃并重启时恢复同一 Run，不重复已经正式提交的 AnalysisStep 或发布对象。
- Session 历史不会跨 Project 泄露；新上传文件不会改变旧 Task 的 Snapshot、上下文或证据链。
- 上下文压缩后仍能从稳定引用重建事实；摘要、模型原文或工具摘要不能伪造 Dataset、Artifact、Finding 或 lineage。
- 图表规范不允许外部 URL、任意 JavaScript、HTML、iframe 或未发布 Dataset 引用；hash 漂移时 fail closed。
- fake cloud E2E、真实 OpenSandbox 集成测试和可选真实模型 smoke test 有独立命令及证据；默认测试不使用真实云凭据。

Required report: `doc/phase-11-production-agent-harness-YYYYMMDD.md`

### Phase 12 — Local WebUI

Objective：为个人本机用户提供项目、文件、连续对话、任务执行和证据结果的一体化浏览器工作台。

Deliverables:

- 在 `web/` 建立 React、TypeScript 和 Vite 应用，使用 Ant Design、TanStack Query、React Router 与 Vega-Lite；根据 FastAPI OpenAPI 生成或校验 TypeScript API 类型。
- 实现 `/projects` 项目列表，以及项目创建、归档和最近任务入口。
- 实现 `/projects/{project_id}` 项目工作台：文件上传、版本、处理状态、检索和 Session 创建。
- 实现 `/projects/{project_id}/sessions/{session_id}` 对话界面：自由问题、快捷分析模板、连续追问、取消、恢复和 WAITING 输入。
- 实现 `/tasks/{task_id}` 结果页面：最终回答、Dataset、Artifact、Finding、证据、lineage、图表与可展开工具轨迹。
- 建立统一 ChartRenderer：默认渲染已验证的 Vega-Lite JSON，失败时回退 PNG/SVG，并提供图表、数据表和说明切换。
- 使用 SSE 显示简化任务进度；工具名称、耗时、输入/输出摘要和资源引用按需展开，不展示隐藏思考过程。
- 提供本地诊断抽屉，显示 Docker、OpenSandbox、API、Worker、模型配置、镜像 digest、数据目录和磁盘占用；API Key 只显示已配置/未配置。
- 开发期由 Vite 代理 API/SSE；发布构建由 FastAPI 同源托管，最终用户不需要 Node.js。
- 建立组件测试、API mock 测试、可访问性检查和 Playwright 关键用户流程。

Exit Gate:

- 用户只通过 WebUI 即可创建 Project、上传文件、创建 Session、提交问题、观察执行、处理 WAITING 并查看最终证据结果。
- 页面刷新和 SSE 断线不丢失事实状态；重新进入 Task 时从 API/Runtime 恢复，而不是依赖浏览器内存。
- 前端不执行 Agent 生成的脚本、HTML 或任意 URL；图表和下载只读取经过验证的正式资源。
- 大文件、大 Dataset 和长事件流使用分页、有界预览或下载，不把完整载荷无界加载到浏览器。
- WebUI 构建产物可由 FastAPI 在回环地址同源提供；不存在生产 CORS 放宽或公网监听默认值。
- Playwright 覆盖项目创建、文件上传、问题提交、SSE 进度、图表显示、取消、WAITING 和恢复。

Required report: `doc/phase-12-local-webui-YYYYMMDD.md`

### Phase 13 — One-command local deployment

Objective：把个人本机应用固化为可检查、可启动、可停止、可恢复和可诊断的发布包。

Deliverables:

- 提供 `setup.ps1`：检查 Docker Desktop、uv、锁文件、端口和配置，安装 Python 依赖，构建/验证 secure-analysis 镜像，并生成不包含秘密的本地配置。
- 提供 `start.ps1`、`stop.ps1` 和 `status.ps1`，统一管理 OpenSandbox Server、DataHarness API 和 DataHarness Worker 三个独立宿主进程。
- API 同源托管预构建 WebUI；Docker 只按需运行受控 Sandbox、execd 和 egress 容器，不把 API/Worker 强制容器化。
- 使用明确的 PID、日志和健康状态目录；重复启动幂等，停止时先停止领取新任务，再取消/清理外部执行并退出。
- 启动前验证本地配置的 `api_key` 是否存在，但不打印、写入 Runtime 或发送到 Sandbox；诊断输出只显示配置状态。
- 建立 Docker、OpenSandbox、Worker、模型 Provider、镜像 digest、允许挂载路径和端口冲突的预检及中文修复提示。
- 记录 Runtime/Privacy/Project 数据备份、恢复、升级和卸载边界；默认操作不得删除用户数据。
- 发布包包含锁文件、前端静态产物、配置样例、镜像构建证据、License/Notice、操作手册和故障排查清单。

Exit Gate:

- 在满足 Docker Desktop 与 uv 前置条件的干净 Windows 环境中，仅通过 `setup.ps1` 和 `start.ps1` 可打开 WebUI 并完成一条真实 Agent 分析链路。
- API、Worker 或 OpenSandbox 任一进程异常退出时，`status.ps1` 能准确定位；重启后按 checkpoint 恢复或给出明确终态。
- `start.ps1` 重复执行不会启动重复 Worker/OpenSandbox；`stop.ps1` 不误杀项目外进程，也不删除用户数据。
- 发布环境不要求 Node.js，不默认绑定公网地址，不把 Docker socket、API Key、Runtime DB 或 Privacy DB 暴露给 Sandbox。
- 备份恢复演练、端口冲突、Docker 未启动、模型密钥缺失和 OpenSandbox 配置错误均有自动化或可复现验收证据。

Required report: `doc/phase-13-local-deployment-YYYYMMDD.md`

### Phase 14 — Team deployment preparation

Objective：在不提前实现团队平台的前提下，记录从个人本机工具迁移到内部多人服务所需的稳定边界和拆分顺序。

Deliverables:

- 形成 API/Web/Worker/OpenSandbox 独立部署拓扑和威胁模型，不把个人版回环安全假设沿用到共享环境。
- 设计认证、RBAC、Project 租户隔离、审计、TLS、反向代理、CSRF/CORS 和密钥管理边界。
- 评估 Runtime SQLite 向 PostgreSQL、LocalWorkspace 向对象存储、单机 Worker 向耐久队列迁移的 Adapter 与数据迁移策略。
- 设计团队环境的并发配额、Sandbox 池、观测、备份、恢复、升级和容量基线。
- 形成容器镜像和 Compose/Kubernetes 部署草案；未完成安全 Gate 前不得对外提供共享访问。

Exit Gate:

- 团队版决策文档明确列出信任边界、事实源、迁移顺序、兼容接口和不可沿用的个人版假设。
- 核心 Domain、AgentRunHandler、API DTO 和 WebUI 不依赖本机进程管理实现，可由 Provider/Adapter 替换基础设施。
- 未把文档设计描述成已实现能力；认证、多租户、在线数据库和高可用保持明确的后续状态。

Required report: `doc/phase-14-team-deployment-preparation-YYYYMMDD.md`

## 6. Test ownership

| Test layer | Owns | Must not do |
|---|---|---|
| Unit | Project/领域规则、检测规则、路径、hash、预算、错误分类 | 网络、真实 OpenSandbox、真实模型 |
| Contract | ProjectCorpus/Sandbox/Workspace/ModelGateway/Durable Interface 的共享行为 | 断言具体 SDK 内部结构 |
| Integration | SQLite、Project 提取/索引、文件系统、OpenSandbox、PydanticAI 与 Adapter 组合 | 访问真实云账号或生产数据 |
| E2E | 用户可见流程、崩溃恢复、安全不变量和发布证据 | 绕过 ModelGateway 或用 Host 执行替代 Sandbox |

## 7. Definitions of done

### 7.1 Core Harness V1

只有同时满足以下条件，项目才可标记为 V1 完成：

- Phase 00–10 均为 `COMPLETED`，每阶段有独立且可追溯的完成报告。
- 所有 cross-phase Gate 和 Phase 10 Exit Gate 通过。
- 架构、根级/模块级 AGENT 约束、实现和测试之间不存在已知冲突。
- 从干净环境可以使用锁文件和固定镜像 digest 复现验收。
- 文档明确说明普通业务数据可能发送给用户配置的云模型，隐私检测为 best-effort。
- V1 非目标没有被描述成已经实现的安全能力。

Phase 00–10 的 `COMPLETED` 表示核心 Harness、控制面和发布证据已经完成，不等价于存在生产模型、自动 Worker、WebUI 或一键部署。

### 7.2 Local application release

个人本机应用只有同时满足以下条件才可发布：

- Phase 11–13 均为 `COMPLETED`，每阶段有独立完成报告和 checkpoint commit。
- 用户可以从 WebUI 提交问题并得到由真实 Agent、OpenSandbox 和 Verification Gate 产生的可追溯回答。
- API、Worker 和 OpenSandbox 可由脚本独立管理，异常恢复、日志、诊断和备份流程有验收证据。
- 前端不执行 Agent 生成的任意代码，模型调用不绕过 ModelGateway，Sandbox 不获得 Host 凭据或事实数据库。
- 干净环境部署不要求 Node.js，不默认开放公网，也不把团队版能力描述成已经完成。

## 8. Plan maintenance

- 状态仅使用 `NOT_STARTED`、`IN_PROGRESS`、`BLOCKED`、`COMPLETED`。
- 开始阶段时将状态改为 `IN_PROGRESS`；阻塞时链接 `BLOCKED` 阶段报告。
- 完成时先创建阶段报告，再将状态改为 `COMPLETED` 并填入相对链接。
- 计划新增、删除、拆分或合并阶段时，增加 Plan version，并在独立决策文档记录原因。
- 不删除历史阶段报告；纠错使用 `phase-XX-<slug>-YYYYMMDD-addendum-NN.md`。
