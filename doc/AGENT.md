# Development Documentation

本目录保存开发计划、阶段完成报告和重要决策证据。

- `DEVELOPMENT_PLAN.md` 是阶段顺序、依赖关系、进入条件和退出 Gate 的事实来源。
- 每个阶段完成时必须新建 `phase-XX-<slug>-YYYYMMDD.md`，不得覆盖、复用或把多个阶段合并进同一报告。
- 同日同阶段有多次正式验收时，在日期后追加 `-01`、`-02`。
- 阶段报告以 `PHASE_REPORT_TEMPLATE.md` 为最低内容要求；必须包含真实命令、结果和改动文件，不能只写概述。
- 未满足全部退出 Gate 时状态只能是 `PARTIAL` 或 `BLOCKED`，并明确缺口；不得提前把总计划标为 `COMPLETED`。
- 完成报告创建后，更新 `DEVELOPMENT_PLAN.md` 的状态与报告链接。报告正文一经作为验收证据使用即保持不可变；纠错另建 addendum。
- 架构改变先修改 `ARCHITECTURE.md` 和相关 `AGENT.md`，在阶段报告中记录原因、影响和迁移方案。

