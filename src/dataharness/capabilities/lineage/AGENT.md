# Lineage Capability

- 记录 Dataset/Artifact/Finding 与 Run、Step、代码、输入、Skill、镜像 digest 的关系。
- 边使用稳定 ID 与内容 hash，不使用裸文件路径作为身份。
- 模型只能提出 lineage 声明；Host 在发布和验证时确认。
- 语义可参考 OpenLineage，但 V1 不要求部署 OpenLineage 服务。
- Lineage 为 EvidenceGate 提供可追溯证据，不负责判断业务结论真伪。

