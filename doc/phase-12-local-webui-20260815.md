# Phase 12 Completion Report: Local WebUI

- Status: `COMPLETED`
- Date: `2026-08-15`
- Plan phase: `Phase 12`
- Commit/revision: `working tree；未创建 checkpoint commit，保留既有 Phase 11 与用户原有修改`

## 1. Objective and scope

本阶段为个人本机用户提供项目、文件版本、Snapshot、连续 Session、Task 执行进度、WAITING 恢复和结构化证据结果的一体化浏览器工作台。实现范围包括 `web/` React/Vite 应用、FastAPI 同源静态托管、开发期 API/SSE 代理、OpenAPI 路由契约校验、组件/API mock/Playwright/可访问性测试，以及阶段12所需的三个只读或状态补充 API。

本阶段不实现 Phase 13 的一键安装、进程监督、Docker/OpenSandbox 生命周期管理，也不引入认证、多租户、公网监听或真实模型凭据。

## 2. Detailed changes

### Backend and API

- `src/dataharness/api/app.py`：`create_app` 支持可选 WebUI 静态目录；仅对带 `Accept: text/html` 的前端导航提供 SPA fallback，保留 `/docs`、`/openapi.json`、健康检查和 JSON API 的语义；在构建目录存在时挂载 `StaticFiles`。
- `src/dataharness/api/app.py`、`src/dataharness/api/services.py`：增加项目归档、项目任务列表和本地诊断接口。诊断只返回 API/Worker/模型/Sandbox 配置状态、镜像配置状态、数据目录和磁盘剩余空间，不返回密钥。
- `src/dataharness/storage/repository.py`：从已有 Runtime task 表按 Project 查询任务，沿用现有事实源和排序规则，不新增表。
- `src/dataharness/cli.py`：`dataharness serve` 自动发现仓库根目录下的 `web/dist`，生产运行时不需要 Node.js。
- `scripts/export_openapi.py`：从 FastAPI 应用导出 `web/openapi.generated.json`；`web/scripts/check-openapi.mjs` 校验项目、文件、Snapshot、Session、Task、事件流、答案、Finding、lineage 和 diagnostics 等 13 个关键路径。
- `tests/integration/test_phase12_webui.py`：覆盖项目归档、项目任务、诊断脱敏、同源静态首页、前端路由刷新和健康检查。

### WebUI

- `web/`：建立 React 19 + TypeScript + Vite 应用，使用 Ant Design、TanStack Query、React Router、Vega-Lite/react-vega；实现 `/projects`、`/projects/:projectId`、`/projects/:projectId/sessions/:sessionId` 和 `/tasks/:taskId`。
- `web/src/api/`：统一 DTO、API client、query keys 和 hooks；JSON 错误转换为稳定的用户错误；文件上传使用二进制请求和 `X-File-Name`；查询缓存不作为事实源。
- `web/src/api/hooks.ts`：先从 API 恢复任务和历史事件，再通过 `after`/`Last-Event-ID` 建立 SSE；断线自动重连；事件内存最多保留最近 100 条；终态和 WAITING 不继续建立无意义的流连接。
- `web/src/components/AppShell.tsx`：提供项目/任务导航和本地诊断抽屉；只展示安全的配置状态。
- `web/src/components/ChartRenderer.tsx`：只接受绑定正式 Dataset/Artifact 的已校验 Vega-Lite JSON；拒绝 URL、HTML、iframe、JavaScript、signal/expression 等字段，图表 JSON 上限 256 KiB，支持图表、数据、说明切换，渲染失败时回退 PNG/SVG 正式产物。
- `web/src/pages/`：实现项目创建/归档/最近任务、文件上传/版本/状态/检索入口、Snapshot/Session、模板问题/自由问题/连续任务、取消/恢复/重试、WAITING 原因和结构化结果展示。
- `web/src/test/`、`web/e2e/`：加入 API 错误与上传测试、页面和 ChartRenderer 测试；Playwright 覆盖真实 FastAPI 的项目/上传/Snapshot/Session/Task/SSE/取消流程，以及确定性 mock 的 WAITING/恢复/图表证据流程；`@axe-core/playwright` 覆盖项目入口关键 WCAG 扫描。
- `web/vite.config.ts`、`web/playwright.config.ts`、`web/eslint.config.js`、`web/openapi.generated.json`：提供开发代理、生产构建、Chrome 复用、ESLint、OpenAPI 契约和测试配置。

### Documentation and fixtures

- `README.md`：补充 Phase 12 WebUI 的开发、构建、检查、测试和 FastAPI 同源运行说明。
- `.gitignore`：忽略 `web/node_modules`、Playwright 报告和测试结果。
- `tests/fixtures/phase12-web-e2e.csv`：仅包含合成的月份/数值数据，用于浏览器上传流程。
- `web/package.json`、`web/pnpm-lock.yaml`：记录前端运行时、测试和可访问性依赖。

## 3. Interface and invariant changes

- 新增 `POST /projects/{project_id}/archive`、`GET /projects/{project_id}/tasks` 和 `GET /diagnostics`；项目、Session、Task 的详情仍由 Runtime/API 事实源提供。
- Task SSE 帧包含 `id`、事件类型和安全的 `EventRecord` JSON；客户端使用事件序号补发和重连，SSE 只负责进度展示，不替代 API/Runtime 事实源。
- WebUI 只消费结构化 TaskAnswer、Dataset、Artifact、Finding 和 lineage；不显示隐藏思考、原始模型消息、原始 prompt 或任意工具脚本。
- 项目页面绑定 Project，Session 页面绑定 Project/Session，Task 页面从 Task ID 重新读取状态；新上传文件不会改变既有 Task 的 Snapshot 或已发布证据引用。
- WAITING 状态展示稳定等待原因；恢复操作调用 `/resume` 后重新读取 Task 并恢复进度监听；取消和重试使用各自的后端状态迁移，不由前端本地伪造终态。
- 图表的事实来源必须是正式 Artifact 内容及其 `content_hash`/Dataset 引用；安全校验失败时 fail closed，不执行外部 URL、HTML 或脚本。
- 文件检索、任务查询和事件显示均采用后端限制或前端有界预览；长事件流最多保留 100 条，图表规格最多读取 256 KiB。

## 4. Storage and migration impact

没有新增 SQLite schema 或 migration。项目归档复用现有 Project 状态，项目任务列表复用已有 `tasks` 表，前端 React Query 缓存只用于视图加速，不是持久事实源。

没有改变 Workspace 目录布局、Snapshot 兼容性或历史 Task 数据格式；`web/dist` 是可重建构建产物并由 Git 忽略。回滚 WebUI 不需要数据回滚；保留新增 API 路由也不会改变既有 Runtime 表结构。

## 5. Security and privacy impact

- 默认服务仍绑定回环地址；生产构建由 FastAPI 同源提供，不放宽生产 CORS，不暴露公网默认监听。
- 诊断抽屉只显示“已配置/未配置”、状态、镜像配置状态、数据目录和磁盘容量；不会显示 API Key、密钥值或原始环境变量。
- SSE 和页面结果不输出隐藏思考、原始模型载荷、完整 prompt、任意本地路径或执行脚本；工具轨迹只展示安全摘要和正式资源引用。
- ChartRenderer 对规格大小和危险字段 fail closed；下载/预览仅通过正式 Artifact API，不把 Agent 生成内容当 HTML、脚本或任意 URL 执行。
- 浏览器 E2E 只使用 `tests/fixtures/phase12-web-e2e.csv` 的合成数据；默认测试不使用真实云账号、真实 API Key 或生产数据。

## 6. Dependency changes

- 新增前端直接依赖：React、Ant Design、TanStack Query、React Router、react-vega/Vega/Vega-Lite；开发依赖包括 Vite、TypeScript、Vitest、Testing Library、ESLint、Playwright 和 `@axe-core/playwright`。
- `web/pnpm-lock.yaml` 已更新；`pnpm install --ignore-scripts` 通过 pnpm lockfile supply-chain policy 校验。Python 依赖未新增，`uv.lock` 未改变。
- 本阶段没有接入新的外部服务或凭据。当前仓库未配置独立的前端 License/SBOM/漏洞扫描，作为 Phase 13 发布包的技术债记录；本阶段至少完成锁文件与安装策略校验。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv lock --check` | `PASS` | Lockfile resolved successfully。 |
| `uv run ruff format --check src tests scripts` | `PASS` | 182 files already formatted。 |
| `uv run ruff check src tests scripts` | `PASS` | 无 lint 错误。 |
| `uv run pyright` | `PASS` | 0 errors, 0 warnings, 0 informations。 |
| `uv run pytest -q` | `PASS` | 229 passed，7 个 OpenSandbox live 测试因未设置 `DATAHARNESS_LIVE_SANDBOX` 跳过。 |
| `uv run pytest -q tests/integration/test_phase12_webui.py` | `PASS` | 2 passed。 |
| `pnpm install --ignore-scripts` | `PASS` | lockfile supply-chain policies 通过。 |
| `& 'C:\\Users\\34447\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\bin\\node.exe' .\\node_modules\\typescript\\bin\\tsc --noEmit` | `PASS` | TypeScript 类型检查通过。 |
| `& 'C:\\Users\\34447\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\bin\\node.exe' .\\node_modules\\eslint\\bin\\eslint.js .` | `PASS` | ESLint 检查通过。 |
| `& 'C:\\Users\\34447\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\bin\\node.exe' .\\scripts\\check-openapi.mjs` | `PASS` | 13 个关键 OpenAPI 路径通过。 |
| `& 'C:\\Users\\34447\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\bin\\node.exe' .\\node_modules\\vitest\\vitest.mjs run` | `PASS` | 3 个测试文件、4 个测试通过。 |
| `& 'C:\\Users\\34447\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\bin\\node.exe' .\\node_modules\\vite\\bin\\vite.js build` | `PASS` | 生产静态产物构建成功；Vite 提示主 chunk 约 2.19 MB，已记录为技术债。 |
| `& 'C:\\Users\\34447\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\bin\\node.exe' .\\node_modules\\@playwright\\test\\cli.js test --reporter=line` | `PASS` | 3/3：真实 API 工作流、WAITING/ChartArtifact mock、关键可访问性扫描。 |
| In-app Browser 手工验收：FastAPI `dataharness serve` + `/projects` | `PASS` | 创建项目、上传合成 CSV、创建 Snapshot/Session、提交问题、观察 SSE、刷新恢复、取消 Task、打开诊断抽屉均完成。 |

前端检查也可通过仓库脚本复现：`pnpm run typecheck`、`pnpm run lint`、`pnpm run openapi:check`、`pnpm run test`、`pnpm run build` 和 `pnpm run test:e2e`；本报告中的直接 Node CLI 命令均为这些脚本对应的实际执行命令。

## 8. Exit Gate evidence

### 用户只通过 WebUI 即可创建 Project、上传文件、创建 Session、提交问题、观察执行、处理 WAITING 并查看最终证据结果

真实 Playwright/浏览器流程覆盖项目创建、上传、Snapshot、Session、问题提交、Task 页面、SSE 进度和取消；Playwright 的确定性 WAITING 分支覆盖等待原因、恢复和结构化 Dataset/Artifact/图表证据显示。没有真实模型凭据时，最终证据使用受控 API mock 验证，避免把云凭据引入默认测试。

### 页面刷新和 SSE 断线不丢失事实状态；重新进入 Task 时从 API/Runtime 恢复，而不是依赖浏览器内存

Task 页面启动时先读取 Task、答案和历史事件，SSE 使用 `after`/`Last-Event-ID` 补发；Playwright 覆盖 WAITING 页面重新读取及恢复，手工浏览器验收覆盖刷新后恢复 Task 状态。客户端事件列表是有界展示缓存，不是事实源。

### 前端不执行 Agent 生成的脚本、HTML 或任意 URL；图表和下载只读取经过验证的正式资源

`ChartRenderer` 对 Vega-Lite 规格做危险字段、绑定 Dataset/Artifact、hash 和大小检查；图表测试和 Playwright ChartArtifact mock 验证图表/数据/说明界面。代码没有 `dangerouslySetInnerHTML` 或任意 URL 渲染路径。

### 大文件、大 Dataset 和长事件流使用分页、有界预览或下载，不把完整载荷无界加载到浏览器

项目任务/检索使用 API limit，事件在客户端最多保存 100 条，图表规格最多读取 256 KiB；正式 Artifact 通过受控内容接口读取，未将完整 Runtime 数据库或模型原文加载到浏览器。

### WebUI 构建产物可由 FastAPI 在回环地址同源提供；不存在生产 CORS 放宽或公网监听默认值

`tests/integration/test_phase12_webui.py` 验证首页、前端路由刷新和 `/healthz`；`dataharness serve` 自动挂载 `web/dist`。Vite 代理只在开发期生效，生产路径不依赖 Node.js，也没有新增 CORS 配置。

### Playwright 覆盖项目创建、文件上传、问题提交、SSE 进度、图表显示、取消、WAITING 和恢复

`web/e2e/local-workflow.spec.ts` 的 3 个用例全部通过：真实 FastAPI 操作链路、WAITING/恢复/ChartArtifact、关键可访问性扫描；真实链路覆盖创建/上传/问题/SSE/取消，mock 链路覆盖 WAITING/图表/恢复。

## 9. Architecture deviations and decisions

None。实现遵循 `doc/decision-002-local-agent-application.md` 和既有架构：前端只消费 API/Runtime 正式事实，Agent/Worker/Sandbox 不移入浏览器，不新增第二套 Agent loop，不提供公网部署。

为支持前端路由刷新而增加的 `Accept: text/html` SPA fallback、归档/任务列表/诊断路由都是 API 层的增量接口，不改变既有事实源、信任边界或状态机。

## 10. Known issues and technical debt

- `worker` 诊断当前显示 `not_reported`；Phase 13 进程监督和 heartbeat 接入后再提供真实 Worker 状态。责任阶段：Phase 13；影响：只影响诊断展示，不影响 API/Task 事实。
- Vite 生产主 chunk 约 2.19 MB，构建有大 chunk warning；后续可通过路由级 dynamic import 和 manual chunks 优化。责任阶段：Phase 13/后续性能迭代。
- 7 个真实 OpenSandbox 集成测试因未设置 `DATAHARNESS_LIVE_SANDBOX` 跳过；这是无 Sandbox/无真实凭据环境下的预期限制，不代表 WebUI mock 流程失败。责任阶段：具备 Sandbox 的验收环境。
- Playwright 启动 FastAPI 时仍会看到既有 OpenTelemetry `Span 关闭失败` 日志；不影响 HTTP、测试断言或退出码，后续由观测性模块处理。
- 前端尚未纳入独立 License/SBOM/漏洞扫描；Phase 13 发布包必须补齐 License/Notice 和依赖扫描证据。

## 11. Next-phase entry check

满足 Phase 13 入口条件：

- 可重建的 `web/dist`、`web/package.json`、`web/pnpm-lock.yaml`、OpenAPI 快照和 Playwright fixture 已在工作树中。
- `dataharness serve` 已具备同源静态托管路径；Phase 13 只需将构建产物纳入 setup/start/status 生命周期，不需要改变 WebUI 的事实源契约。
- 诊断接口已预留 API、Worker、模型、Sandbox、数据目录和磁盘字段；Phase 13 可接入真实进程 PID/heartbeat/镜像 digest。
- 无需 migration、数据对账或前端缓存迁移。进入 Phase 13 时需携带的风险是主 chunk 体积、OpenTelemetry 关闭告警、真实 OpenSandbox/Provider smoke test 和发布包依赖合规扫描。
