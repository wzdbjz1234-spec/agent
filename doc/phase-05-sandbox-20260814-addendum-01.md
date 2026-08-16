# Phase 05 Completion Report Addendum: OpenSandbox execution seam（真实环境证据）

- Status: `COMPLETED`（本 addendum 解除原 `PARTIAL` 报告的阻塞项）
- Date: `2026-08-14`
- Plan phase: `Phase 05`
- Commit/revision: `9f511c3 feat(sandbox): unblock phase 05/06 with real OpenSandbox execution`
- 关联报告: [phase-05-sandbox-20260813.md](phase-05-sandbox-20260813.md)（PARTIAL 基线，本文档只补充其阻塞项证据，不覆盖原报告）

## 1. Objective and scope

原报告的全部代码交付保持有效。本 addendum 完成原报告第 11 节列出的四条恢复条件：

1. 在可访问 PyPI 的环境锁定官方 OpenSandbox SDK（`opensandbox==0.1.15`），并实现
   `OpenSandboxClient` 的 SDK 包装（`SdkOpenSandboxClient`）；
2. 使用 Docker（Docker Desktop 4.86.0，引擎 29.7.2）构建 `secure-analysis` 并记录实际 digest；
3. 生成 SBOM、完成漏洞扫描并把证据写入 `build-evidence/`；
4. 使用真实 OpenSandbox 服务（自托管 `opensandbox-server 0.2.2`，Docker runtime）跑通
   create/connect/execute/cancel/terminate 集成测试，验证网络、用户、三项挂载、资源限制
   与 fail-closed。

未包含：Provider lease 落盘（Phase 07 职责）、镜像漏洞的修复（见第 10 节接受风险）。

## 2. Detailed changes

- `src/dataharness/providers/sandbox/opensandbox_sdk.py`（新增）：`SdkOpenSandboxClient`
  实现 `OpenSandboxClient` 协议，是唯一允许 import 官方 SDK 的模块。
  - `create_sandbox`：镜像引用固定为 `<runtime>@sha256:<digest>`（docker daemon 只能解析
    本地已锁定镜像，无 tag 回退）；三项 host volume 挂载（`/project` 只读、`/task/working`
    `/task/staging` 可写）；deny-all egress 策略（`defaultAction=deny`、无规则）；
    资源限制（memory/cpu）；digest 与 snapshot 以 metadata 回写（digest 因服务端 label
    63 字符限制拆为 head+tail 两段）。
  - `inspect_sandbox`：运行时 attestation——比对 metadata digest/snapshot、镜像 URI 运行时名，
    并执行有界 Python 探测脚本（15s 预算）：user/uid（`pwd`）、`NoNewPrivs`、`CapEff`、
    根目录可写性、出站网络（非 53 端口 TCP + DNS 解析，二者都必须被拒绝）、三项挂载的
    存在性与读写性、cgroup `memory.max`。任何一项与 `SandboxSpec` 不符 → `SandboxPolicyError`
    （fail closed）。
  - `execute_step`：code 写入 `/task/working/<step_id>.{py,sql}`；PYTHON 直接 `python <file>`，
    SQL 走镜像内置 runner；`RunCommandOpts(timeout=...)` 由服务端强制超时，Host 侧
    `asyncio.wait` 兜底；取消通过 `asyncio.Event` 与 `commands.interrupt` 双通道，确定性返回
    `SandboxCancelledError`；结果映射为稳定 `ExecutionResult`（退出码/状态分类/stdout/stderr/
    schema/statistics/process_id）。
  - `cancel_step/cleanup_step/terminate_sandbox`：interrupt + 文件清理（`delete_files`），
    所有清理失败仅记录不掩盖主结果；销毁后清空本地缓存。
- `sandbox-images/secure-analysis/sql_runner.py`（新增）：镜像内置 SQL runner——扫描
  `/project` 下 parquet/csv/json 按文件主干注册 DuckDB 表（非标识符文件名跳过并写 stderr），
  执行查询、CSV 输出到 stdout、schema/statistics 写 `<query>.schema.json` sidecar。
- `sandbox-images/secure-analysis/Dockerfile`：`COPY sql_runner.py`；新增 `PIP_INDEX_URL`
  构建参数（受限网络可用镜像源）；构建后移除 pip 本体与缓存（运行时无任何包管理器入口，
  消除 24 个 pip 漏洞）。
- `sandbox-images/secure-analysis/requirements.lock`：锁定 duckdb==1.5.5、pyarrow==25.0.1、
  pandas==2.2.3、pandera==0.21.1（Phase 06 runner 依赖）。
- `sandbox-images/secure-analysis/build.ps1`：修复 PowerShell 5.1 下 docker stderr 被误判为
  终止错误的问题（经 cmd /c 重定向，按 `$LASTEXITCODE` 判断）；证据文件只记录裸 digest
  （`sha256:<64hex>`）；脚本保持 ASCII-only（避免无 BOM UTF-8 被按 ANSI 误读）。
- `sandbox-images/secure-analysis/scan_vulns.py`（新增）：从 SBOM（scout JSON）提取 purl →
  映射 OSV ecosystem → 批量查询 `api.osv.dev/v1/querybatch` → 生成可复现的漏洞证据报告。
- `src/dataharness/config.py` + `dataharness.example.toml`：`SandboxConfig.api_key`。
- `ARCHITECTURE.md` 8.4、`src/dataharness/sandbox/AGENT.md`、
  `src/dataharness/providers/sandbox/AGENT.md`：记录 `root_read_only` 的 V1 语义
  （根文件系统对执行用户不可写，由非 root + no_new_privileges + CapEff=0 提供）与
  真实 SDK 接入点（见第 9 节）。
- 测试：
  - `tests/unit/test_opensandbox_sdk_client.py`（新增，14 个）：create 参数映射
    （digest 引用/deny-all/三项 volume/metadata 拆分）、attestation 探测解析与 fail-closed
    （digest 漂移、网络可通）、execute 映射（Python/SQL/超时/非零退出）、cancel/cleanup/
    terminate。
  - `tests/integration/test_opensandbox_live.py`（新增，6 个，`DATAHARNESS_LIVE_SANDBOX=1`
    启用，否则显式跳过）：真实 create→attest→execute→terminate、SQL runner 读 /project 表、
    取消运行中 Step、并行 Run 隔离、伪造 digest fail closed、AnalysisRuntime 端到端
    （Python + SQL 都产生正式可发布输出）。

## 3. Interface and invariant changes

- `OpenSandboxClient` 协议不变；新增唯一实现 `SdkOpenSandboxClient`。
- `SandboxSpec.image_digest` 不变（`sha256:<64hex>`）；`SdkOpenSandboxClient` 将其拼为
  `<runtime>@<digest>` 传给服务端，docker daemon 本地解析失败即创建失败。
- 新不变量：attestation 的 `root_read_only`/`network_enabled` 由真实运行时探测事实决定；
  服务端 egress 必须是 `dns+nft` 模式（仅 `dns` 模式会放过直连 IP 的非 53 端口流量）。
- 探测与执行都受预算约束（探测 15s、请求超时 +30s 兜底）；输出上限仍由
  `OpenSandboxProvider` 在 Host 侧强制。

## 4. Storage and migration impact

None。与原报告一致，不新增 Runtime migration；真实环境证据全部位于
`sandbox-images/secure-analysis/build-evidence/`（gitignored）与本文档。

## 5. Security and privacy impact

- 生成代码仍然只作为 `ExecutionRequest.code` 交给 SDK；`opensandbox_sdk.py` 不包含
  exec/eval/subprocess/shell（AST 静态测试覆盖该文件）。
- 真实 Sandbox 验证：非 root `sandbox` 用户（uid 10001）、`NoNewPrivs=1`、`CapEff=0`、
  根文件系统对执行用户不可写、出站网络（非 53 TCP + DNS）全部被拒绝、三项挂载存在且
  读写性与 Spec 一致、cgroup 内存上限与 Spec 一致。
- Host 路径只存在于部署装配层的 `mount_resolver`；服务端 `allowed_host_paths` 限定为
  `C:/projects/research/runtime-data`（部署证据，非仓库内容）。
- 镜像内移除 pip；SBOM 146 包、OSV 扫描 34 个漏洞（详见第 10 节风险评估）。
- 服务端本地无认证模式（`OPENSANDBOX_INSECURE_SERVER=YES`）仅用于本机验收环境；
  生产部署必须配置 `api_key`（`SandboxConfig.api_key` 已支持）。

## 6. Dependency changes

- 新增直接依赖：`opensandbox==0.1.15`（Apache-2.0；`uv add` 后 `uv.lock` 解析 129 个包，
  SDK 传递依赖：httpx、pydantic、attrs、python-dateutil、six 等，均已在锁文件内）。
- 镜像依赖（requirements.lock）：duckdb/pyarrow/pandas/pandera 均为当前 PyPI 最新版。
- License：opensandbox 为 Apache-2.0；其余为既有依赖无变化。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv add opensandbox` | PASS | 锁定 opensandbox==0.1.15；129 packages resolved |
| `uv lock --check` | PASS | 锁文件一致 |
| `uv run --offline ruff format --check .` | PASS | 179 files already formatted |
| `uv run --offline ruff check .` | PASS | All checks passed |
| `uv run --offline pyright` | PASS | 0 errors, 0 warnings |
| `uv run --offline pytest -q` | PASS | 216 passed, 6 skipped（live 测试显式跳过） |
| `DATAHARNESS_LIVE_SANDBOX=1 uv run pytest -q` | PASS | 222 passed；总覆盖率 89% |
| `DATAHARNESS_LIVE_SANDBOX=1 uv run pytest tests/integration/test_opensandbox_live.py -v` | PASS | 6/6：create/attest/execute/terminate、SQL runner、cancel、parallel、digest fail-closed、runtime e2e |
| `uv run --offline python -m dataharness.tooling.dependency_check` | PASS | 依赖方向无违规 |
| `./build.ps1 -BaseImage 'dockerproxy.net/library/python:3.12-slim@sha256:dd2937...' -Tag 'secure-analysis:1.0.0'` | PASS | 产出 digest `sha256:11929d8dbf14021a638c51c0db8771d5f687e14431d82f97d3e75ac977868188` |
| `docker scout sbom --output build-evidence/sbom.spdx.json secure-analysis:1.0.0` | PASS | 146 packages 索引 |
| `python scan_vulns.py build-evidence/sbom.spdx.json build-evidence/vuln-scan.json` | PASS | 142 包查询，5 个受影响，34 个漏洞（详见证据文件） |

## 8. Exit Gate evidence

1. **生成代码没有 Host 执行路径。** `test_sandbox_host_execution.py` 的 AST 断言覆盖
   `providers/sandbox/*.py`（含新增 `opensandbox_sdk.py`）：无 subprocess/shlex 导入、
   无 exec/eval/system/popen/run/Popen 调用；code 只经 SDK `commands.run` 进入 Sandbox。
2. **Sandbox 看不到 Runtime/Privacy/credential/Docker/其他 Task。** `SandboxSpec` 只接受三项
   精确 mount（模型层负向测试）；服务端 `allowed_host_paths` 白名单 + `Volume` 只含
   resolver 解析的三目录；真实 attestation 探测确认 `/project` 只读、working/staging 可写、
   无其他挂载可见；镜像内无 docker socket、无凭据、无 Runtime/Privacy DB 路径。
3. **取消/销毁不影响并行 Run。** `test_live_parallel_runs_are_isolated`：两个真实 Sandbox
   独立 lease，销毁一个后另一个仍可执行。
4. **attestation/配置不符 fail closed。** `test_live_attestation_fails_closed_on_wrong_digest`
   （伪造 digest 创建被 docker daemon 拒绝）；单元测试覆盖 metadata digest 漂移与
   探测网络可通时的 `SandboxPolicyError`。
5. **超时、取消、丢失无残留进程且同 digest 可重建。**
   `test_live_cancel_interrupts_running_step`：取消运行中 Step 返回 CANCELLED，随后同一
   Sandbox 继续执行成功；`test_live_create_attest_execute_terminate` 验证 terminate 后
   以同一 digest 重建并再次执行。
6. **镜像 digest、依赖锁、SBOM、漏洞扫描证据齐全。** `build-evidence/` 含
   `image-digest.txt`（sha256:11929d...）、`sbom.spdx.json`（146 包）、`vuln-scan.json`
   （OSV，34 漏洞，详见第 10 节）；requirements.lock 锁定镜像依赖；uv.lock 锁定 SDK 依赖。

## 9. Architecture deviations and decisions

- `root_read_only` 语义细化：官方 OpenSandbox docker 后端不提供只读根挂载，V1 语义定为
  「根文件系统对执行用户不可写」，由非 root sandbox 用户 + `no_new_privileges` +
  `CapEff=0` 保证，attestation 以真实探测为准。已更新 `ARCHITECTURE.md` 8.4 与相关
  `AGENT.md`（按计划规则 7 先改文档，不静默改变安全承诺）。
- 服务端 egress 必须为 `dns+nft` 模式：验收中发现仅 `dns` 模式只拦截 DNS（直连 IP 非 53
  端口可绕过），已记录到 `ARCHITECTURE.md` 与镜像 README，部署配置为部署证据。
- `opensandbox-server` 为本地自托管部署（uvx 安装 0.2.2），配置
  `~/.sandbox.toml`：drop_capabilities、no_new_privileges、pids_limit=4096、
  egress=dns+nft、allowed_host_paths 限定。该配置是部署证据，不入仓库。
- 镜像 digest 用 docker daemon 对 digest 引用的本地解析强制（创建时），重连时以 metadata
  回写比对复核；不依赖服务端报告 digest。

## 10. Known issues and technical debt

- **剩余漏洞（接受风险，持续监测）**：OSV 扫描 34 个漏洞分布在 numpy(16)/pyarrow(9)/
  duckdb(4)/pydantic(4)/pandas(1)，这些包均为当前 PyPI 最新版且无上游修复版本；
  移除 pip 后已消除 24 个（58→34）。跟踪：Phase 10 加固时升级并重新扫描。证据：
  `build-evidence/vuln-scan.json`。
- **服务端 egress 模式依赖**：若部署未按 `dns+nft` 配置，`network_enabled=False` 的
  attestation 探测会失败（fail closed），不会静默降级；但部署文档必须强调该模式。
- **SDK 类型边界**：`opensandbox` SDK 的 pydantic 模型使用别名参数（mountPath/readOnly/
  defaultAction），包装层按别名构造；SDK 升级可能破坏这些签名（供应商边界，非我们可控）。
- **每次构建证据绑定**：digest/SBOM/漏洞结果与具体构建绑定，`build-evidence/` gitignored；
  重新构建后必须重跑 SBOM 与扫描（build.ps1 + scan_vulns.py 流程可复现）。
- Provider lease 尚未落盘；这是 Phase 07 的耐久 executor 与恢复职责，不引入第二事实源。

## 11. Next-phase entry check

Phase 06 的前置条件已满足：真实 Sandbox 服务可用（127.0.0.1:18080）、secure-analysis 镜像
已锁定 digest、SDK 包装与 attestation 已验收、create/connect/execute/cancel/terminate
集成测试 6/6 通过。Phase 06 报告见
[phase-06-analysis-20260814.md](phase-06-analysis-20260814.md)。
