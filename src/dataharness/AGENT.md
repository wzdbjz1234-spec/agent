# DataHarness Package

依赖方向固定为：

`api -> orchestration -> agent/capabilities/analysis/projects -> domain + boundary protocols -> providers/storage`

- 云模型只经 ModelGateway；生成代码只经 SandboxProvider。
- Runtime SQLite、Privacy SQLite 和 Host 凭据不得进入 Workspace 或 Sandbox。
- Project 文件按版本不可变，Run 固定 ProjectSnapshot；正式文件经 staging 发布，领域关系引用稳定 ID 与 hash。
- V1 使用 PydanticAI 原生工具、OpenSandbox、LocalWorkspaceProvider 和 LocalDurableExecutor。
- 不引入 CodeMode/Monty、Prefect、AgentFS、向量记忆、在线数据库工具或运行时装包。
