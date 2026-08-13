# Blob Provider

- V1 不要求独立 Blob 服务；ProjectFileVersion、Dataset/Artifact 文件由 LocalWorkspaceProvider 保存。
- 本目录仅保留未来大对象后端的窄接口或兼容适配，不得成为当前事实源。
- 若实现，键必须由稳定 ID 生成，支持流式读写、hash 校验、幂等提交和 Task 范围隔离。
- 禁止把凭据、Privacy DB、Runtime DB 或未发布 staging 上传到外部存储。
- 启用远程 Blob 属于未来部署决策，必须重新评估数据出境与凭据边界。
