# Tests

- 测试按 unit/contract/integration/e2e 分层，默认不访问真实云模型或公网。
- 安全断言必须是负向且可证：生成代码不在 Host 执行，Sandbox 看不到 Runtime/Privacy DB 和凭据，输入不可写。
- 隐私测试覆盖凭据阻断、Task 内稳定 PII 占位、跨 Task 不关联、受控恢复以及日志/trace 再扫描。
- 耐久测试覆盖 lease 过期、Host 崩溃、Sandbox 重建、取消、预算耗尽、幂等发布和 reconciler。
- Project 测试覆盖文件不可变版本、Snapshot 固定、跨文件来源定位、FULL_PROJECT Coverage 和同项目并行 Task 隔离。
- 固定时间、随机种子、镜像/Skill hash 和 fake provider；测试数据不得包含真实秘密。
