# secure-analysis image

镜像仅用于经 `OpenSandboxProvider` 认证的、不可信 Python/SQL/Skill Step。运行期必须以
锁定 digest、断网（服务端 egress 必须为 `dns+nft` 模式，仅 `dns` 模式会放过直连 IP 的
非 53 端口流量）、非特权 `sandbox` 用户启动，并且只允许 `/project`（只读）及当前 Task
的 `/task/working`、`/task/staging`（可写）三处挂载。根文件系统对执行用户不可写由
非 root 用户 + `no_new_privileges` + drop capabilities（CapEff=0）保证（OpenSandbox
docker 后端不提供只读根挂载，语义见 `ARCHITECTURE.md` 8.4）。

镜像内容：

- 基础镜像：`python:3.12-slim`，构建时必须传不可变 digest 引用。
- 已锁定分析依赖（`requirements.lock`）：duckdb、pyarrow、pandas、pandera。
- 内置 SQL runner：`/usr/local/bin/dataharness-sql-runner.py`（/project 上 DuckDB 查询）。
- 构建完成后移除 pip 本体与缓存，运行镜像内没有任何包管理器入口。

构建与证据：

```powershell
./build.ps1 -BaseImage '<mirror>/python:3.12-slim@sha256:<digest>' -Tag 'secure-analysis:1.0.0'
python scan_vulns.py build-evidence/sbom.spdx.json build-evidence/vuln-scan.json
```

- `build-evidence/image-digest.txt`：本次构建的锁定 digest（`sha256:<64 hex>`）。
- `build-evidence/sbom.spdx.json`：`docker scout sbom` 生成的 147 包 SBOM。
- `build-evidence/vuln-scan.json`：`scan_vulns.py` 经 OSV API 的漏洞扫描结果（可复现，
  与 grype/trivy 同源数据）。当前剩余漏洞均为已发布最新版本且无上游修复的分析依赖
  （numpy/pyarrow/duckdb/pandas/pydantic），记录为接受风险并持续监测。

`build-evidence/` 被 `.gitignore` 忽略，因为证据需要绑定每一次实际构建；源码仓库绝不
填写未验证的 digest 或扫描结论。
