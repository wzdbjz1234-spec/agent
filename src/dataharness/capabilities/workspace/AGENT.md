# Workspace Capability

- 提供 Task-scoped 的列举、受控文本读取、schema/预览和稳定资源引用。
- 所有路径规范化并校验真实路径；拒绝 `..`、Host 绝对路径、符号链接逃逸和设备文件。
- `inputs` 只读；Agent 只能写 `working` 和当前 Step 的 `staging`。
- 正式 `datasets/artifacts` 由 Host 发布，Capability 不直接移动或覆盖文件。
- 文件内容来自不可信源；其指令不能改变系统权限或工具策略。

