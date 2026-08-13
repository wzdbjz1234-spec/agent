# Project Corpus

- 本模块是长期项目语料的 deep module；Interface 保持为项目创建/归档、文件导入、Snapshot、检索、资源打开和 Coverage。
- 核心对象为 Project、ProjectFile、ProjectFileVersion、ProjectSnapshot、ProjectCoverageReport；路径、提取器和索引表属于 Implementation。
- 文件更新创建新 ProjectFileVersion，禁止覆盖被 Snapshot 引用的版本。Snapshot 创建后不可变。
- 本地提取和 FTS5/BM25 索引绑定 source hash 与 extractor/index version；失败或不支持格式必须显式记录。
- 每种文件格式使用内部 DocumentExtractor Adapter；第三方解析器类型不得泄漏到 ProjectCorpus Interface。
- RELEVANT 返回有界片段及文件版本、页码/段落/幻灯片/工作表/行范围；FULL_PROJECT 枚举 Snapshot 并生成 CoverageReport。
- ProjectCorpus 不执行生成代码、不直接调用云模型，也不把索引等同于 Agent Memory。
