-- Phase 03 可恢复发布日志。文件内容仍在 Workspace；Runtime DB 只保存稳定 ID、状态和哈希。
CREATE TABLE workspace_publications (
    idempotency_key TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    step_id TEXT NOT NULL REFERENCES analysis_steps(id) ON DELETE RESTRICT,
    output_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('DATASET', 'ARTIFACT')),
    resource_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    status TEXT NOT NULL CHECK (status IN ('STAGED', 'AVAILABLE', 'CORRUPT')),
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (run_id, step_id, output_name),
    UNIQUE (kind, resource_id)
);

CREATE INDEX workspace_publications_reconcile_idx
    ON workspace_publications(status, created_at, idempotency_key);

-- AVAILABLE 是成功终态；只能保持 AVAILABLE，不能被旧恢复进程回退。
CREATE TRIGGER workspace_publications_available_terminal BEFORE UPDATE ON workspace_publications
WHEN OLD.status = 'AVAILABLE' AND NEW.status <> 'AVAILABLE'
BEGIN SELECT RAISE(ABORT, 'available publication is terminal'); END;
