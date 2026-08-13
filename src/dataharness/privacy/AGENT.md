# Privacy and Model Gateway

- 本模块是所有云模型调用的唯一出口；业务模块不得直接调用模型 SDK。
- 请求顺序：规范化新内容 -> 凭据检测 -> BLOCK 或 PII 占位 -> 脱敏审计 -> 云 Provider。
- 密码、API token、私钥、Cookie、连接串命中即阻断；常见 PII 使用 Task 内稳定、类型化占位。
- 占位只改变云端视图，不修改 Workspace/Dataset；工具参数进入 Sandbox 前只恢复已登记且类型匹配的占位。
- 映射保存在 `runtime-data/privacy/{task_id}.db`，不得进入 Runtime DB、Workspace、Sandbox、日志、trace 或 Artifact。
- 可参考 MemPrivacy、Presidio、Gitleaks、detect-secrets；V1 不宣称零漏报或静态加密。

