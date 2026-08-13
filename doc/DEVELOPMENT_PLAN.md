# DataHarness V1 Development Plan

> Status: Approved baseline
> Plan version: 1.1
> Architecture source: `ARCHITECTURE.md`

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
| 04 | Privacy and ModelGateway | privacy, hooks | 00–02 | IN_PROGRESS | — |
| 05 | OpenSandbox execution seam | sandbox, providers/sandbox, images | 00, 03 | NOT_STARTED | — |
| 06 | Analysis runtime and agent capabilities | analysis, capabilities | 01–05 | NOT_STARTED | — |
| 07 | Durable orchestration and recovery | orchestration, providers/durable | 01–06 | NOT_STARTED | — |
| 08 | Agent assembly, Skills and context | agent, skills, memory | 04, 06–07 | NOT_STARTED | — |
| 09 | Verification, HTTP and observability | analysis, api, observability | 03–08 | NOT_STARTED | — |
| 10 | E2E hardening and V1 release | all modules | 00–09 | NOT_STARTED | — |

Critical path:

`00 -> 01 -> 02 -> 03 -> 05 -> 06 -> 07 -> 08 -> 09 -> 10`

Phase 04 可在 Phase 03 期间并行实现，但 Phase 06 和 Phase 08 的验收都依赖它。

Version 1.1 将 ProjectCorpus、不可变文件版本、ProjectSnapshot、RELEVANT/FULL_PROJECT 跨文件处理纳入 V1 基线；决策记录见 `decision-001-project-corpus.md`。

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

## 6. Test ownership

| Test layer | Owns | Must not do |
|---|---|---|
| Unit | Project/领域规则、检测规则、路径、hash、预算、错误分类 | 网络、真实 OpenSandbox、真实模型 |
| Contract | ProjectCorpus/Sandbox/Workspace/ModelGateway/Durable Interface 的共享行为 | 断言具体 SDK 内部结构 |
| Integration | SQLite、Project 提取/索引、文件系统、OpenSandbox、PydanticAI 与 Adapter 组合 | 访问真实云账号或生产数据 |
| E2E | 用户可见流程、崩溃恢复、安全不变量和发布证据 | 绕过 ModelGateway 或用 Host 执行替代 Sandbox |

## 7. Definition of V1 done

只有同时满足以下条件，项目才可标记为 V1 完成：

- Phase 00–10 均为 `COMPLETED`，每阶段有独立且可追溯的完成报告。
- 所有 cross-phase Gate 和 Phase 10 Exit Gate 通过。
- 架构、根级/模块级 AGENT 约束、实现和测试之间不存在已知冲突。
- 从干净环境可以使用锁文件和固定镜像 digest 复现验收。
- 文档明确说明普通业务数据可能发送给用户配置的云模型，隐私检测为 best-effort。
- V1 非目标没有被描述成已经实现的安全能力。

## 8. Plan maintenance

- 状态仅使用 `NOT_STARTED`、`IN_PROGRESS`、`BLOCKED`、`COMPLETED`。
- 开始阶段时将状态改为 `IN_PROGRESS`；阻塞时链接 `BLOCKED` 阶段报告。
- 完成时先创建阶段报告，再将状态改为 `COMPLETED` 并填入相对链接。
- 计划新增、删除、拆分或合并阶段时，增加 Plan version，并在独立决策文档记录原因。
- 不删除历史阶段报告；纠错使用 `phase-XX-<slug>-YYYYMMDD-addendum-NN.md`。
