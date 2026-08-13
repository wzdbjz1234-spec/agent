# Local Workspace Provider

- V1 唯一正式 Workspace 实现是受控本地 Task 目录，不依赖 AgentFS。
- 原子创建 `inputs/working/staging/datasets/artifacts/state`，并执行路径、权限与配额检查。
- 输入导入时识别真实格式、计算 hash，拒绝符号链接、设备文件、可执行文件和目录逃逸。
- 正式发布使用幂等键与原子重命名/等价提交；支持崩溃后 reconciler 对账。
- AgentFS 仅供未来审计、快照或可移植 Workspace Provider 参考。

