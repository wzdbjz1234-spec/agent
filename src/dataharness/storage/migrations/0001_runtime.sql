-- DataHarness Runtime SQLite 初始 schema。
-- 领域对象使用独立表，复合外键把 project/task/run/snapshot 的归属关系下沉到数据库约束。

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    created_at TEXT NOT NULL,
    archived_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 0 CHECK (row_version >= 0)
);

CREATE TABLE project_files (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, name),
    UNIQUE (id, project_id)
);

CREATE TABLE project_file_versions (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    status TEXT NOT NULL CHECK (status IN ('IMPORTING', 'READY', 'FAILED', 'UNSUPPORTED')),
    content_hash TEXT,
    byte_size INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
    media_type TEXT,
    created_at TEXT NOT NULL,
    finalized_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    FOREIGN KEY (file_id, project_id) REFERENCES project_files(id, project_id) ON DELETE RESTRICT,
    UNIQUE (file_id, version_number),
    UNIQUE (id, file_id, project_id),
    CHECK (
        (status = 'IMPORTING' AND finalized_at IS NULL) OR
        (status = 'READY' AND finalized_at IS NOT NULL AND content_hash IS NOT NULL
            AND byte_size IS NOT NULL AND media_type IS NOT NULL) OR
        (status IN ('FAILED', 'UNSUPPORTED') AND finalized_at IS NOT NULL)
    )
);

CREATE TABLE project_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    index_version TEXT,
    UNIQUE (id, project_id)
);

CREATE TABLE snapshot_entries (
    snapshot_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    file_version_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('IMPORTING', 'READY', 'FAILED', 'UNSUPPORTED')),
    content_hash TEXT,
    PRIMARY KEY (snapshot_id, file_id),
    FOREIGN KEY (snapshot_id, project_id) REFERENCES project_snapshots(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (file_version_id, file_id, project_id)
        REFERENCES project_file_versions(id, file_id, project_id) ON DELETE RESTRICT
);

CREATE TABLE snapshot_datasets (
    snapshot_id TEXT NOT NULL REFERENCES project_snapshots(id) ON DELETE RESTRICT,
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position >= 0),
    PRIMARY KEY (snapshot_id, dataset_id),
    UNIQUE (snapshot_id, position)
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    label TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('QUEUED', 'ACTIVE', 'WAITING', 'COMPLETED', 'FAILED', 'CANCELLED')),
    wait_reason TEXT CHECK (wait_reason IS NULL OR wait_reason IN ('USER_INPUT', 'BUDGET_EXHAUSTED', 'RETRY_APPROVAL', 'MISSING_DEPENDENCY')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    UNIQUE (id, project_id),
    CHECK ((status = 'WAITING') = (wait_reason IS NOT NULL)),
    CHECK ((status IN ('COMPLETED', 'FAILED', 'CANCELLED')) = (completed_at IS NOT NULL))
);

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    project_snapshot_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'WAITING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    phase TEXT NOT NULL CHECK (phase IN ('PREPARING', 'REASONING', 'EXECUTING', 'VERIFYING', 'FINALIZING')),
    wait_reason TEXT CHECK (wait_reason IS NULL OR wait_reason IN ('USER_INPUT', 'BUDGET_EXHAUSTED', 'RETRY_APPROVAL', 'MISSING_DEPENDENCY')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    cancel_requested_at TEXT,
    lease_owner TEXT,
    lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    FOREIGN KEY (task_id, project_id) REFERENCES tasks(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (project_snapshot_id, project_id) REFERENCES project_snapshots(id, project_id) ON DELETE RESTRICT,
    UNIQUE (id, task_id),
    UNIQUE (id, project_id),
    CHECK ((status = 'WAITING') = (wait_reason IS NOT NULL)),
    CHECK ((status IN ('SUCCEEDED', 'FAILED', 'CANCELLED')) = (completed_at IS NOT NULL)),
    CHECK ((lease_owner IS NULL AND lease_expires_at IS NULL AND heartbeat_at IS NULL)
        OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL))
);

CREATE INDEX runs_claim_idx ON runs(status, lease_expires_at, created_at);

CREATE TABLE analysis_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED')),
    failure_kind TEXT CHECK (failure_kind IS NULL OR failure_kind IN ('MODEL_CORRECTABLE', 'RESOURCE_LIMIT', 'SANDBOX_ERROR', 'INVALID_OUTPUT', 'POLICY_DENIED', 'INTERNAL_ERROR')),
    retry_of_step_id TEXT REFERENCES analysis_steps(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    UNIQUE (id, run_id),
    CHECK ((status = 'FAILED') = (failure_kind IS NOT NULL)),
    CHECK ((status IN ('SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED')) = (finished_at IS NOT NULL))
);

CREATE TABLE datasets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id, project_id) REFERENCES tasks(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (run_id, project_id) REFERENCES runs(id, project_id) ON DELETE RESTRICT
);

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id, project_id) REFERENCES tasks(id, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (run_id, project_id) REFERENCES runs(id, project_id) ON DELETE RESTRICT
);

CREATE TABLE coverage_reports (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES project_snapshots(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TABLE coverage_items (
    report_id TEXT NOT NULL REFERENCES coverage_reports(id) ON DELETE RESTRICT,
    file_version_id TEXT NOT NULL REFERENCES project_file_versions(id) ON DELETE RESTRICT,
    file_id TEXT NOT NULL REFERENCES project_files(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('PROCESSED', 'FAILED', 'UNSUPPORTED', 'SKIPPED')),
    detail TEXT,
    PRIMARY KEY (report_id, file_version_id)
);

CREATE TABLE findings (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL,
    project_snapshot_id TEXT NOT NULL REFERENCES project_snapshots(id) ON DELETE RESTRICT,
    summary TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'VERIFIED', 'WARNING', 'REJECTED')),
    created_at TEXT NOT NULL,
    verified_at TEXT,
    row_version INTEGER NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    FOREIGN KEY (run_id, task_id) REFERENCES runs(id, task_id) ON DELETE RESTRICT,
    CHECK ((status = 'DRAFT') = (verified_at IS NULL))
);

CREATE TABLE finding_evidence (
    finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position >= 0),
    kind TEXT NOT NULL CHECK (kind IN ('FILE', 'STEP', 'DATASET', 'ARTIFACT')),
    target_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    locator TEXT,
    PRIMARY KEY (finding_id, position)
);

CREATE TABLE lineage (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('FILE_VERSION', 'STEP', 'DATASET', 'ARTIFACT', 'FINDING')),
    source_id TEXT NOT NULL,
    source_hash TEXT,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('FILE_VERSION', 'STEP', 'DATASET', 'ARTIFACT', 'FINDING')),
    target_id TEXT NOT NULL,
    target_hash TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, source_kind, source_id, target_kind, target_id)
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX events_aggregate_idx ON events(aggregate_type, aggregate_id, id);

CREATE TABLE checkpoint_metadata (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    checkpoint_ref TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, sequence),
    UNIQUE (run_id, checkpoint_ref)
);

CREATE TABLE idempotency_keys (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result_ref TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);

-- Snapshot 及其成员是追加式事实；任何更新或删除都意味着既有 Run 的数据视图漂移。
CREATE TRIGGER project_snapshots_no_update BEFORE UPDATE ON project_snapshots
BEGIN SELECT RAISE(ABORT, 'project snapshot is immutable'); END;
CREATE TRIGGER project_snapshots_no_delete BEFORE DELETE ON project_snapshots
BEGIN SELECT RAISE(ABORT, 'project snapshot is immutable'); END;
CREATE TRIGGER snapshot_entries_no_update BEFORE UPDATE ON snapshot_entries
BEGIN SELECT RAISE(ABORT, 'snapshot entry is immutable'); END;
CREATE TRIGGER snapshot_entries_no_delete BEFORE DELETE ON snapshot_entries
BEGIN SELECT RAISE(ABORT, 'snapshot entry is immutable'); END;
CREATE TRIGGER snapshot_datasets_no_update BEFORE UPDATE ON snapshot_datasets
BEGIN SELECT RAISE(ABORT, 'snapshot dataset is immutable'); END;
CREATE TRIGGER snapshot_datasets_no_delete BEFORE DELETE ON snapshot_datasets
BEGIN SELECT RAISE(ABORT, 'snapshot dataset is immutable'); END;

-- CoverageItem 必须来自报告固定 Snapshot，避免 FULL_PROJECT 报告引用视图外文件。
CREATE TRIGGER coverage_item_must_belong_to_snapshot BEFORE INSERT ON coverage_items
WHEN NOT EXISTS (
    SELECT 1
    FROM coverage_reports AS report
    JOIN snapshot_entries AS entry ON entry.snapshot_id = report.snapshot_id
    WHERE report.id = NEW.report_id
      AND entry.file_version_id = NEW.file_version_id
      AND entry.file_id = NEW.file_id
)
BEGIN SELECT RAISE(ABORT, 'coverage item is outside snapshot'); END;

-- 文件版本只允许 IMPORTING 定稿一次；定稿后的字段与状态均不可修改。
CREATE TRIGGER file_versions_final_immutable BEFORE UPDATE ON project_file_versions
WHEN OLD.status <> 'IMPORTING'
BEGIN SELECT RAISE(ABORT, 'project file version is finalized'); END;

-- Run 固定 Task、Project 与 Snapshot，lease/CAS 更新也不得改写输入视图。
CREATE TRIGGER runs_identity_immutable BEFORE UPDATE ON runs
WHEN NEW.task_id <> OLD.task_id OR NEW.project_id <> OLD.project_id
    OR NEW.project_snapshot_id <> OLD.project_snapshot_id
BEGIN SELECT RAISE(ABORT, 'run identity and snapshot are immutable'); END;
