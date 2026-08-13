# Domain

- 纯领域层，不依赖 FastAPI、PydanticAI、OpenSandbox、SQLite 或遥测 SDK，不执行 I/O。
- 核心对象：Session、Task、Run、AnalysisStep、Dataset、Artifact、Finding、Lineage。
- Task：`QUEUED/ACTIVE/WAITING/COMPLETED/FAILED/CANCELLED`，等待细节用 `wait_reason`。
- Run：`QUEUED/RUNNING/WAITING/SUCCEEDED/FAILED/CANCELLED`；工作阶段用 `phase`。
- Step：`PENDING/RUNNING/SUCCEEDED/FAILED/TIMED_OUT/CANCELLED`；重试创建新 Step 并设置 `retry_of_step_id`。
- Finding：`DRAFT/VERIFIED/WARNING/REJECTED`；只有 Host Verification Gate 可改变正式状态。

