# Phase XX Completion Report: <name>

- Status: `COMPLETED | PARTIAL | BLOCKED`
- Date: `YYYY-MM-DD`
- Plan phase: `Phase XX`
- Commit/revision: `<git commit or working-tree description>`

## 1. Objective and scope

说明本阶段承诺、实际完成范围和明确未包含的内容。

## 2. Detailed changes

按模块及文件列出新增、修改、删除内容，并说明行为变化，不能只列文件名。

## 3. Interface and invariant changes

记录新增或改变的 Interface、输入输出、错误、顺序约束、性能特征、状态迁移和事实来源。

## 4. Storage and migration impact

记录 schema、migration、Workspace 布局、兼容性、回滚和数据对账影响；无变化时写 `None`。

## 5. Security and privacy impact

记录信任边界、Sandbox、凭据、PII、路径、日志/trace 和网络影响，以及相应负向测试。

## 6. Dependency changes

列出新增、升级、移除的直接依赖、锁文件变化、License 和漏洞检查；无变化时写 `None`。

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `<exact command>` | `PASS/FAIL` | `<summary or artifact>` |

## 8. Exit Gate evidence

逐项复制该阶段的退出 Gate，给出满足证据。不能用“已完成”代替证据。

## 9. Architecture deviations and decisions

列出与 `ARCHITECTURE.md` 或计划的偏差及批准情况；无偏差时写 `None`。

## 10. Known issues and technical debt

列出负责人、影响、后续阶段和跟踪方式。不得隐去失败或未测路径。

## 11. Next-phase entry check

说明下一阶段前置条件是否满足，以及需要携带的接口、fixtures、migrations 和风险。

