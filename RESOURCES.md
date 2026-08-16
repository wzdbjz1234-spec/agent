# RESOURCES

用于为 DataHarness 教程提供知识的权威资源清单。按优先级排列。

## 官方文档（首选知识来源）

- **FastAPI 官方文档** — https://fastapi.tiangolo.com/ — 路由、依赖注入、中间件、异常处理。对应课程 03。
- **Pydantic 官方文档** — https://docs.pydantic.dev/latest/ — BaseModel、校验器、ConfigDict。对应课程 02/05。
- **PydanticAI 官方文档** — https://ai.pydantic.dev/ — Agent、工具、结构化输出、UsageLimits。对应课程 08。
- **Python 官方教程** — https://docs.python.org/3/tutorial/ — 类型标注、async/await 基础。对应课程 02。
- **SQLite FTS5 文档** — https://www.sqlite.org/fts5.html — BM25 全文检索。对应课程 07。
- **SQLite WAL 与事务** — https://www.sqlite.org/wal.html — WAL 模式、busy_timeout。对应课程 06。
- **uv 文档** — https://docs.astral.sh/uv/ — `uv sync`、`uv run`。对应课程 01/13。
- **OpenTelemetry Python** — https://opentelemetry.io/docs/languages/python/ — 遥测。对应课程 03/11。

## 项目参考（本仓库内）

- `ARCHITECTURE.md` — V1 架构基线（威胁模型、设计原则、状态机、验收链路）。**每个课程都应先读对应章节。**
- `README.md` — 运行与验收操作手册。
- `doc/decision-001/002/003` — 关键设计决策记录。
- `doc/phase-XX-*.md` — 各阶段开发报告（含验收证据）。

## 复用/参考的开源项目（代码级学习材料）

- **OpenSandbox** — https://github.com/opensandbox-group/OpenSandbox — 沙箱平台，V1 唯一 Sandbox Provider。对应课程 10。
- **Pydantic AI Harness** — https://github.com/pydantic/pydantic-ai-harness — Skills、Compaction。对应课程 08/09。
- **DuckDB** — https://duckdb.org/docs/ — 沙箱内数据分析。对应课程 10/13。
- **Microsoft Presidio** — https://github.com/microsoft/presidio — PII 检测参考。对应课程 09。

## 社区（获取 Wisdom）

- r/LocalLLaMA — LLM 本地应用实践。
- r/Python — 通用 Python 与 Web 开发问题。
- PydanticAI GitHub Discussions — Agent 框架问题。
- OpenSandbox GitHub Discussions — 沙箱隔离问题。
