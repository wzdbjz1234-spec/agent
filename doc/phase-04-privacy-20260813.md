# Phase 04 Completion Report: Privacy and ModelGateway

- Status: `COMPLETED`
- Date: `2026-08-13`
- Plan phase: `Phase 04`
- Commit/revision: `37d7442 feat(privacy): add protected model gateway`（实现与测试 checkpoint）

## 1. Objective and scope

本阶段实现所有云模型调用的唯一安全出口，以及针对明确凭据和常见 PII 的本地、确定性、
best-effort 防护。范围包含 secret 阻断、PII 类型化占位、每 Task 独立 Privacy SQLite、
扫描缓存、受控恢复，以及 request、response、tool result、异常、compaction、log 和 trace
的统一再扫描。

本阶段不接入真实云模型 SDK、NER、Presidio 或任何在线检测服务；不承诺 PII 零漏报，也不
阻止普通业务数据被发送给用户配置的云模型。

## 2. Detailed changes

- `src/dataharness/privacy/detector.py`：新增本地 `SecretDetector` 和 `PIIDetector`。secret
  规则覆盖密码、API token、私钥、Cookie 与连接串；PII 规则覆盖邮箱、手机号、银行卡
  （含 Luhn 校验）、身份证以及显式配置的正则规则。
- `src/dataharness/privacy/placeholders.py`：新增 `PlaceholderStore`。它为每 Task 初始化独立
  Privacy SQLite，保存类型化 PII 映射和按内容 hash 缓存的无明文检测结果；映射分配使用
  SQLite 写事务保证同一 Task 内稳定。
- `src/dataharness/privacy/policy.py`：新增 `PrivacyPolicy`、无明文 `PrivacyAudit` 和统一的
  request/boundary 处理。请求 secret fail closed；所有返回边界先将 secret 代换为安全标记，
  再进行 PII 占位。
- `src/dataharness/privacy/gateway.py`：新增 `CloudModelProvider` 最小协议和 `ModelGateway`。
  Provider 只能收到 gateway 准备好的云端视图；Provider 异常也会先脱敏再转换为稳定错误。
- `tests/unit/test_privacy.py`、`tests/contract/test_model_gateway_contract.py`、
  `tests/integration/test_privacy_sqlite.py`：新增规则、稳定性、受控恢复、全部再扫描边界、
  fake cloud、缓存与 Runtime/Privacy/Workspace 物理隔离测试。

## 3. Interface and invariant changes

- `CloudModelProvider.complete(request: str) -> str` 是云模型 Adapter 的最小边界；业务代码必须
  通过 `ModelGateway.complete(task_id, request)` 调用它。
- `PrivacyPolicy.prepare_request` 返回只可出站的 `PreparedRequest`；命中任意 secret 时抛出
  `SecretDetectedError`，Provider 没有调用机会。
- PII 占位格式为 `<PII:TYPE:NNNN>`。同一 Task 对相同规范化值稳定复用，不同 Task 使用独立
  数据库，不能由映射或序号关联。占位只改变云端视图，调用方传入字符串不被修改。
- `restore_tool_input` 仅能恢复当前 Task 已登记的占位符，且必须在工具声明的类型白名单内；
  伪造、跨 Task 或类型不匹配的占位符均抛出 `PlaceholderRestoreError`。
- Provider response、tool result、exception、compaction、log、trace 均通过同一
  `sanitize_boundary_text` 路径；日志/trace 可记录的审计对象只有内容 hash、种类和数量。

## 4. Storage and migration impact

None（Runtime schema 保持 v2）。

Privacy SQLite 由既有 `PrivacyConnectionFactory` 在
`runtime-data/privacy/{task_id}.db`（或配置的 privacy root）按 Task 创建。其 schema 是本地
私有实现：`pii_mappings` 只含占位、类型、规范化 hash 和原值；`scan_cache` 只含内容 hash、
种类和位置 JSON。两张表不进入 Runtime DB、Workspace、Sandbox、Artifact、日志或 trace，
因此无需 Runtime migration。

## 5. Security and privacy impact

- secret 规则在 Provider 调用之前执行且 fail closed；fake cloud 负向测试证明命中请求不会
  到达 Adapter。
- PII 只向云端发送类型化占位符；本地原始请求字符串、Project 输入与其 hash 不会被改写。
- Privacy DB 按 Task 物理分文件；恢复要求当前 Task 映射和显式类型授权，阻止模型伪造占位符
  或跨 Task 引用。
- 所有模型返回与辅助文本统一再扫描；异常、日志和 trace 不会携带命中的明文 PII 或 secret。
- 检测为规则型 best-effort：普通业务数据默认可出站；姓名、自然语言地址和对抗性编码规避
  不属于 V1 的默认保证。

## 6. Dependency changes

None。实现仅使用 Python 标准库 `re`、`sqlite3`、`json` 与既有 Pydantic/domain/storage 依赖，
`uv.lock` 无变化。`uv run --with pip-audit pip-audit` 未发现已知漏洞；本地未发布的
`dataharness 0.1.0` 被工具正常跳过。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `uv lock --check` | PASS | 30 packages resolved；锁文件无漂移 |
| `uv run ruff format --check .` | PASS | 123 files already formatted |
| `uv run ruff check .` | PASS | All checks passed |
| `uv run pyright` | PASS | 0 errors, 0 warnings, 0 informations |
| `uv run pytest -q` | PASS | 171 passed in 7.73s |
| `uv run pytest --cov=dataharness --cov-report=term -q` | PASS | 171 passed；总覆盖率 94% |
| `uv run --with pip-audit pip-audit` | PASS | No known vulnerabilities found；本地项目包正常跳过 |

## 8. Exit Gate evidence

1. **任何 Adapter 不可绕过 ModelGateway。** `CloudModelProvider` 是唯一 Adapter 协议，
   `test_model_gateway_contract.py` 使用 fake cloud 验证其仅由 `ModelGateway` 调用且只接收
   已占位请求；静态依赖检查与全量测试通过。
2. **secret 不会到达 fake cloud。** `test_gateway_blocks_secret_before_fake_cloud_provider`
   覆盖五类 V1 凭据中的 gateway 路径并断言 `provider.requests == []`；契约测试再次断言
   secret 调用不会增加 fake cloud 调用数。
3. **PII 到达 fake cloud 时只有占位符，原件和 hash 不变。** unit/contract tests 断言
   email/phone 明文不在 Adapter 输入中，输入为 `<PII:...>`；调用方原始字符串未变。阶段
   04 不读取或写入 Project Workspace，因此 Project 原件/hash 没有变更路径。
4. **Privacy DB 不进入 Runtime DB、Workspace、Sandbox、Artifact、log 或 trace。**
   `test_pii_mapping_and_scan_cache_never_enter_runtime_or_workspace` 用真实 SQLite 文件验证
   Runtime DB 不存在 privacy 表、映射仅在 `privacy/task-1.db`；公开 `PrivacyAudit` 不含原文，
   `sanitize_log/trace` 同路径再扫描。Sandbox/Artifact 尚未在本阶段接入，且 privacy 模块不
   导入其类型或路径。
5. **检测明确为 best-effort。** `PrivacyPolicy`、模块文档和本报告均限制为本地规则检测；
   未将普通业务数据默认阻断，也未声称零漏报。

## 9. Architecture deviations and decisions

None。实现遵循 `ARCHITECTURE.md` 第 10 节：所有云调用经 ModelGateway、secret 阻断、Task
内稳定 PII 占位、独立 Privacy SQLite 和所有模型边界再扫描。V1 不添加 Presidio/NER，符合
其“可选增强而非默认路径”的取舍。

## 10. Known issues and technical debt

- 规则型 detector 无法覆盖所有格式、语言和刻意编码的敏感信息；后续如引入 NER/Presidio，
  必须保持本地执行、可配置、同样的 fail-closed secret 语义，并增加独立依赖审查。
- 当前 `CloudModelProvider` 是同步最小协议；Phase 08 接入 PydanticAI 时需要提供适配层，
  但不得直接使用模型 SDK 或绕开 `ModelGateway`。
- Sandbox 工具尚未在本阶段实现。Phase 05/06 接入工具调用时，必须在进入 Sandbox 前使用
  `restore_tool_input` 和每个工具的 PII 类型白名单，不能把 Privacy DB 挂载入 Sandbox。

## 11. Next-phase entry check

Phase 05 的前置条件满足：模型出口已有可测试的单一边界，Privacy SQLite 的物理隔离已被
集成测试证明。下一阶段应确保 OpenSandbox 从不挂载 Runtime DB、Privacy DB 或 Host 凭据；
并将 `restore_tool_input` 仅接在 Sandbox 的受控工具入口，避免建立任何 Host 执行回退路径。
