# Skill Registry

- 只发现配置根目录中管理员预装的本地 Skill；不访问在线 registry。
- 未激活时仅暴露名称、描述和内容 hash；激活后加载完整 SKILL.md，其余资源按需读取。
- 路径必须保持在 Skill 根目录内并拒绝符号链接逃逸；运行期间目录只读。
- Skill 脚本通过 AnalysisRuntime 在 OpenSandbox 执行，绝不在 Host import/exec。
- Run manifest 固化 Skill 内容 hash；检测到运行中变更时拒绝继续或创建新 Run。

