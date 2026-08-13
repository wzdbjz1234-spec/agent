-- Phase 07：把恢复、取消和有限重试所需的控制面状态持久化到 Runtime SQLite。
-- 迁移只增加可空列/追加表，不改变已有 Project、Snapshot 和已发布资源事实。
ALTER TABLE runs ADD COLUMN next_attempt_at TEXT;
ALTER TABLE checkpoint_metadata ADD COLUMN project_snapshot_id TEXT;
ALTER TABLE checkpoint_metadata ADD COLUMN sandbox_id TEXT;
ALTER TABLE checkpoint_metadata ADD COLUMN sandbox_image_digest TEXT;
ALTER TABLE checkpoint_metadata ADD COLUMN run_lease_epoch INTEGER;
ALTER TABLE checkpoint_metadata ADD COLUMN phase TEXT;

CREATE TABLE run_retry_attempts (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    failure_kind TEXT NOT NULL,
    delay_seconds REAL NOT NULL CHECK (delay_seconds >= 0),
    next_attempt_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, attempt)
);

CREATE INDEX run_retry_ready_idx ON runs(status, next_attempt_at, created_at);
