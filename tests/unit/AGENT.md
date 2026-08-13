# Unit Tests

- 纯函数和领域状态机使用快速、确定性测试，不启动网络、OpenSandbox 或真实模型。
- 覆盖 Task/Run/Step/Finding 的合法与非法迁移、wait_reason、phase、retry_of_step_id 和终态规则。
- 覆盖 Project/FileVersion/Snapshot/Coverage 不变量、文件更新建新版本和 Run 固定 Snapshot。
- 覆盖路径规范化、hash/幂等键、预算与熔断、错误分类、发布状态和证据引用校验。
- 覆盖 secret/PII 规则、占位稳定性、类型匹配恢复、响应再扫描和脱敏日志。
- 使用 fake clock、固定 seed 和小型合成数据；不要把实现细节或第三方 SDK 类型写入领域测试。
