# Memory Provider 约束

- 这里仅实现独立的对话历史 FTS5/BM25 存储，不读取或复制 `ProjectCorpus` 索引。
- 不允许引入向量数据库、Embedding 或在线记忆服务。
- 写入内容必须已经经过 `ModelGateway` 的边界脱敏；Provider 不接触原始凭据。
