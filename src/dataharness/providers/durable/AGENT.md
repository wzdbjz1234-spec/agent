# Durable Provider

- V1 正式实现是 LocalDurableExecutor：SQLite 队列、原子 claim、lease epoch、heartbeat 与过期回收。
- 不使用 Prefect；其只能作为未来分布式执行的设计参考。
- claim、状态提交、取消请求和重试记录必须事务化且幂等。
- Worker 丢失后从最近 checkpoint/Workspace 恢复，不重复已正式发布的 Step 输出。
- 终态不可回退；自动重试必须有次数上限、错误分类与退避。

