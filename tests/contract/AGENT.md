# Contract Tests

- 对每个 Protocol 运行同一组契约测试，正式 Provider 与 fake 实现必须语义一致。
- Sandbox 契约覆盖 create/connect/execute/terminate、attestation、超时、取消、输出上限和清理。
- ProjectCorpus 契约覆盖不可变版本、Snapshot、RELEVANT 来源定位和 FULL_PROJECT Coverage。
- Workspace 契约覆盖只读 Project 资源、Task 写入、路径逃逸、原子发布、hash、幂等与崩溃对账。
- ModelGateway 契约覆盖凭据 BLOCK、PII placeholder/restore、跨 Task 隔离及所有模型路径统一出口。
- Durable/Storage 契约覆盖 CAS 状态、lease epoch、终态不回退、重试新 Step 和事务事件。
