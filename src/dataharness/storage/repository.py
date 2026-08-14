# ruff: noqa: E501
"""Runtime SQLite repository。

Repository 把 SQL 行重建为冻结领域对象，并把所有状态写入变成带 ``row_version`` 的
compare-and-set。状态和对应事件共用调用方 UnitOfWork，因而只能一起成功或回滚。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, TypeVar

from dataharness.domain import (
    AnalysisStep,
    Artifact,
    ArtifactId,
    ContentHash,
    CoverageItem,
    CoverageItemStatus,
    CoverageReportId,
    Dataset,
    DatasetId,
    EvidenceKind,
    EvidenceRef,
    FileId,
    FileVersionId,
    FileVersionStatus,
    Finding,
    FindingCandidate,
    FindingId,
    FindingStatus,
    Lineage,
    LineageId,
    Project,
    ProjectCoverageReport,
    ProjectFile,
    ProjectFileVersion,
    ProjectId,
    ProjectSnapshot,
    ProjectStatus,
    ResourceKind,
    ResourceRef,
    Run,
    RunId,
    RunPhase,
    RunStatus,
    Session,
    SessionId,
    SnapshotEntry,
    SnapshotId,
    StepFailureKind,
    StepId,
    StepStatus,
    Task,
    TaskId,
    TaskStatus,
    WaitReason,
)
from dataharness.domain.finding import FINDING_TRANSITIONS
from dataharness.domain.run import RUN_PHASE_TRANSITIONS, RUN_TRANSITIONS
from dataharness.domain.step import STEP_TRANSITIONS
from dataharness.domain.task import TASK_TRANSITIONS

from .errors import (
    ConcurrencyConflictError,
    IdempotencyConflictError,
    InvalidMetadataError,
    LeaseLostError,
    RecordNotFoundError,
)
from .records import (
    CheckpointMetadata,
    EventRecord,
    IdempotencyRecord,
    RetryRecord,
    RunLease,
    StoredRecord,
)

T = TypeVar("T")

# Phase 04 才会引入完整 Secret/PII 检测；storage 先以窄字段与大小上限阻止最常见的
# 原始模型载荷、凭据和隐私映射误写。事件只应承载 ID、epoch、序号等审计元数据。
_FORBIDDEN_EVENT_KEY_PARTS = frozenset(
    {"secret", "password", "token", "api_key", "pii", "prompt", "response", "payload"}
)
_MAX_EVENT_PAYLOAD_BYTES = 16 * 1024


def _dt(value: str) -> datetime:
    """读取数据库的必填时间；schema 破损时立即失败，不以默认值掩盖。"""
    return datetime.fromisoformat(value)


def _optional_dt(value: str | None) -> datetime | None:
    """读取数据库允许为空的时间。"""
    return _dt(value) if value is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _required(row: sqlite3.Row | None, kind: str, identity: object) -> sqlite3.Row:
    if row is None:
        raise RecordNotFoundError(f"{kind} {identity} 不存在")
    return row


def _check_transition(
    table: dict[Any, frozenset[Any]], current: Any, target: Any, aggregate: str
) -> None:
    """Repository 防御性校验，阻止调用方用直接构造对象绕过领域方法。"""
    if current != target and target not in table.get(current, frozenset()):
        raise ConcurrencyConflictError(f"{aggregate} 非法持久化迁移：{current} -> {target}")


class RuntimeRepository:
    """绑定单个 UnitOfWork 连接的 Runtime 元数据仓库。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def append_event(
        self,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        occurred_at: datetime,
        payload: dict[str, object] | None = None,
    ) -> None:
        """追加脱敏事件；payload 使用稳定 JSON，禁止传入领域大对象或原始模型载荷。"""
        event_payload = payload or {}
        for key in event_payload:
            normalized = key.casefold()
            if any(part in normalized for part in _FORBIDDEN_EVENT_KEY_PARTS):
                raise InvalidMetadataError(f"事件元数据字段不允许保存敏感或原始内容：{key}")
        payload_json = json.dumps(
            event_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if len(payload_json.encode("utf-8")) > _MAX_EVENT_PAYLOAD_BYTES:
            raise InvalidMetadataError("事件元数据超过 16 KiB 上限")
        self._connection.execute(
            "INSERT INTO events(aggregate_type, aggregate_id, event_type, occurred_at, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                aggregate_type,
                aggregate_id,
                event_type,
                _iso(occurred_at),
                payload_json,
            ),
        )

    def list_events(self, aggregate_type: str, aggregate_id: str) -> tuple[EventRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM events WHERE aggregate_type = ? AND aggregate_id = ? ORDER BY id",
            (aggregate_type, aggregate_id),
        ).fetchall()
        return tuple(
            EventRecord(
                id=row["id"],
                aggregate_type=row["aggregate_type"],
                aggregate_id=row["aggregate_id"],
                event_type=row["event_type"],
                occurred_at=_dt(row["occurred_at"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        )

    def add_project(self, project: Project) -> None:
        self._connection.execute(
            "INSERT INTO projects(id, name, status, created_at, archived_at) VALUES (?, ?, ?, ?, ?)",
            (
                str(project.id),
                project.name,
                project.status,
                _iso(project.created_at),
                _iso(project.archived_at),
            ),
        )
        self.append_event("project", str(project.id), "PROJECT_CREATED", project.created_at)

    def list_projects(self) -> tuple[Project, ...]:
        """按创建时间返回项目窄视图，供本地控制面展示，不暴露 SQL 行。"""
        rows = self._connection.execute("SELECT * FROM projects ORDER BY created_at, id").fetchall()
        return tuple(
            Project(
                id=ProjectId(row["id"]),
                name=row["name"],
                status=ProjectStatus(row["status"]),
                created_at=_dt(row["created_at"]),
                archived_at=_optional_dt(row["archived_at"]),
            )
            for row in rows
        )

    def get_project(self, project_id: ProjectId) -> StoredRecord[Project]:
        row = _required(
            self._connection.execute(
                "SELECT * FROM projects WHERE id = ?", (str(project_id),)
            ).fetchone(),
            "Project",
            project_id,
        )
        value = Project(
            id=ProjectId(row["id"]),
            name=row["name"],
            status=ProjectStatus(row["status"]),
            created_at=_dt(row["created_at"]),
            archived_at=_optional_dt(row["archived_at"]),
        )
        return StoredRecord(value=value, version=row["row_version"])

    def save_project(self, project: Project, expected_version: int) -> StoredRecord[Project]:
        current = self.get_project(project.id)
        if current.value.name != project.name or current.value.created_at != project.created_at:
            raise ConcurrencyConflictError("Project 身份字段不可修改")
        if (
            current.value.status == ProjectStatus.ARCHIVED
            and project.status != current.value.status
        ):
            raise ConcurrencyConflictError("终态 Project 不可回退")
        result = self._connection.execute(
            "UPDATE projects SET status = ?, archived_at = ?, row_version = row_version + 1 "
            "WHERE id = ? AND row_version = ? AND status = ?",
            (
                project.status,
                _iso(project.archived_at),
                str(project.id),
                expected_version,
                current.value.status,
            ),
        )
        self._require_cas(result, "Project", project.id)
        self.append_event(
            "project",
            str(project.id),
            "PROJECT_ARCHIVED",
            project.archived_at or project.created_at,
        )
        return StoredRecord(value=project, version=expected_version + 1)

    def add_file(self, file: ProjectFile) -> None:
        self._connection.execute(
            "INSERT INTO project_files(id, project_id, name, created_at) VALUES (?, ?, ?, ?)",
            (str(file.id), str(file.project_id), file.name, _iso(file.created_at)),
        )
        self.append_event("project_file", str(file.id), "PROJECT_FILE_CREATED", file.created_at)

    def get_file(self, file_id: FileId) -> ProjectFile:
        row = _required(
            self._connection.execute(
                "SELECT * FROM project_files WHERE id = ?", (str(file_id),)
            ).fetchone(),
            "ProjectFile",
            file_id,
        )
        return ProjectFile(
            id=FileId(row["id"]),
            project_id=ProjectId(row["project_id"]),
            name=row["name"],
            created_at=_dt(row["created_at"]),
        )

    def list_project_files(self, project_id: ProjectId) -> tuple[ProjectFile, ...]:
        """按稳定名称列出项目逻辑文件，供 ProjectCorpus 组装当前视图。"""
        rows = self._connection.execute(
            "SELECT * FROM project_files WHERE project_id = ? ORDER BY name, id",
            (str(project_id),),
        ).fetchall()
        return tuple(
            ProjectFile(
                id=FileId(row["id"]),
                project_id=ProjectId(row["project_id"]),
                name=row["name"],
                created_at=_dt(row["created_at"]),
            )
            for row in rows
        )

    def add_file_version(self, version: ProjectFileVersion) -> None:
        self._connection.execute(
            "INSERT INTO project_file_versions(id, file_id, project_id, version_number, status, "
            "content_hash, byte_size, media_type, created_at, finalized_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(version.id),
                str(version.file_id),
                str(version.project_id),
                version.version_number,
                version.status,
                version.content_hash,
                version.byte_size,
                version.media_type,
                _iso(version.created_at),
                _iso(version.finalized_at),
            ),
        )
        self.append_event(
            "file_version", str(version.id), "FILE_VERSION_CREATED", version.created_at
        )

    def get_file_version(self, version_id: FileVersionId) -> StoredRecord[ProjectFileVersion]:
        row = _required(
            self._connection.execute(
                "SELECT * FROM project_file_versions WHERE id = ?", (str(version_id),)
            ).fetchone(),
            "ProjectFileVersion",
            version_id,
        )
        value = ProjectFileVersion(
            id=FileVersionId(row["id"]),
            file_id=FileId(row["file_id"]),
            project_id=ProjectId(row["project_id"]),
            version_number=row["version_number"],
            status=FileVersionStatus(row["status"]),
            content_hash=ContentHash(row["content_hash"]) if row["content_hash"] else None,
            byte_size=row["byte_size"],
            media_type=row["media_type"],
            created_at=_dt(row["created_at"]),
            finalized_at=_optional_dt(row["finalized_at"]),
        )
        return StoredRecord(value=value, version=row["row_version"])

    def list_file_versions(self, file_id: FileId) -> tuple[ProjectFileVersion, ...]:
        """按版本号列出不可变历史；调用方据此创建下一版本。"""
        rows = self._connection.execute(
            "SELECT id FROM project_file_versions WHERE file_id = ? ORDER BY version_number",
            (str(file_id),),
        ).fetchall()
        return tuple(self.get_file_version(FileVersionId(row["id"])).value for row in rows)

    def list_current_file_versions(self, project_id: ProjectId) -> tuple[ProjectFileVersion, ...]:
        """每个逻辑文件只返回最高版本号，包含失败和不支持状态。"""
        rows = self._connection.execute(
            "SELECT version.id FROM project_file_versions AS version "
            "JOIN (SELECT file_id, MAX(version_number) AS number FROM project_file_versions "
            "GROUP BY file_id) AS latest ON latest.file_id = version.file_id "
            "AND latest.number = version.version_number "
            "WHERE version.project_id = ? ORDER BY version.file_id",
            (str(project_id),),
        ).fetchall()
        return tuple(self.get_file_version(FileVersionId(row["id"])).value for row in rows)

    def list_project_datasets(self, project_id: ProjectId) -> tuple[Dataset, ...]:
        """列出已登记项目 Dataset；调用方只应登记完成发布的正式资源。"""
        rows = self._connection.execute(
            "SELECT id FROM datasets WHERE project_id = ? ORDER BY created_at, id",
            (str(project_id),),
        ).fetchall()
        return tuple(self.get_dataset(DatasetId(row["id"])) for row in rows)

    def list_project_artifacts(self, project_id: ProjectId) -> tuple[Artifact, ...]:
        """列出项目正式 Artifact；文件内容仍由 Workspace 发布事实校验。"""
        rows = self._connection.execute(
            "SELECT id FROM artifacts WHERE project_id = ? ORDER BY created_at, id",
            (str(project_id),),
        ).fetchall()
        return tuple(self.get_artifact(ArtifactId(row["id"])) for row in rows)

    def list_runs_for_task(self, task_id: TaskId) -> tuple[Run, ...]:
        """返回 Task 的 Run 元数据，供取消、恢复和事件查询选择正确聚合。"""
        rows = self._connection.execute(
            "SELECT * FROM runs WHERE task_id = ? ORDER BY created_at, id", (str(task_id),)
        ).fetchall()
        return tuple(self._run_from_row(row) for row in rows)

    def finalize_file_version(
        self, version: ProjectFileVersion, expected_version: int
    ) -> StoredRecord[ProjectFileVersion]:
        current = self.get_file_version(version.id)
        if (
            current.value.status != FileVersionStatus.IMPORTING
            or version.status == FileVersionStatus.IMPORTING
        ):
            raise ConcurrencyConflictError("文件版本只能从 IMPORTING 定稿一次")
        if (
            current.value.file_id,
            current.value.project_id,
            current.value.version_number,
            current.value.created_at,
        ) != (version.file_id, version.project_id, version.version_number, version.created_at):
            raise ConcurrencyConflictError("ProjectFileVersion 身份字段不可修改")
        result = self._connection.execute(
            "UPDATE project_file_versions SET status = ?, content_hash = ?, byte_size = ?, media_type = ?, "
            "finalized_at = ?, row_version = row_version + 1 WHERE id = ? AND status = 'IMPORTING' AND row_version = ?",
            (
                version.status,
                version.content_hash,
                version.byte_size,
                version.media_type,
                _iso(version.finalized_at),
                str(version.id),
                expected_version,
            ),
        )
        self._require_cas(result, "ProjectFileVersion", version.id)
        self.append_event(
            "file_version",
            str(version.id),
            f"FILE_VERSION_{version.status}",
            version.finalized_at or version.created_at,
        )
        return StoredRecord(value=version, version=expected_version + 1)

    def add_snapshot(self, snapshot: ProjectSnapshot) -> None:
        """在同一事务写入 Snapshot 头、固定文件版本成员与 Dataset 版本成员。"""
        self._connection.execute(
            "INSERT INTO project_snapshots(id, project_id, created_at, index_version) VALUES (?, ?, ?, ?)",
            (
                str(snapshot.id),
                str(snapshot.project_id),
                _iso(snapshot.created_at),
                snapshot.index_version,
            ),
        )
        self._connection.executemany(
            "INSERT INTO snapshot_entries(snapshot_id, project_id, file_version_id, file_id, version_number, status, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    str(snapshot.id),
                    str(snapshot.project_id),
                    str(entry.file_version_id),
                    str(entry.file_id),
                    entry.version_number,
                    entry.status,
                    entry.content_hash,
                )
                for entry in snapshot.entries
            ],
        )
        self._connection.executemany(
            "INSERT INTO snapshot_datasets(snapshot_id, dataset_id, position) VALUES (?, ?, ?)",
            [
                (str(snapshot.id), str(dataset_id), position)
                for position, dataset_id in enumerate(snapshot.dataset_version_ids)
            ],
        )
        self.append_event("snapshot", str(snapshot.id), "SNAPSHOT_CREATED", snapshot.created_at)

    def get_snapshot(self, snapshot_id: SnapshotId) -> ProjectSnapshot:
        row = _required(
            self._connection.execute(
                "SELECT * FROM project_snapshots WHERE id = ?", (str(snapshot_id),)
            ).fetchone(),
            "ProjectSnapshot",
            snapshot_id,
        )
        entries = self._connection.execute(
            "SELECT * FROM snapshot_entries WHERE snapshot_id = ? ORDER BY file_id",
            (str(snapshot_id),),
        ).fetchall()
        datasets = self._connection.execute(
            "SELECT dataset_id FROM snapshot_datasets WHERE snapshot_id = ? ORDER BY position",
            (str(snapshot_id),),
        ).fetchall()
        return ProjectSnapshot(
            id=SnapshotId(row["id"]),
            project_id=ProjectId(row["project_id"]),
            created_at=_dt(row["created_at"]),
            index_version=row["index_version"],
            entries=tuple(
                SnapshotEntry(
                    file_version_id=FileVersionId(item["file_version_id"]),
                    file_id=FileId(item["file_id"]),
                    version_number=item["version_number"],
                    status=FileVersionStatus(item["status"]),
                    content_hash=ContentHash(item["content_hash"])
                    if item["content_hash"]
                    else None,
                )
                for item in entries
            ),
            dataset_version_ids=tuple(DatasetId(item["dataset_id"]) for item in datasets),
        )

    def add_session(self, session: Session) -> None:
        self._connection.execute(
            "INSERT INTO sessions(id, label, created_at) VALUES (?, ?, ?)",
            (str(session.id), session.label, _iso(session.created_at)),
        )
        self.append_event("session", str(session.id), "SESSION_CREATED", session.created_at)

    def get_session(self, session_id: SessionId) -> Session:
        row = _required(
            self._connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (str(session_id),)
            ).fetchone(),
            "Session",
            session_id,
        )
        return Session(
            id=SessionId(row["id"]), label=row["label"], created_at=_dt(row["created_at"])
        )

    def add_task(self, task: Task) -> None:
        self._connection.execute(
            "INSERT INTO tasks(id, project_id, session_id, status, wait_reason, created_at, updated_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(task.id),
                str(task.project_id),
                str(task.session_id) if task.session_id else None,
                task.status,
                task.wait_reason,
                _iso(task.created_at),
                _iso(task.updated_at),
                _iso(task.completed_at),
            ),
        )
        self.append_event("task", str(task.id), "TASK_CREATED", task.created_at)

    def get_task(self, task_id: TaskId) -> StoredRecord[Task]:
        row = _required(
            self._connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (str(task_id),)
            ).fetchone(),
            "Task",
            task_id,
        )
        value = Task(
            id=TaskId(row["id"]),
            project_id=ProjectId(row["project_id"]),
            session_id=SessionId(row["session_id"]) if row["session_id"] else None,
            status=TaskStatus(row["status"]),
            wait_reason=WaitReason(row["wait_reason"]) if row["wait_reason"] else None,
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
            completed_at=_optional_dt(row["completed_at"]),
        )
        return StoredRecord(value=value, version=row["row_version"])

    def save_task(self, task: Task, expected_version: int, event_type: str) -> StoredRecord[Task]:
        current = self.get_task(task.id)
        if (current.value.project_id, current.value.session_id, current.value.created_at) != (
            task.project_id,
            task.session_id,
            task.created_at,
        ):
            raise ConcurrencyConflictError("Task 身份字段不可修改")
        _check_transition(TASK_TRANSITIONS, current.value.status, task.status, "Task")
        result = self._connection.execute(
            "UPDATE tasks SET status = ?, wait_reason = ?, updated_at = ?, completed_at = ?, row_version = row_version + 1 "
            "WHERE id = ? AND row_version = ? AND status = ?",
            (
                task.status,
                task.wait_reason,
                _iso(task.updated_at),
                _iso(task.completed_at),
                str(task.id),
                expected_version,
                current.value.status,
            ),
        )
        self._require_cas(result, "Task", task.id)
        self.append_event("task", str(task.id), event_type, task.updated_at)
        return StoredRecord(value=task, version=expected_version + 1)

    def add_run(self, run: Run) -> None:
        self._connection.execute(
            "INSERT INTO runs(id, task_id, project_id, project_snapshot_id, status, phase, wait_reason, created_at, updated_at, completed_at, cancel_requested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(run.id),
                str(run.task_id),
                str(run.project_id),
                str(run.project_snapshot_id),
                run.status,
                run.phase,
                run.wait_reason,
                _iso(run.created_at),
                _iso(run.updated_at),
                _iso(run.completed_at),
                _iso(run.cancel_requested_at),
            ),
        )
        self.append_event("run", str(run.id), "RUN_CREATED", run.created_at)

    def get_run(self, run_id: RunId) -> StoredRecord[Run]:
        row = _required(
            self._connection.execute("SELECT * FROM runs WHERE id = ?", (str(run_id),)).fetchone(),
            "Run",
            run_id,
        )
        return StoredRecord(value=self._run_from_row(row), version=row["row_version"])

    def get_run_lease(self, run_id: RunId) -> RunLease | None:
        row = _required(
            self._connection.execute("SELECT * FROM runs WHERE id = ?", (str(run_id),)).fetchone(),
            "Run",
            run_id,
        )
        if row["lease_owner"] is None:
            return None
        return RunLease(
            run_id=run_id,
            owner=row["lease_owner"],
            epoch=row["lease_epoch"],
            expires_at=_dt(row["lease_expires_at"]),
            heartbeat_at=_dt(row["heartbeat_at"]),
        )

    def save_run(
        self,
        run: Run,
        expected_version: int,
        event_type: str,
        *,
        lease: RunLease | None = None,
        lease_checked_at: datetime | None = None,
    ) -> StoredRecord[Run]:
        current = self.get_run(run.id)
        if (
            current.value.task_id,
            current.value.project_id,
            current.value.project_snapshot_id,
            current.value.created_at,
        ) != (run.task_id, run.project_id, run.project_snapshot_id, run.created_at):
            raise ConcurrencyConflictError("Run 身份与 project_snapshot_id 不可修改")
        _check_transition(RUN_TRANSITIONS, current.value.status, run.status, "Run")
        _check_transition(RUN_PHASE_TRANSITIONS, current.value.phase, run.phase, "Run.phase")
        where = "id = ? AND row_version = ? AND status = ?"
        params: list[object] = [
            run.status,
            run.phase,
            run.wait_reason,
            _iso(run.updated_at),
            _iso(run.completed_at),
            _iso(run.cancel_requested_at),
            str(run.id),
            expected_version,
            current.value.status,
        ]
        if lease is not None:
            if lease.run_id != run.id:
                raise LeaseLostError("lease 不属于待提交 Run")
            if lease_checked_at is None:
                raise ValueError("带 lease 提交 Run 时必须提供 lease_checked_at")
            # 仅比较 owner/epoch 仍允许“已到期但尚未回收”的 Worker 提交；加入数据库中的
            # expires_at 判断，使 lease 到期本身就构成 fencing 边界。
            where += " AND lease_owner = ? AND lease_epoch = ? AND lease_expires_at > ?"
            params.extend([lease.owner, lease.epoch, _iso(lease_checked_at)])
        result = self._connection.execute(
            "UPDATE runs SET status = ?, phase = ?, wait_reason = ?, updated_at = ?, completed_at = ?, cancel_requested_at = ?, "
            "lease_owner = CASE WHEN ? IN ('WAITING', 'SUCCEEDED', 'FAILED', 'CANCELLED') THEN NULL ELSE lease_owner END, "
            "lease_expires_at = CASE WHEN ? IN ('WAITING', 'SUCCEEDED', 'FAILED', 'CANCELLED') THEN NULL ELSE lease_expires_at END, "
            "heartbeat_at = CASE WHEN ? IN ('WAITING', 'SUCCEEDED', 'FAILED', 'CANCELLED') THEN NULL ELSE heartbeat_at END, "
            f"row_version = row_version + 1 WHERE {where}",
            [*params[:6], run.status, run.status, run.status, *params[6:]],
        )
        if result.rowcount != 1 and lease is not None:
            raise LeaseLostError(f"Run {run.id} 的 lease epoch 已失效")
        self._require_cas(result, "Run", run.id)
        self.append_event(
            "run",
            str(run.id),
            event_type,
            run.updated_at,
            {"lease_epoch": lease.epoch} if lease else None,
        )
        return StoredRecord(value=run, version=expected_version + 1)

    def add_step(self, step: AnalysisStep) -> None:
        self._connection.execute(
            "INSERT INTO analysis_steps(id, run_id, status, failure_kind, retry_of_step_id, created_at, started_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(step.id),
                str(step.run_id),
                step.status,
                step.failure_kind,
                str(step.retry_of_step_id) if step.retry_of_step_id else None,
                _iso(step.created_at),
                _iso(step.started_at),
                _iso(step.finished_at),
            ),
        )
        self.append_event("step", str(step.id), "STEP_CREATED", step.created_at)

    def get_step(self, step_id: StepId) -> StoredRecord[AnalysisStep]:
        row = _required(
            self._connection.execute(
                "SELECT * FROM analysis_steps WHERE id = ?", (str(step_id),)
            ).fetchone(),
            "AnalysisStep",
            step_id,
        )
        value = AnalysisStep(
            id=StepId(row["id"]),
            run_id=RunId(row["run_id"]),
            status=StepStatus(row["status"]),
            failure_kind=StepFailureKind(row["failure_kind"]) if row["failure_kind"] else None,
            retry_of_step_id=StepId(row["retry_of_step_id"]) if row["retry_of_step_id"] else None,
            created_at=_dt(row["created_at"]),
            started_at=_optional_dt(row["started_at"]),
            finished_at=_optional_dt(row["finished_at"]),
        )
        return StoredRecord(value=value, version=row["row_version"])

    def save_step(
        self, step: AnalysisStep, expected_version: int, event_type: str
    ) -> StoredRecord[AnalysisStep]:
        current = self.get_step(step.id)
        if (current.value.run_id, current.value.retry_of_step_id, current.value.created_at) != (
            step.run_id,
            step.retry_of_step_id,
            step.created_at,
        ):
            raise ConcurrencyConflictError("AnalysisStep 身份与 retry_of_step_id 不可修改")
        _check_transition(STEP_TRANSITIONS, current.value.status, step.status, "AnalysisStep")
        result = self._connection.execute(
            "UPDATE analysis_steps SET status = ?, failure_kind = ?, started_at = ?, finished_at = ?, row_version = row_version + 1 "
            "WHERE id = ? AND row_version = ? AND status = ?",
            (
                step.status,
                step.failure_kind,
                _iso(step.started_at),
                _iso(step.finished_at),
                str(step.id),
                expected_version,
                current.value.status,
            ),
        )
        self._require_cas(result, "AnalysisStep", step.id)
        self.append_event(
            "step", str(step.id), event_type, step.finished_at or step.started_at or step.created_at
        )
        return StoredRecord(value=step, version=expected_version + 1)

    def add_dataset(self, dataset: Dataset) -> None:
        self._add_resource("datasets", dataset)
        self.append_event("dataset", str(dataset.id), "DATASET_REGISTERED", dataset.created_at)

    def get_dataset(self, dataset_id: DatasetId) -> Dataset:
        row = _required(
            self._connection.execute(
                "SELECT * FROM datasets WHERE id = ?", (str(dataset_id),)
            ).fetchone(),
            "Dataset",
            dataset_id,
        )
        return Dataset(
            id=DatasetId(row["id"]),
            project_id=ProjectId(row["project_id"]),
            name=row["name"],
            content_hash=ContentHash(row["content_hash"]),
            task_id=TaskId(row["task_id"]) if row["task_id"] else None,
            run_id=RunId(row["run_id"]) if row["run_id"] else None,
            created_at=_dt(row["created_at"]),
        )

    def add_artifact(self, artifact: Artifact) -> None:
        self._add_resource("artifacts", artifact)
        self.append_event("artifact", str(artifact.id), "ARTIFACT_REGISTERED", artifact.created_at)

    def get_artifact(self, artifact_id: ArtifactId) -> Artifact:
        row = _required(
            self._connection.execute(
                "SELECT * FROM artifacts WHERE id = ?", (str(artifact_id),)
            ).fetchone(),
            "Artifact",
            artifact_id,
        )
        return Artifact(
            id=ArtifactId(row["id"]),
            project_id=ProjectId(row["project_id"]),
            name=row["name"],
            content_hash=ContentHash(row["content_hash"]),
            task_id=TaskId(row["task_id"]) if row["task_id"] else None,
            run_id=RunId(row["run_id"]) if row["run_id"] else None,
            created_at=_dt(row["created_at"]),
        )

    def add_coverage_report(self, report: ProjectCoverageReport) -> None:
        self._connection.execute(
            "INSERT INTO coverage_reports(id, snapshot_id, created_at) VALUES (?, ?, ?)",
            (str(report.id), str(report.snapshot_id), _iso(report.created_at)),
        )
        self._connection.executemany(
            "INSERT INTO coverage_items(report_id, file_version_id, file_id, status, detail) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    str(report.id),
                    str(item.file_version_id),
                    str(item.file_id),
                    item.status,
                    item.detail,
                )
                for item in report.items
            ],
        )
        self.append_event("coverage", str(report.id), "COVERAGE_RECORDED", report.created_at)

    def get_coverage_report(self, report_id: CoverageReportId) -> ProjectCoverageReport:
        row = _required(
            self._connection.execute(
                "SELECT * FROM coverage_reports WHERE id = ?", (str(report_id),)
            ).fetchone(),
            "ProjectCoverageReport",
            report_id,
        )
        items = self._connection.execute(
            "SELECT * FROM coverage_items WHERE report_id = ? ORDER BY file_id", (str(report_id),)
        ).fetchall()
        return ProjectCoverageReport(
            id=CoverageReportId(row["id"]),
            snapshot_id=SnapshotId(row["snapshot_id"]),
            created_at=_dt(row["created_at"]),
            items=tuple(
                CoverageItem(
                    file_version_id=FileVersionId(item["file_version_id"]),
                    file_id=FileId(item["file_id"]),
                    status=CoverageItemStatus(item["status"]),
                    detail=item["detail"],
                )
                for item in items
            ),
        )

    def add_finding(self, finding: Finding) -> None:
        candidate = finding.candidate
        self._connection.execute(
            "INSERT INTO findings(id, task_id, run_id, project_snapshot_id, summary, status, created_at, verified_at, coverage_report_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(finding.id),
                str(candidate.task_id),
                str(candidate.run_id),
                str(candidate.project_snapshot_id),
                candidate.summary,
                finding.status,
                _iso(candidate.created_at),
                _iso(finding.verified_at),
                str(candidate.coverage_report_id) if candidate.coverage_report_id else None,
            ),
        )
        self._connection.executemany(
            "INSERT INTO finding_evidence(finding_id, position, kind, target_id, content_hash, locator) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    str(finding.id),
                    position,
                    evidence.kind,
                    evidence.target_id,
                    evidence.content_hash,
                    evidence.locator,
                )
                for position, evidence in enumerate(candidate.evidence)
            ],
        )
        self.append_event("finding", str(finding.id), "FINDING_CREATED", candidate.created_at)

    def get_finding(self, finding_id: FindingId) -> StoredRecord[Finding]:
        row = _required(
            self._connection.execute(
                "SELECT * FROM findings WHERE id = ?", (str(finding_id),)
            ).fetchone(),
            "Finding",
            finding_id,
        )
        evidence_rows = self._connection.execute(
            "SELECT * FROM finding_evidence WHERE finding_id = ? ORDER BY position",
            (str(finding_id),),
        ).fetchall()
        candidate = FindingCandidate(
            task_id=TaskId(row["task_id"]),
            run_id=RunId(row["run_id"]),
            project_snapshot_id=SnapshotId(row["project_snapshot_id"]),
            summary=row["summary"],
            coverage_report_id=(
                CoverageReportId(row["coverage_report_id"]) if row["coverage_report_id"] else None
            ),
            evidence=tuple(
                EvidenceRef(
                    kind=EvidenceKind(item["kind"]),
                    target_id=item["target_id"],
                    content_hash=ContentHash(item["content_hash"]),
                    locator=item["locator"],
                )
                for item in evidence_rows
            ),
            created_at=_dt(row["created_at"]),
        )
        return StoredRecord(
            value=Finding(
                id=FindingId(row["id"]),
                candidate=candidate,
                status=FindingStatus(row["status"]),
                verified_at=_optional_dt(row["verified_at"]),
            ),
            version=row["row_version"],
        )

    def save_finding(
        self, finding: Finding, expected_version: int, event_type: str
    ) -> StoredRecord[Finding]:
        current = self.get_finding(finding.id)
        if current.value.candidate != finding.candidate:
            raise ConcurrencyConflictError("FindingCandidate 创建后不可修改")
        _check_transition(FINDING_TRANSITIONS, current.value.status, finding.status, "Finding")
        result = self._connection.execute(
            "UPDATE findings SET status = ?, verified_at = ?, row_version = row_version + 1 WHERE id = ? AND row_version = ? AND status = ?",
            (
                finding.status,
                _iso(finding.verified_at),
                str(finding.id),
                expected_version,
                current.value.status,
            ),
        )
        self._require_cas(result, "Finding", finding.id)
        self.append_event(
            "finding",
            str(finding.id),
            event_type,
            finding.verified_at or finding.candidate.created_at,
        )
        return StoredRecord(value=finding, version=expected_version + 1)

    def add_lineage(self, lineage: Lineage) -> None:
        self._connection.execute(
            "INSERT INTO lineage(id, run_id, source_kind, source_id, source_hash, target_kind, target_id, target_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(lineage.id),
                str(lineage.run_id),
                lineage.source.kind,
                lineage.source.resource_id,
                lineage.source.content_hash,
                lineage.target.kind,
                lineage.target.resource_id,
                lineage.target.content_hash,
                _iso(lineage.created_at),
            ),
        )
        self.append_event("lineage", str(lineage.id), "LINEAGE_RECORDED", lineage.created_at)

    def get_lineage(self, lineage_id: LineageId) -> Lineage:
        row = _required(
            self._connection.execute(
                "SELECT * FROM lineage WHERE id = ?", (str(lineage_id),)
            ).fetchone(),
            "Lineage",
            lineage_id,
        )
        return Lineage(
            id=LineageId(row["id"]),
            run_id=RunId(row["run_id"]),
            source=ResourceRef(
                kind=ResourceKind(row["source_kind"]),
                resource_id=row["source_id"],
                content_hash=ContentHash(row["source_hash"]) if row["source_hash"] else None,
            ),
            target=ResourceRef(
                kind=ResourceKind(row["target_kind"]),
                resource_id=row["target_id"],
                content_hash=ContentHash(row["target_hash"]) if row["target_hash"] else None,
            ),
            created_at=_dt(row["created_at"]),
        )

    def add_checkpoint(self, checkpoint: CheckpointMetadata) -> None:
        existing = self._connection.execute(
            "SELECT * FROM checkpoint_metadata WHERE run_id = ? AND sequence = ?",
            (str(checkpoint.run_id), checkpoint.sequence),
        ).fetchone()
        if existing is not None:
            if existing["content_hash"] != checkpoint.content_hash:
                raise IdempotencyConflictError("同一 checkpoint sequence 已绑定不同内容")
            return
        self._connection.execute(
            "INSERT INTO checkpoint_metadata(id, run_id, sequence, checkpoint_ref, content_hash, created_at, project_snapshot_id, sandbox_id, sandbox_image_digest, run_lease_epoch, phase) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                checkpoint.id,
                str(checkpoint.run_id),
                checkpoint.sequence,
                checkpoint.checkpoint_ref,
                checkpoint.content_hash,
                _iso(checkpoint.created_at),
                str(checkpoint.project_snapshot_id) if checkpoint.project_snapshot_id else None,
                checkpoint.sandbox_id,
                checkpoint.sandbox_image_digest,
                checkpoint.run_lease_epoch,
                checkpoint.phase,
            ),
        )
        self.append_event(
            "run",
            str(checkpoint.run_id),
            "CHECKPOINT_RECORDED",
            checkpoint.created_at,
            {"sequence": checkpoint.sequence},
        )

    def latest_checkpoint(self, run_id: RunId) -> CheckpointMetadata | None:
        row = self._connection.execute(
            "SELECT * FROM checkpoint_metadata WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
            (str(run_id),),
        ).fetchone()
        if row is None:
            return None
        return CheckpointMetadata(
            id=row["id"],
            run_id=RunId(row["run_id"]),
            sequence=row["sequence"],
            checkpoint_ref=row["checkpoint_ref"],
            content_hash=ContentHash(row["content_hash"]),
            created_at=_dt(row["created_at"]),
            project_snapshot_id=(
                SnapshotId(row["project_snapshot_id"]) if row["project_snapshot_id"] else None
            ),
            sandbox_id=row["sandbox_id"],
            sandbox_image_digest=row["sandbox_image_digest"],
            run_lease_epoch=row["run_lease_epoch"],
            phase=RunPhase(row["phase"]) if row["phase"] else None,
        )

    def count_retry_attempts(self, run_id: RunId) -> int:
        """返回已持久化的自动重试次数，重启后仍以此值限制上限。"""
        row = self._connection.execute(
            "SELECT COUNT(*) FROM run_retry_attempts WHERE run_id = ?", (str(run_id),)
        ).fetchone()
        assert row is not None
        return int(row[0])

    def add_retry_attempt(self, retry: RetryRecord) -> RetryRecord:
        """插入一次重试记录；相同 attempt 重放时返回已有事实。"""
        existing = self._connection.execute(
            "SELECT * FROM run_retry_attempts WHERE run_id = ? AND attempt = ?",
            (str(retry.run_id), retry.attempt),
        ).fetchone()
        if existing is not None:
            return RetryRecord(
                run_id=RunId(existing["run_id"]),
                attempt=existing["attempt"],
                failure_kind=existing["failure_kind"],
                delay_seconds=existing["delay_seconds"],
                next_attempt_at=_dt(existing["next_attempt_at"]),
                created_at=_dt(existing["created_at"]),
            )
        self._connection.execute(
            "INSERT INTO run_retry_attempts(run_id, attempt, failure_kind, delay_seconds, next_attempt_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(retry.run_id),
                retry.attempt,
                retry.failure_kind,
                retry.delay_seconds,
                _iso(retry.next_attempt_at),
                _iso(retry.created_at),
            ),
        )
        self.append_event(
            "run",
            str(retry.run_id),
            "RUN_RETRY_SCHEDULED",
            retry.created_at,
            {"attempt": retry.attempt, "failure_kind": retry.failure_kind},
        )
        return retry

    def reserve_idempotency(self, record: IdempotencyRecord) -> IdempotencyRecord:
        """首次调用占位；相同请求摘要重放返回原记录，不同摘要稳定冲突。"""
        existing = self.get_idempotency(record.scope, record.key)
        if existing is not None:
            if existing.request_hash != record.request_hash:
                raise IdempotencyConflictError(f"幂等键 {record.scope}/{record.key} 已绑定其他请求")
            return existing
        self._connection.execute(
            "INSERT INTO idempotency_keys(scope, key, request_hash, result_ref, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                record.scope,
                record.key,
                record.request_hash,
                record.result_ref,
                _iso(record.created_at),
            ),
        )
        return record

    def complete_idempotency(
        self, scope: str, key: str, request_hash: ContentHash, result_ref: str
    ) -> IdempotencyRecord:
        existing = self.get_idempotency(scope, key)
        if existing is None:
            raise RecordNotFoundError(f"幂等键 {scope}/{key} 不存在")
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError(f"幂等键 {scope}/{key} 已绑定其他请求")
        if existing.result_ref is not None and existing.result_ref != result_ref:
            raise IdempotencyConflictError(f"幂等键 {scope}/{key} 已绑定其他结果")
        self._connection.execute(
            "UPDATE idempotency_keys SET result_ref = ? WHERE scope = ? AND key = ? AND result_ref IS NULL",
            (result_ref, scope, key),
        )
        return existing.model_copy(update={"result_ref": result_ref})

    def get_idempotency(self, scope: str, key: str) -> IdempotencyRecord | None:
        row = self._connection.execute(
            "SELECT * FROM idempotency_keys WHERE scope = ? AND key = ?", (scope, key)
        ).fetchone()
        if row is None:
            return None
        return IdempotencyRecord(
            scope=row["scope"],
            key=row["key"],
            request_hash=ContentHash(row["request_hash"]),
            result_ref=row["result_ref"],
            created_at=_dt(row["created_at"]),
        )

    def _add_resource(self, table: str, resource: Dataset | Artifact) -> None:
        if table not in {"datasets", "artifacts"}:
            raise ValueError("非法资源表")
        self._connection.execute(
            f"INSERT INTO {table}(id, project_id, name, content_hash, task_id, run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(resource.id),
                str(resource.project_id),
                resource.name,
                resource.content_hash,
                str(resource.task_id) if resource.task_id else None,
                str(resource.run_id) if resource.run_id else None,
                _iso(resource.created_at),
            ),
        )

    @staticmethod
    def _require_cas(result: sqlite3.Cursor, kind: str, identity: object) -> None:
        if result.rowcount != 1:
            raise ConcurrencyConflictError(f"{kind} {identity} 的 CAS 版本或预期状态已过期")

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> Run:
        return Run(
            id=RunId(row["id"]),
            task_id=TaskId(row["task_id"]),
            project_id=ProjectId(row["project_id"]),
            project_snapshot_id=SnapshotId(row["project_snapshot_id"]),
            status=RunStatus(row["status"]),
            phase=RunPhase(row["phase"]),
            wait_reason=WaitReason(row["wait_reason"]) if row["wait_reason"] else None,
            cancel_requested_at=_optional_dt(row["cancel_requested_at"]),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
            completed_at=_optional_dt(row["completed_at"]),
        )
