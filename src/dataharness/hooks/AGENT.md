# Hooks

- Hook 用于观察或收紧生命周期，不是事实源，也不得偷偷扩权。
- ModelGateway 前后 Hook 执行凭据阻断、PII 占位、响应再扫描和脱敏审计。
- Tool/Step Hook 可记录预算、超时、取消、hash 与 trace 关联，但不能绕过 AnalysisRuntime。
- Hook 失败默认保守处理：安全/隐私 Hook fail closed；纯观测 Hook 可降级并记录告警。
- 禁止通过 Hook 直接调用模型 SDK、运行生成代码或改写原始 Workspace 数据。

