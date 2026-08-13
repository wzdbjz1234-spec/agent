# Analysis Capability

- 暴露结构化的 Python/SQL 执行请求，而非通用 shell 或 CodeMode。
- 强制声明输入引用、预期输出、超时和预算；执行由 AnalysisRuntime 完成。
- 返回有界摘要、schema、统计或 Artifact 引用，完整输出写入 Workspace。
- 相同调用按规范化参数、输入 hash、镜像 digest 生成幂等/熔断键。
- 不允许安装包、联网或访问 Runtime/Privacy DB。

