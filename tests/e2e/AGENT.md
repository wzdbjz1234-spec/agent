# End-to-End Tests

- 使用真实本地 API、Runtime SQLite、LocalWorkspaceProvider 与 OpenSandbox；云模型使用确定性 fake gateway。
- 验收链覆盖 Project 多文件导入/版本/Snapshot、RELEVANT/FULL_PROJECT、Task/Run、隐私出口、Python/SQL Step、发布、lineage、Coverage、Verification 和最终 Finding。
- 注入 Host 崩溃与 Sandbox 丢失，验证恢复同一 Run 且不重复已提交 Step。
- 验证取消和预算耗尽不会遗留进程或发布半成品；输入文件内容与 hash 始终不变。
- 验证业务数据允许进入 fake 云请求，而凭据不进入、PII 仅以占位形式进入；V1 不测试 Webhook、Prefect 或 AgentFS。
- 验证文件更新不改变旧 Run、同 Project 并行 Task 使用独立 Sandbox/写入/取消域，覆盖缺口不会被隐瞒。
