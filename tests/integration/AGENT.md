# Integration Tests

- 组合测试 SQLite repository + LocalDurableExecutor、Workspace + publication reconciler、PydanticAI + ModelGateway、AnalysisRuntime + OpenSandbox。
- OpenSandbox 测试确认断网、固定 digest、非特权用户、Task-scoped 挂载、资源限制和 Step 进程隔离。
- Publication 测试在 STAGED、文件移动和 AVAILABLE 各断点注入故障并验证可恢复。
- Privacy SQLite 与 Runtime SQLite 必须物理分离，且两者均不得被挂载进 Sandbox。
- 不依赖 Prefect、AgentFS、MLflow、在线数据库、在线 Skill registry 或真实云凭据。

