# Workspace Boundary

- 定义 VirtualWorkspace、WorkspaceBridge、资源引用、发布协议与路径策略；项目版本、索引和 Coverage 语义属于 projects module。
- Project 目录包含 versions/sources、extracted、indexes、datasets、artifacts、manifests 与 Task 子目录。
- ProjectFileVersion 和已发布资源只读；Agent 只能写当前 Task working 与当前 Step staging，正式发布由 Host 执行。
- 路径先规范化再解析真实路径，拒绝目录穿越、Host 绝对路径、符号链接和跨 Task 引用。
- Workspace 保存文件事实；领域元数据仍由 Runtime SQLite 保存，`RUN.json` 固定 ProjectSnapshot/FileVersion/hash 且仅是不可变复现清单。
