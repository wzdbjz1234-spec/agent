# Domain

- 纯领域层，不依赖 FastAPI、PydanticAI、OpenSandbox、SQLite 或遥测 SDK，不执行 I/O。
- 核心对象：Project、ProjectFile、ProjectFileVersion、ProjectSnapshot、ProjectCoverageReport、Session、Task、Run、AnalysisStep、Dataset、Artifact、Finding、Lineage。
- Task 必须绑定一个 Project；Run 固定不可变 ProjectSnapshot；文件更新创建新版本而不是覆盖。
- FileVersion 状态为 `IMPORTING/READY/FAILED/UNSUPPORTED`；Snapshot 记录全部当前版本/状态，只有 READY 可检索，Coverage 仍披露其他状态。
- Project 状态为 `ACTIVE/ARCHIVED`；CoverageItem 状态为 `PROCESSED/FAILED/UNSUPPORTED/SKIPPED`。归档不得破坏历史 Snapshot。
- Task：`QUEUED/ACTIVE/WAITING/COMPLETED/FAILED/CANCELLED`，等待细节用 `wait_reason`。
- Run：`QUEUED/RUNNING/WAITING/SUCCEEDED/FAILED/CANCELLED`；工作阶段用 `phase`。
- Step：`PENDING/RUNNING/SUCCEEDED/FAILED/TIMED_OUT/CANCELLED`；重试创建新 Step 并设置 `retry_of_step_id`。
- Finding：`DRAFT/VERIFIED/WARNING/REJECTED`；只有 Host Verification Gate 可改变正式状态。
