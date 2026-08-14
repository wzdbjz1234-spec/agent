# DataHarness V1 运维手册

本文档是 Phase 10 发布验收的一部分。V1 是本机单用户控制面：API 只绑定回环地址，
Runtime SQLite、每 Task Privacy SQLite、LocalWorkspace 和 OpenSandbox 服务必须位于同一
受控开发环境。普通业务数据可能发送到用户配置的云模型；凭据必须通过环境变量注入，
不写入配置文件、Runtime、Workspace 或日志。

## 发布前检查

在仓库根目录执行：

```powershell
uv lock --check
uv run python scripts/release_check.py --require-image
uv run python scripts/verify.py
```

镜像证据由 `sandbox-images/secure-analysis/build.ps1` 生成。构建参数必须是带
`@sha256:<64 位 digest>` 的基础镜像引用；运行服务必须使用
`build-evidence/image-digest.txt` 中的 digest。每次镜像、锁文件或 Skill 正文变化都要
重新生成 SBOM 和漏洞扫描证据。

## 启动与健康检查

1. 启动 Docker Engine，并确认 OpenSandbox 服务监听 `127.0.0.1:8080`。
2. 确认服务端 egress 为 `dns+nft`，网络关闭、非特权用户和三项白名单挂载保持开启。
3. 使用 `dataharness check --config dataharness.example.toml` 校验配置。
4. 使用 `dataharness serve --config dataharness.example.toml` 启动本地 API。
5. 只从本机访问 `/healthz` 和 `/readyz`；V1 没有公网认证、TLS、RBAC、多租户或 Webhook。

真实 OpenSandbox 验收：

```powershell
$env:DATAHARNESS_LIVE_SANDBOX = "1"
uv run pytest -q tests/integration/test_opensandbox_live.py
```

没有真实服务时，普通 `uv run pytest -q` 会明确跳过 live 测试，不把 fake 测试冒充真实
服务验收。

## 故障恢复

- API 进程重启：保留 Runtime DB、Privacy DB 和 Workspace 根目录，再重新启动 API/Worker。
  过期 Run lease 由 `LocalDurableExecutor` 回收；恢复仍使用 Run 创建时的 Snapshot。
- OpenSandbox 丢失：旧 lease 不能重连时，按 checkpoint 中已核对的 Snapshot、任务和镜像
  digest 重建 Sandbox；已提交的 AnalysisStep 依靠 Runtime 幂等记录和 Workspace 摘要避免重复发布。
- 取消：先记录取消意图，再终止当前 Run 的 Sandbox，最后清理未发布 staging；已发布的
  Dataset/Artifact 不删除。
- 失败或预算耗尽：自动重试受上限约束；预算耗尽进入 `WAITING/BUDGET_EXHAUSTED`，不会
  生成半成品正式资源。
- Privacy DB 读写失败：隐私出口必须 fail closed。不要把 Privacy DB 合并回 Runtime DB，
  也不要手工编辑占位符映射；应从备份恢复后重试当前 Task。

## 诊断清单

记录以下脱敏元数据即可：Task/Run/Step/Finding ID、Snapshot ID、镜像 digest、Skill hash、
状态、耗时、失败分类和发布对象 hash。禁止收集 prompt、模型原始回复、stdout/stderr、
凭据、PII 原文或宿主路径。

确认：

- Runtime DB 与每 Task Privacy DB 位于不同根目录；Sandbox 只有 `/project`、
  `/task/working`、`/task/staging` 三项挂载。
- `build-evidence/image-digest.txt`、SBOM、漏洞扫描结果与实际运行镜像一致。
- Finding 的 `Execution/Integrity/Evidence Gate` 结果和 Coverage 披露已进入最终回答。
- 取消、超时、Sandbox 丢失后没有残留进程，也没有 `STAGED` 但未完成发布的资源。

## V1 非目标与已知限制

V1 不提供 Prefect、AgentFS、Webhook、向量数据库、公网安全边界、通用 shell、任意
Host 执行、生产级 PII 完整识别或真实云账号集成测试。隐私检测是规则型 best-effort；
业务数据是否允许发送到用户配置的云模型由用户配置和部署策略决定。镜像依赖的漏洞若
暂无上游修复，必须在扫描证据中保留风险记录并持续监测，不能删除或伪造扫描结果。
