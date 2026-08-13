# Phase 00 Completion Report: Engineering foundation

- Status: `COMPLETED`
- Date: `2026-08-13`
- Plan phase: `Phase 00`
- Commit/revision: `30bbe1e`（“补充 Phase 00 测试 fixtures 与单元测试”）

## 1. Objective and scope

建立所有后续模块共用的 Python 工程骨架：打包与锁文件、配置模型、依赖方向检查、
CLI/最小应用入口、测试 fixtures 与统一验证脚本，不接入真实模型或公网。

本阶段补齐 Phase 01 实施时仅临时搭建的最小脚手架，使其成为正式、可复现的工程基础。

## 2. Detailed changes

### 打包与工程配置

- `pyproject.toml`：新增 `[project.scripts]` 注册 `dataharness = "dataharness.cli:main"`；
  沿用 hatchling 构建 + src 布局，以及 Ruff/Pyright/pytest/Hypothesis 配置。
- `src/dataharness/__init__.py`：新增 `__version__ = "0.1.0"`。
- `uv.lock`：维持锁定，`uv lock --check` 通过。

### 配置模型 `src/dataharness/config.py`

- `PathsConfig`（runtime_data_root / projects_root / privacy_root，子根缺省派生，`runtime_db` 属性）、
  `ModelProviderConfig`（api_key 仅存环境变量名）、`SandboxConfig`（默认断网）、
  `ExtractionConfig`、`IndexConfig`（BM25）、`BudgetConfig`、`ResourceLimitsConfig` 与顶层 `Settings`。
- `load_settings(path)`：用内置 `tomllib` 解析 TOML 并经 Pydantic 校验。

### 基础能力

- `src/dataharness/idgen.py`：`IdFactory` 协议 + `UuidIdFactory` + `DeterministicIdFactory`。
- `src/dataharness/testing.py`：`FakeClock` 与合成数据助手 `synthetic_csv_bytes`/`synthetic_text_bytes`。
- `src/dataharness/tooling/dependency_check.py`：AST 层面的依赖方向静态检查，返回结构化违规列表，
  规则含 domain 禁入框架与内部包反向导入 api 两类。

### 入口与验证

- `src/dataharness/cli.py` + `__main__.py`：最小 CLI，`check` 子命令加载并校验本地配置。
- `dataharness.example.toml`：示例配置（不含密钥）。
- `scripts/verify.py`：本地统一验证脚本（lock/format/lint/typecheck/pytest）。

### 测试

- `tests/conftest.py`：共享 fixtures（`fake_clock`、`id_factory`、`runtime_layout`、`sample_csv_bytes`）。
- `tests/unit/test_config.py`、`test_idgen.py`、`test_testing.py`、`test_dependency_check.py`、
  `test_cli.py`；`test_domain_purity.py` 重构为复用依赖检查器。

## 3. Interface and invariant changes

- 新增 `dataharness.config.Settings` / `load_settings`、`dataharness.idgen.*`、
  `dataharness.testing.*`、`dataharness.tooling.dependency_check.*` 与 `dataharness.cli.main`。
- 配置不变量：默认 Sandbox 断网；密钥仅经环境变量名引用，不落配置/日志；子路径根缺省从
  runtime_data_root 派生；路径默认指向本地 `runtime-data/`。
- 依赖方向规则固化于 `dependency_check.dataharness_rules()`。

## 4. Storage and migration impact

`None`。未改动 Runtime SQLite schema、无迁移、无 Workspace 布局变化；仅定义默认路径常量。

## 5. Security and privacy impact

- 配置模型明确“API 密钥不写入配置文件”，默认 Sandbox 断网，默认监听 127.0.0.1 相关项未引入公网暴露。
- 依赖方向检查把“domain 不导入 FastAPI/PydanticAI/OpenSandbox/SQLite/遥测 SDK”“内部包不反向导入 api”
  固化为一等检查，供 CI/验证脚本持续执行。
- 无凭据、无 PII、无真实数据进入测试或日志。

## 6. Dependency changes

- 无新增运行时依赖（沿用 `pydantic>=2.7`）。dev 依赖组已在 Phase 01 建立并锁定。
- TOML 解析使用标准库 `tomllib`，未新增解析依赖。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv sync --offline` | PASS | 仅凭已提交 `uv.lock` + 缓存重建环境（开发期网络抖动，见 §10） |
| `uv lock --check` | PASS | 锁文件与 pyproject 一致 |
| `uv run dataharness check --config dataharness.example.toml` | PASS | 配置校验通过，正确派生 projects/privacy 根 |
| `uv run python scripts/verify.py` | PASS | 全部检查通过 |
| `uv run ruff format --check .` | PASS | 82 文件已格式化 |
| `uv run ruff check .` | PASS | 0 错误 |
| `uv run pyright` | PASS | 0 errors, 0 warnings |
| `uv run pytest -q` | PASS | 119 passed |

## 8. Exit Gate evidence

- **全新环境可仅凭锁文件安装并运行测试**
  `uv sync --offline`（锁定依赖，不重新解析网络）后 `uv run pytest -q` 通过 119 项。
- **最小应用可启动并读取经过 Pydantic 校验的本地配置**
  `uv run dataharness check --config dataharness.example.toml` 输出“配置校验通过”，
  非法配置路径经 `test_cli.py::test_check_invalid_config_fails` 证明返回非零退出码。
- **lint、format、type-check、unit test 可由一组记录明确的命令完成**
  `uv run python scripts/verify.py` 依次执行并全部通过。
- **依赖方向的正例和负例测试均通过**
  `test_dependency_check.py`：正例 `test_dataharness_passes_dependency_check`（现有代码零违规）、
  负例 `test_flags_forbidden_framework*` 与 `test_flags_reverse_internal_import` 均通过。

## 9. Architecture deviations and decisions

- 新增 `config`、`idgen`、`testing`、`tooling` 与 `cli` 顶层模块，属于 Phase 00 既定交付物，
  不改变架构的运行时依赖方向；`tooling`/`testing` 明确标注为非运行时模块。
- 未引入 CI 工作流文件，采用计划允许的“本地统一验证脚本”（`scripts/verify.py`）作为 CI 命令。

## 10. Known issues and technical debt

- **开发期网络抖动**：`uv sync` 期间 pypi 连接被拒，改用 `uv sync --offline` 完成（依赖已缓存）。
  后续联网环境应执行一次 `uv sync` 确认锁文件可在全新环境完整下载。
- `idgen.DeterministicIdFactory.new` 的前缀参数与 `UuidIdFactory.new` 签名略有差异（可选 vs 必填），
  后续若需多态替换可统一为一致的必填签名。

## 11. Next-phase entry check

Phase 01 已先行完成并标记 COMPLETED；Phase 00 现在补齐后，Phase 00→01 依赖链闭环。
进入 Phase 02（Runtime storage）的前置条件已满足：配置模型提供 SQLite 路径与根目录；
`Settings`/`load_settings` 可供 storage 连接工厂使用；依赖方向检查与统一验证脚本可保证后续
各阶段持续受控。
