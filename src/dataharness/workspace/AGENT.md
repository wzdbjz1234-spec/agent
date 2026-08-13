# Workspace Boundary

- 定义 VirtualWorkspace、WorkspaceBridge、资源引用、发布协议与路径策略。
- Task 目录固定为 `inputs/working/staging/datasets/artifacts/state`；`inputs` 不可变。
- Agent 可浏览和受控读取，但只能写 working 与当前 Step staging；正式发布由 Host 执行。
- 路径先规范化再解析真实路径，拒绝目录穿越、Host 绝对路径、符号链接和跨 Task 引用。
- Workspace 保存文件事实；领域元数据仍由 Runtime SQLite 保存，`RUN.json` 仅是不可变复现清单。

