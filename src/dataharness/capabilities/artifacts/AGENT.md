# Artifact Capability

- Artifact/Dataset 是正式领域对象，不是裸路径。
- 只暴露经 Host 发布且状态为 AVAILABLE 的输出。
- 发布必须校验类型、大小、hash，并关联 Run、Step、输入和生成代码。
- Agent 不能直接把 staging 标记为正式产物，也不能覆盖原始输入。
- 读取返回元数据、受控片段或稳定引用；大文件不直接塞入模型上下文。

