# Project Capability

- 向 Agent 暴露窄工具：list_project_files、search_project、inspect_project_file、preview_project_table、query_project_tables、get_project_coverage。
- 所有请求隐式限定为当前 Run 的 project_snapshot_id；模型不能传入 Host 路径或切换 Project。
- 返回稳定 ProjectFileVersion ID、内容 hash 与页码/段落/幻灯片/工作表/行范围；正文片段必须有界。
- RELEVANT 记录实际消费的资源引用；FULL_PROJECT 委托 ProjectCorpus 枚举 Snapshot 并生成 CoverageReport。
- 工具不直接解析文件、读索引表、运行代码或调用模型；这些复杂性留在对应 deep module。

