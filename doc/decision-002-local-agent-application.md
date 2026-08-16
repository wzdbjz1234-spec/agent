# Decision 002: Local Agent Application, WebUI and Deployment

- Status: Accepted
- Date: 2026-08-15
- Affects plan: 1.2

## Context

Phase 00–10 已完成 DataHarness 的核心领域模型、ProjectCorpus、隐私边界、OpenSandbox、AnalysisRuntime、耐久编排、PydanticAI Agent 组件、验证 Gate 和 FastAPI 控制面。但是当前 `dataharness serve` 只启动 API：Task 不包含用户问题，真实模型 Provider、AgentRunHandler、Worker 生命周期和 WebUI 均未完成，因此创建 Task 不会执行 Agent，也不会创建 Sandbox 容器。

产品第一目标是个人本机工具，后续可能演进为团队内部平台。新的应用化工作必须复用已有事实源和安全边界，不能用前端轮询状态、FastAPI 后台任务、任意模型脚本或 Docker Compose 隐式状态替代 Runtime SQLite、LocalDurableExecutor、ModelGateway 和 OpenSandbox。

## Decision

### Product boundary

- 第一版是个人本机浏览器工具，只绑定回环地址，不提供登录、多租户、共享访问或公网安全承诺。
- 核心交互采用 Project 工作台和对话式分析；自由问题为主，快捷分析模板只负责填充问题，不建立第二套任务类型。
- Session 必须绑定单一 Project；同一 Session 可以连续追问，但每条用户消息创建独立 Task，每个 Task 固定当时的 ProjectSnapshot。
- Agent 在预算和安全边界内自主使用工具；歧义、输入缺失、预算耗尽、策略阻断或需要网络等越界能力时进入 WAITING。

### Agent Harness

- 继续使用单一 PydanticAI Agent，不引入 Planner、Executor、Reviewer 多 Agent 编排，也不实现第二套 agent loop。
- 新增生产 `AgentRunHandler`，内部装配 AgentRunner、ModelGateway、AnalysisRuntime、OpenSandbox、checkpoint、Skills、Memory、发布和 Verification Gate。
- 首个真实模型实现为 OpenAI-compatible CloudModelProvider，通过本地 TOML 的 `model`、`base_url`、超时和 `api_key` 配置；所有模型调用必须经过 ModelGateway。
- 上下文分为当前消息、结构化工作状态、稳定领域引用和 Project + Session 作用域历史。摘要只用于压缩对话，不作为事实证据。
- 在 AnalysisStep、发布、验证、WAITING、完成和 compaction 等关键边界保存 checkpoint；恢复固定使用原 Run、Snapshot、Sandbox digest 和稳定资源引用。
- Agent 工具仍保持窄接口。Python、SQL 和 Skill 只在 OpenSandbox 执行，不能访问 Host shell、网络、运行时安装、Runtime/Privacy SQLite 或任意主机路径。

### Events and user interaction

- 用户命令使用普通 HTTP，任务进度使用 SSE 单向推送；不引入 WebSocket。
- SSE 使用 Runtime 事件序号支持断线补发，不作为事实源。
- UI 默认展示简化进度，允许展开工具名称、耗时、输入/输出摘要和资源引用；不展示模型隐藏思考过程或敏感原文。

### Chart rendering

- Agent 发布 Dataset 与受控 Vega-Lite JSON；Host 校验 schema、Dataset ID/hash、大小、变换和数据来源后，前端才能渲染。
- 图表规范不得包含外部 URL、JavaScript 函数、任意 HTML、iframe 或未发布数据引用。
- 同时支持 PNG/SVG Artifact 作为渲染失败、报告导出和长期复现的静态兜底。
- 前端统一提供图表、数据表和图表说明视图。

### WebUI

- 使用 React、TypeScript、Vite、Ant Design、TanStack Query、React Router 和 Vega-Lite。
- MVP 只包含项目列表、项目工作台、Session 对话、Task 结果四个核心页面，以及一个本地诊断设置抽屉。
- 开发期由 Vite 代理 API/SSE；发布时 FastAPI 同源托管预构建静态资源，最终用户不需要 Node.js。
- 前端 API 类型从 FastAPI OpenAPI 生成或在 CI 中校验，避免 DTO 漂移。

### Local deployment

- API、Worker 和 OpenSandbox 作为三个独立宿主进程运行，由 `setup.ps1`、`start.ps1`、`stop.ps1` 和 `status.ps1` 统一管理。
- Docker 只负责按需创建受控 Sandbox、execd 和 egress 容器；个人版不强制将 API 或 Worker 容器化。
- 第一版要求 Docker Desktop 和 uv，不制作 `.exe` 或 `.msi`；前端构建产物随发布包交付。
- API Key 只通过未纳入版本控制的本地 TOML 配置，UI 仅显示配置状态，不读取或回显密钥。

### Team evolution

- 团队版作为后续独立阶段，必须重新建立认证、RBAC、租户隔离、TLS、CSRF/CORS、密钥管理和共享环境威胁模型。
- 保留 API、Worker、OpenSandbox 和 Web 的独立边界，使 Runtime SQLite、LocalWorkspace 和本机脚本未来可以替换为 PostgreSQL、对象存储、耐久队列和服务编排。
- 个人版的回环地址、单用户文件权限和本地 TOML 密钥假设不得直接沿用到团队部署。

## Consequences

- Phase 08 的 Agent 组件仍是有效历史交付；生产运行闭环由新增 Phase 11 完成，不重写旧阶段报告。
- 前端开发依赖 Phase 11 的真实 Task prompt、事件、WAITING 和资源接口，不能用 mock 行为宣称应用可发布。
- 本机也维持独立 Worker 进程，增加少量进程管理成本，但降低 API 重启对长任务的影响，并为团队版拆分保留边界。
- Vega-Lite 规范和静态兜底会增加 ChartArtifact 验证与导出工作，但避免执行 Agent 生成的脚本和 HTML。
- OpenAI-compatible 首发减少 Provider 数量；Anthropic 原生协议留作后续独立 Adapter。
- Session 历史必须增加 Project 过滤和迁移，当前无作用域的全局检索不得在生产装配中启用。

## Rejected alternatives

- 多 Agent Planner/Executor/Reviewer：增加模型成本、状态恢复和事实一致性复杂度，且违反单 Agent 与确定性 Host Gate 的现有边界。
- FastAPI lifespan 内运行 Worker：API 重载、退出和故障会与长任务生命周期耦合。
- 第一版本全部 Docker Compose：Windows Host 路径、Docker socket 和 OpenSandbox 挂载语义更复杂；个人版先使用独立宿主进程。
- Electron/Tauri：安装体验更原生，但首版会引入额外打包、签名、升级和进程管理成本。
- WebSocket：当前交互主要是服务端单向事件，SSE 加普通 HTTP 已满足重连和控制需求。
- Agent 生成 Plotly HTML 或任意前端脚本：扩大 XSS、资源加载和不可复现风险。
- 首版直接实现团队平台：认证、多租户、共享存储和分布式执行会延迟个人版闭环，也会错误沿用本机信任假设。

## Rollout

1. Phase 11 完成生产 Agent Harness 和可恢复执行闭环。
2. Phase 12 在稳定 API/SSE/Artifact 接口上实现本机 WebUI。
3. Phase 13 固化独立进程、一键脚本和干净环境发布验收。
4. Phase 14 只在个人版稳定后设计团队迁移，不提前宣称或暴露共享服务能力。
