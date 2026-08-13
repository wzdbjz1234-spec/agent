# Phase 03 Completion Report: Project corpus, workspace, import and publication

- Status: `COMPLETED`
- Date: `2026-08-13`
- Plan phase: `Phase 03`
- Commit/revision: `working tree（包含 Phase 02 未提交基线及本阶段增量）`

## 1. Objective and scope

本阶段完成长期 Project 语料、不可变文件版本、本地多格式提取、FTS5/BM25 检索、
不可变 ProjectSnapshot、Task 隔离写域，以及可在 SQLite/文件系统双写中断后恢复的正式
输出发布协议。

实现范围包含 CSV、Parquet、Excel、JSON、PDF、DOCX、PPTX、Markdown、TXT、SQLite
快照和可选 DuckDB 快照。图片 OCR、音视频、在线数据源、语义向量检索、Dataset/Artifact
lineage 注册不在本阶段；前两项显式记录 `UNSUPPORTED`，lineage 由 Phase 06 负责。

## 2. Detailed changes

- `projects`：新增 `ProjectCorpus`，集中提供项目创建/归档、文件导入、派生物重建、
  Snapshot、RELEVANT 检索、有界资源读取和 FULL_PROJECT Coverage；新增内容格式嗅探、
  内部提取器、带定位片段和项目级 FTS5/BM25 索引。
- `workspace`：新增 `VirtualWorkspace`、`PublicationJournal`、`WorkspaceBridge`、稳定资源
  引用、发布状态/错误；支持 Project/Task 布局、Task working/state、不可变 manifest 与
  `RUN.json`、staging、正式资源和只见 `AVAILABLE` 的查询。
- `providers/workspace`：新增 `LocalWorkspace` 正式 Adapter 和 `FakeWorkspace` 测试
  Adapter；实现真实路径校验、符号链接/特殊文件/可执行文件拒绝、Unicode 文件名规范化、
  大小与 SHA-256 校验、只读输入、同目录 fsync + 原子替换。
- `storage`：新增 migration `0002_workspace_publications.sql` 和
  `SqlitePublicationJournal`；Repository 新增项目文件、版本、当前版本和 Dataset 的有界
  列举能力。
- `tests`：新增 Workspace unit/contract tests、ProjectCorpus contract tests，以及真实
  多格式提取/索引和发布崩溃对账 integration tests；已有 migration 断言升级到 schema v2。

## 3. Interface and invariant changes

- `ProjectCorpus.import_file` 对同一规范化逻辑文件名始终追加新版本；READY、FAILED、
  UNSUPPORTED 均为定稿状态。解析器和 FTS 表不进入公共 Interface。
- 提取物记录 `source_hash + extractor_version`；索引元数据记录源哈希、媒体类型、提取器
  版本和 `fts5-v1`。删除派生物后可由 `rebuild_derived` 从不可变原件重建。
- `ProjectSnapshot` 固定创建时每个逻辑文件的最高版本及处理状态、索引版本和正式
  Dataset ID；后续导入不改变旧 Snapshot。
- RELEVANT 只查询 Snapshot 的 READY 条目，并返回 `file_id/file_version_id/hash` 以及页、
  段落、幻灯片、工作表、表或行范围定位。FULL_PROJECT Coverage 显式枚举 FAILED、
  UNSUPPORTED 和 SKIPPED。
- 发布幂等键为 `run_id + step_id + output_name`；状态流为 `STAGED -> AVAILABLE` 或
  `STAGED -> CORRUPT`。`AVAILABLE` 是成功终态，相同幂等键不能对应不同哈希或资源。
- Runtime SQLite 继续保存领域/发布元数据，Workspace 保存原件、提取物、索引和正式文件。

## 4. Storage and migration impact

- Runtime schema 从 v1 升级到 v2，新增 `workspace_publications` 表、恢复扫描索引和
  AVAILABLE 终态 trigger。表内仅保存稳定 ID、状态、大小、哈希和非敏感错误摘要，
  不保存文件正文。
- Workspace 新增约定布局：`sources/{file_id}/{version_id}`、`extracted`、`indexes`、
  `datasets`、`artifacts`、`manifests`、`tasks/{task_id}/{working,staging,state}`。
- migration 可从空库或 v1 顺序升级并可重放。正式发布采用 SQLite STAGED 记录、文件
  原子移动、SQLite AVAILABLE 三步协议；reconciler 可处理移动前、移动后和文件缺失状态。
- 回滚代码前需先确认没有 v2 数据依赖；migration 本身不做破坏性降级。

## 5. Security and privacy impact

- 拒绝 `..`、宿主绝对路径、路径分隔符、Windows 设备名、Workspace 根/路径内符号链接、
  导入源符号链接、目录、设备文件、可执行权限和可执行扩展名。
- 原件导入前后均核验 SHA-256；有界读取、派生物重建和正式发布再次核验内容哈希/大小。
- ProjectFileVersion、snapshot manifest、`RUN.json` 与正式资源写入后只读；公共接口不提供
  覆盖或删除 Project 输入的操作。Task working/staging 路径完全由 project/task/step ID 派生。
- 本阶段无网络或云模型调用，不接触 secret/PII 映射；Runtime DB 不保存正文或原始载荷。
- 负向测试覆盖路径逃逸、符号链接根/输入、可执行文件、跨状态覆盖、损坏文件、发布缺失
  与幂等请求冲突。

## 6. Dependency changes

- 新增并锁定：DuckDB 1.5.5（MIT）、openpyxl 3.1.5（MIT）、PyArrow 25.0.1
  （Apache-2.0）、pypdf 6.15.0（BSD-3-Clause）、python-docx 1.2.0（MIT）、
  python-pptx 1.0.2（MIT）。传递依赖同步写入 `uv.lock`。
- `uv run --with pip-audit pip-audit`：未发现已知漏洞；本地包 `dataharness 0.1.0` 因不在
  PyPI 被正常跳过。
- Sandbox 镜像未变化，无 digest、SBOM 或镜像扫描变化。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv lock --check` | PASS | 30 个包解析一致，锁文件无漂移 |
| `uv run ruff format --check .` | PASS | 114 files already formatted |
| `uv run ruff check .` | PASS | All checks passed |
| `uv run pyright` | PASS | 0 errors, 0 warnings, 0 informations |
| `uv run pytest --cov=dataharness --cov-report=term-missing -q` | PASS | 157 passed；总覆盖率 94%，`projects/corpus.py` 92% |
| `uv run --with pip-audit pip-audit` | PASS | No known vulnerabilities found；仅跳过本地项目包 |

## 8. Exit Gate evidence

1. **Agent/Sandbox 无法覆盖或删除 ProjectFileVersion；同名更新创建新版本。**
   `VirtualWorkspace` 不提供输入删除/覆盖操作，原件只读；Local/Fake contract tests 验证同一
   version 不能再次导入，ProjectCorpus contract tests 验证同名导入追加版本。
2. **路径逃逸、绝对路径、符号链接和跨 Task 引用被拒绝。**
   `test_workspace_paths.py` 覆盖 `..`、Windows/POSIX 绝对路径、根/输入符号链接、设备名和
   staging 逃逸；Task 路径只由受控 ID 构造，不接受外部 Task 路径。
3. **提取物与索引绑定源哈希及版本，删除后可重建。**
   integration test 检查每种格式提取 JSON 的 `source_hash/extractor_version`，删除 extracted
   和 index 后由 `rebuild_derived` 恢复原 Snapshot 的检索。
4. **不支持或损坏文件显式标记。**
   PNG fixture 为 UNSUPPORTED，损坏 JSON 为 FAILED；两者不进入 READY 检索，Coverage
   显式披露未覆盖项。
5. **Snapshot 创建后不可变，后续上传不改变旧 Run 数据视图。**
   contract test 在第一版 Snapshot 后导入第二版，验证旧 Snapshot 仅检索第一版，新
   Snapshot 仅检索第二版；SQLite 既有不可变 trigger 继续生效。
6. **发布断点注入后 reconciler 收敛。**
   integration tests 分别在 STAGED 后、文件已移动但数据库未 AVAILABLE、staging 文件
   缺失三个断点模拟崩溃，结果收敛到 AVAILABLE 或显式 CORRUPT。
7. **上层只看到 AVAILABLE，正式对象有稳定 ID/hash。**
   `WorkspaceBridge.available` 在发布前返回空，发布后只返回含 resource_id、SHA-256、大小
   的 AVAILABLE 记录；STAGED/CORRUPT 查询不会泄漏。

## 9. Architecture deviations and decisions

None。实现沿用 `ARCHITECTURE.md` 的 Runtime SQLite 元数据事实源、本地 Workspace 文件
事实源、FTS5/BM25、只读版本与三步发布协议。DuckDB/SQLite 快照按计划作为可导入格式，
不连接在线数据库。

## 10. Known issues and technical debt

- FTS5 是词法检索，不提供语义相似度；这是 V1 明确取舍，未来只能经独立
  `SemanticIndexProvider` 扩展。
- 图片 OCR、音视频和未知容器保持 UNSUPPORTED；Phase 03 不尝试静默降级。
- 本阶段 publication 记录稳定资源 ID/hash 和可见状态；Dataset/Artifact 领域注册及 lineage
  在 Phase 06 AnalysisRuntime 中完成。
- 解析器按 `max_file_bytes` 受限，但部分结构化格式仍会在 Host 解析时占用高于文件大小的
  内存；后续性能基线阶段应记录大文件峰值并决定是否分批提取。

## 11. Next-phase entry check

Phase 04 前置条件满足：Runtime schema v2 可重放，ProjectCorpus/VirtualWorkspace/
WorkspaceBridge 接口稳定，输入与发布边界有负向测试。下一阶段应保持 Runtime DB、
Workspace 与每 Task Privacy DB 的物理隔离，并确保任何模型请求、异常、日志或 trace 都不
携带 Workspace 原文或 privacy 映射。
