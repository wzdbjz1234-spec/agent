# Sandbox Capability

- 只暴露面向分析的 `execute_python` 与 `execute_sql`，不暴露生命周期、Host 路径或 Provider SDK。
- Run 复用可替换 lease，Step 使用独立进程；超时、取消和输出限制必须传播。
- 创建或重连后必须检查 image digest、网络、挂载、用户和资源限制；不符即 fail closed。
- 不提供 `run_shell`、`install_package`、网络开关或额外挂载工具。
- Sandbox 返回内容在进入模型、日志或 trace 前经过隐私出口。

