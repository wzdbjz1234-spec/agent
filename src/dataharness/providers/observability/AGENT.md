# Observability Provider

- V1 默认 OpenTelemetry adapter；统一关联 trace/task/run/step/tool_call/sandbox ID。
- 默认记录元数据、hash、大小、状态、耗时和错误分类，不记录原始输入或隐私映射。
- Prompt、响应、Tool Result、stdout/stderr 和异常在记录前必须经过隐私处理。
- 观测后端失败不得破坏业务状态，但必须产生本地告警；隐私处理失败则 fail closed。
- MLflow 是未来可选分析评估适配器，不是 V1 依赖或事实源。

