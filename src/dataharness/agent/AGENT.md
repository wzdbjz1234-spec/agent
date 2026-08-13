# Agent Assembly

- 只负责装配 PydanticAI Agent、ModelGateway、UsageLimits、工具、Skills、Checkpoint 与 Compaction。
- 不实现第二套 agent loop、代码解释器或工作流状态机。
- 工具面限定为 `execute_python/execute_sql/list_workspace/read_text/inspect_output/submit_finding`。
- 工具参数、模型文本和 Skill 内容均不可信；执行请求必须下沉到 AnalysisRuntime/OpenSandbox。
- Compaction 前持久化目标、计划、进度、领域引用和未解决问题；大结果只传有界片段或引用。

