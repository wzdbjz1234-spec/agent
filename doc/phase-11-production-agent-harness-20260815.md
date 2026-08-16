# Phase 11 完成报告：Production Agent Harness

- Status: `COMPLETED`
- Date: `2026-08-15`
- Plan phase: `Phase 11`
- Commit/revision: `工作树验收（待 checkpoint commit）`

## 1. Objective and scope

本阶段把 Phase 04–10 的 ModelGateway、PydanticAI Agent、AnalysisRuntime、OpenSandbox
和 LocalDurableExecutor 装配成由用户问题驱动的可恢复运行闭环。完成范围包括受控 prompt、
Project-scoped Session、OpenAI-compatible Provider、AgentRunHandler、独立 Worker、
checkpoint/compaction、Finding Verification、SSE 事件回放和 Vega-Lite 安全边界。

本阶段没有实现 React WebUI、Docker/API/Worker 一键部署、认证、多租户、团队版或
Anthropic 原生 Provider；这些分别属于 Phase 12–14 或明确的后续范围。

## 2. Detailed changes

### Task、Session、Workspace 和 Runtime

- `src/dataharness/domain/task.py`：Task 增加 `prompt_ref/prompt_hash`，并校验两者必须成对出现。
- `src/dataharness/domain/session.py`：Session 增加可选 `project_id`；新 API 创建的 Session
  固定绑定一个 Project，保留可空兼容字段以读取 Phase 00–10 历史记录。
- `src/dataharness/storage/migrations/0005_agent_harness.sql`：增加 Session Project 归属、
  Task prompt 引用/hash、索引。
- `src/dataharness/orchestration/services.py`：TaskService 将 prompt 写入不可变
  `tasks/<task>/state/PROMPT.json`，新增 SessionService，并在 Task 创建时校验 Session/Project。
- `src/dataharness/providers/workspace/local.py`、`src/dataharness/workspace/protocols.py`：
  增加不可变 prompt 读取和按 Snapshot 物理 materialize 的只读视图。
- `src/dataharness/projects/corpus.py`：创建 Snapshot 时复制 READY 文件版本到独立 Snapshot
  视图；旧 Snapshot 在 Worker 首次使用时可按事实源补建。
- `src/dataharness/storage/repository.py`：持久化新字段，增加 Session/Step/Finding 按作用域查询。

### Model、Agent、Worker 和恢复

- `src/dataharness/providers/model/openai_compatible.py`：实现标准库 HTTP 的
  OpenAI-compatible Chat Completions Adapter，支持 model/base_url/timeout/API-key env，
  工具调用回译和稳定错误分类。
- `src/dataharness/privacy/gateway.py`、`src/dataharness/orchestration/errors.py`：Provider
  错误保持稳定 code，并接入唯一 ModelGateway 和有限重试分类。
- `src/dataharness/agent/handler.py`：新增 AgentRunHandler，装配单一 PydanticAI Agent、
  AnalysisRuntime、Memory、Skills、checkpoint、Sandbox lease 和 VerificationService；
  缺 prompt、缺依赖、预算耗尽、策略阻断和模型配置错误进入稳定 WAITING。
- `src/dataharness/agent/context.py`、`models.py`、`runner.py`：checkpoint 记录 Sandbox
  digest/lease epoch/phase；按序列化上下文大小触发经 ModelGateway 的 compaction。
- `src/dataharness/worker.py`、`src/dataharness/cli.py`：新增独立 Worker 装配和
  `dataharness worker` 命令，正式路径使用 OpenSandboxProvider；测试可注入 fake cloud/sandbox。
- `src/dataharness/providers/durable/executor.py`：Run WAITING 时同步推进 Task WAITING，
  恢复/取消/预算耗尽的 API 生命周期保持一致。

### API、历史、图表和测试

- `src/dataharness/api/app.py`、`models.py`、`services.py`：新增 Snapshot、Session、prompt
  Task 创建接口；SSE 支持 `after`/`Last-Event-ID` 事件补发；增加正式 Artifact 内容读取。
- `src/dataharness/providers/memory/*`、`capabilities/memory/history.py`：历史条目增加
  Project/Session 作用域，生产 Handler 不再使用全局历史检索。
- `src/dataharness/analysis/charts.py`：新增 Vega-Lite Dataset ID/hash、大小、变换、URL、
  HTML、iframe、脚本字段校验，以及 SVG/PNG 静态兜底生成器。
- `src/dataharness/analysis/runtime.py`、`agent/tools.py`：新增受控 ChartArtifact 发布工具、
  Dataset/Step lineage 和从 Workspace 重建 AnalysisSummary 的接口。
- `tests/integration/test_phase11_agent_harness.py`、`tests/unit/test_phase11_provider.py`：
  覆盖 fake cloud E2E、prompt 不入 Runtime、Worker/Agent/SSE、Session 隔离、图表 Gate 和
  OpenAI-compatible 工具调用/缺 key 错误。

## 3. Interface and invariant changes

- 新的 HTTP Task 输入为 `project_snapshot_id + prompt + 可选 session_id`；prompt 原文不进入
  Runtime SQLite，固定引用为 `task:<task_id>:state:PROMPT.json`，hash 漂移即 WAITING。
- Session 历史查询必须同时带 Project 作用域，并可进一步带 Session 作用域；旧的无作用域
  MemoryCapability 仅保留给兼容测试，不由生产 Handler 装配。
- Worker 继续使用唯一 LocalDurableExecutor/SQLite lease；AgentRunHandler 不创建第二套
  loop、Planner、Reviewer 或工作流事实源。
- checkpoint 绑定 Run、Snapshot、Sandbox ID、镜像 digest 和 lease epoch；恢复不能切换到
  项目最新版本，Sandbox 丢失时按已校验规格重建。
- Agent 工具新增结构化 `InputReference/OutputSpec`、表格预览、Coverage、ChartArtifact
  发布；Python/SQL 仍只下沉到 AnalysisRuntime/OpenSandbox。
- SSE 只是 Runtime 事件的有序投影，事件 ID 可作为断线游标；不返回隐藏思考、原始 prompt、
  secret、PII 映射或无界 stdout/stderr。
- Run WAITING 与 Task WAITING 同步提交，`resume` 仍恢复同一个 Run/Snapshot；终态 Run 不重开。

## 4. Storage and migration impact

- Runtime schema 从 4 升到 5；迁移只追加 `sessions.project_id`、`tasks.prompt_ref`、
  `tasks.prompt_hash` 和索引。现有 Phase 00–10 数据可通过有序 migration 继续读取。
- Workspace 新增 `projects/<project>/snapshots/<snapshot>/` 只读视图和不可变
  `tasks/<task>/state/PROMPT.json`。Snapshot 视图可由既有 Snapshot 事实按需重建；旧输入
  版本和已发布资源不被覆盖。
- 对账仍由既有 WorkspaceBridge/PublicationJournal 负责；ChartArtifact 使用正式 Artifact
  表和 AVAILABLE 发布记录，Dataset→Chart、Step→Chart lineage 追加写入。
- 回滚需保留 migration 5 和对应代码，不建议直接降级已有数据库；应用代码仍兼容 prompt 为空
  的历史内部 Task。

## 5. Security and privacy impact

- 所有真实模型调用仍经 ModelGateway；Provider 只收到已脱敏 JSON。缺 key、鉴权失败、响应
  损坏和超时映射为不含请求正文的稳定 code。
- `/project` Sandbox 挂载改为固定 Snapshot 的物理视图，不再挂载包含其他版本和 Task 目录的
  Project 根目录；挂载白名单仍只有 `/project`、`/task/working`、`/task/staging`。
- prompt 在 Workspace 不可变保存；Runtime DB、Privacy DB、Host 凭据和 API Key 不写入
  prompt、checkpoint、SSE 或 Sandbox 挂载。
- Memory SQLite/FTS5 只返回 Project/Session 作用域命中；PII 仍使用既有 Task-local placeholder，
  secret 仍在 Gateway 前 fail-closed。
- Chart Gate 拒绝外链、脚本、HTML、iframe、内嵌 values、未登记 Dataset、未知变换和 hash
  漂移；前端可用纯 SVG/PNG 静态兜底，不执行 Agent 生成的代码。
- 负向测试继续覆盖路径穿越、符号链接、Runtime/Privacy 分离、secret/PII、恶意文件内容、
  Sandbox 约束和 Host 不执行生成代码。

## 6. Dependency changes

None。未新增 Python 依赖；OpenAI-compatible HTTP 使用 Python 标准库 urllib，图表 SVG/PNG
兜底使用标准库。`uv.lock` 未改变，现有 License/Notice 和镜像 digest 证据继续适用。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv lock --check` | `PASS` | 锁文件与项目依赖一致 |
| `uv run ruff format --check src tests` | `PASS` | 所有源码和测试已格式化 |
| `uv run ruff check src tests` | `PASS` | All checks passed |
| `uv run pyright` | `PASS` | 0 errors, 0 warnings, 0 informations |
| `uv run pytest -q` | `PASS` | 227 passed, 7 live OpenSandbox tests explicitly skipped |
| `uv run pytest -q tests/integration/test_phase11_agent_harness.py tests/unit/test_phase11_provider.py` | `PASS` | 4 passed；fake cloud Worker、SSE、Session scope、Provider error/tool-call |
| `uv run python scripts/verify.py` | `PASS` | Phase 10 baseline/结构检查与测试入口继续通过 |

真实云模型 smoke test 未在默认验收中运行；需要用户自己的 API Key 和模型服务。真实
OpenSandbox live 测试仍使用既有 `DATAHARNESS_LIVE_SANDBOX=1` 独立命令，默认测试不会访问
公网或真实凭据。

## 8. Exit Gate evidence

- **HTTP 问题驱动真实 Agent 闭环**：`test_phase11_prompt_worker_agent_and_sse_replay` 通过
  HTTP 创建含 prompt 的 Task，独立 Worker 领取 Run，fake cloud 至少调用
  `list_project_files`，最终 Run/Task 为成功，且 SSE 能读取同一 Runtime 事件序列。fake
  Sandbox 只作为不执行代码的测试 Adapter，生产 Worker 装配使用 OpenSandboxProvider。
- **Provider 不绕过 ModelGateway**：Agent 仍由 `gateway_function_model` 调用 Gateway；
  `test_openai_compatible_provider_maps_tool_call_without_leaking_request` 检查 OpenAI-compatible
  请求转换，缺 key 测试检查 `MODEL_API_KEY_MISSING` 且不回显环境变量名。
- **Python/SQL 固定 Sandbox**：Agent 工具只能调用 AnalysisRuntime；SandboxSpec 只声明固定
  digest、Snapshot 只读和当前 Task 写域，既有 sandbox contract/live 入口继续通过。
- **checkpoint/恢复/不重复发布**：checkpoint 现在含 Snapshot、Sandbox ID/digest、lease epoch；
  既有 durable recovery/idempotency/AnalysisRuntime 测试与 Phase 11 Worker E2E 共同证明恢复
  使用同一 Run，正式资源由既有 idempotency/journal 保护。
- **Session/Project 隔离**：Session API 写入 Project 归属，Task 创建再次校验；Phase 11
  Memory fake 测试证明相同查询不会跨 Project 命中；Snapshot 物理视图只复制固定 READY 版本。
- **上下文压缩不是事实源**：ContextCompactor 仍经 ModelGateway，保留结构化 state、稳定
  ResourceRef 和最近消息；checkpoint metadata 不含原始模型载荷。
- **ChartArtifact Gate**：Vega-Lite validator 检查 Dataset ID/hash、大小、URL/脚本/HTML/iframe、
  变换白名单和 hash；Artifact 发布使用 AVAILABLE Workspace 记录及 Dataset/Step lineage。
- **测试命令独立且无真实凭据**：Phase 11 fake-cloud E2E 和 Provider unit tests 可单独运行；
  真实 OpenSandbox live 和可选真实模型 smoke 使用独立环境变量/命令，默认全量测试明确 skip live。

## 9. Architecture deviations and decisions

None。实现复用已有 LocalDurableExecutor、ModelGateway、AnalysisRuntime、WorkspaceBridge 和
OpenSandbox seam，没有引入第二个 Agent loop、Prefect、向量数据库、在线数据库或 Host 代码执行。
Snapshot 物理视图是对既有“Snapshot 只读挂载”承诺的具体化，不改变架构边界。

## 10. Known issues and technical debt

- 默认 `dataharness serve` 与 `dataharness worker` 仍需分别启动，OpenSandbox Server 仍需独立
  运行；Phase 13 负责一键生命周期管理。
- OpenAI-compatible Provider 当前支持 Chat Completions 形状，不包含 Anthropic 原生协议、
  流式 token 或供应商专属响应字段；后续应以独立 Adapter 扩展。
- Snapshot 物理视图复制 READY 文件，会增加本机磁盘占用；后续可在保持不可变 hash 的前提下
  评估硬链接/内容寻址存储，但不能退回挂载项目根目录。
- 真实模型 smoke 和默认全量测试中的 OpenSandbox live 仍需要外部服务/凭据，未被 fake 测试
  冒充为默认通过。
- PII 检测仍是规则型 best-effort；本地个人版不提供团队版认证、多租户或公网安全承诺。

## 11. Next-phase entry check

满足 Phase 12 入口：稳定的 Task prompt、Project-scoped Session、Task answer、资源内容、
Runtime event/SSE、WAITING/resume 和 ChartArtifact validator 已提供给 WebUI；`dataharness
worker` 可在开发期独立运行。Phase 12 仍需建立 React/Vite/Ant Design/TanStack Query/Router/
Vega-Lite 前端、OpenAPI 类型校验和浏览器级刷新/断线验收，不得在前端执行 Agent 生成代码。
