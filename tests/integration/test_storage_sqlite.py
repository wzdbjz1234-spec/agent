"""SQLite migration、并发 claim、lease fencing 与物理隔离集成测试。"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dataharness.domain import (
    FileId,
    FileVersionId,
    Project,
    ProjectFile,
    ProjectFileVersion,
    ProjectId,
    ProjectSnapshot,
    Run,
    RunId,
    SnapshotId,
    Task,
    TaskId,
)
from dataharness.storage import (
    LeaseLostError,
    Migration,
    MigrationError,
    PrivacyConnectionFactory,
    RuntimeConnectionFactory,
    SqliteRuntimeStore,
    current_schema_version,
    migrate,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _queued_store(tmp_path: Path) -> tuple[SqliteRuntimeStore, Run]:
    store = SqliteRuntimeStore(RuntimeConnectionFactory(tmp_path / "runtime.db"))
    project = Project(id=ProjectId("p"), name="p", created_at=T0)
    snapshot = ProjectSnapshot(id=SnapshotId("s"), project_id=project.id, created_at=T0)
    task = Task(id=TaskId("t"), project_id=project.id, created_at=T0, updated_at=T0)
    run = Run(
        id=RunId("r"),
        task_id=task.id,
        project_id=project.id,
        project_snapshot_id=snapshot.id,
        created_at=T0,
        updated_at=T0,
    )
    with store.unit_of_work() as uow:
        uow.repo.add_project(project)
        uow.repo.add_snapshot(snapshot)
        uow.repo.add_task(task)
        uow.repo.add_run(run)
    return store, run


def test_empty_database_upgrade_replay_and_wal(tmp_path: Path) -> None:
    factory = RuntimeConnectionFactory(tmp_path / "runtime.db")
    first = factory.connect()
    # Phase 11 增加 prompt 引用与 Session Project 作用域，schema 版本推进到 5。
    assert current_schema_version(first) == 5
    assert first.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert first.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    first.close()
    second = factory.connect()
    assert current_schema_version(second) == 5
    second.close()


def test_progressive_custom_migration_and_failed_version_roll_back() -> None:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    v1 = Migration(1, "base", "CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT);")
    v2 = Migration(2, "add_value", "INSERT INTO sample(id, value) VALUES (1, 'kept');")
    assert migrate(connection, (v1,)) == 1
    assert migrate(connection, (v1, v2)) == 2
    broken = Migration(
        3,
        "broken",
        "CREATE TABLE half_applied(id INTEGER); INSERT INTO missing_table VALUES (1);",
    )
    with pytest.raises(MigrationError):
        migrate(connection, (v1, v2, broken))
    assert current_schema_version(connection) == 2
    assert (
        connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'half_applied'").fetchone()
        is None
    )
    assert connection.execute("SELECT value FROM sample WHERE id = 1").fetchone()[0] == "kept"


def test_two_workers_cannot_claim_same_effective_lease(tmp_path: Path) -> None:
    store, run = _queued_store(tmp_path)

    def claim(owner: str):
        return store.claim_next_run(owner, T0, timedelta(seconds=30))

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ("worker-a", "worker-b")))
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0].run.id == run.id
    assert winners[0].lease.epoch == 1


def test_expired_lease_is_reclaimed_and_old_epoch_cannot_commit(tmp_path: Path) -> None:
    store, _ = _queued_store(tmp_path)
    first = store.claim_next_run("worker-old", T0, timedelta(seconds=10))
    assert first is not None
    second = store.claim_next_run("worker-new", T0 + timedelta(seconds=11), timedelta(seconds=10))
    assert second is not None
    assert second.recovered is True
    assert second.lease.epoch == first.lease.epoch + 1

    stale_update = first.run.advance_phase(first.run.phase.REASONING, T0 + timedelta(seconds=12))
    with store.unit_of_work() as uow, pytest.raises(LeaseLostError):
        uow.repo.save_run(
            stale_update,
            second.version,
            "RUN_PHASE_ADVANCED",
            lease=first.lease,
            lease_checked_at=T0 + timedelta(seconds=12),
        )


def test_heartbeat_rejects_expired_epoch(tmp_path: Path) -> None:
    store, _ = _queued_store(tmp_path)
    claim = store.claim_next_run("worker", T0, timedelta(seconds=10))
    assert claim is not None
    with pytest.raises(LeaseLostError):
        store.heartbeat(claim.lease, T0 + timedelta(seconds=10), timedelta(seconds=5))


def test_snapshot_and_file_version_are_database_immutable(tmp_path: Path) -> None:
    factory = RuntimeConnectionFactory(tmp_path / "runtime.db")
    store = SqliteRuntimeStore(factory)
    project = Project(id=ProjectId("p"), name="p", created_at=T0)
    file = ProjectFile(id=FileId("f"), project_id=project.id, name="x", created_at=T0)
    version = ProjectFileVersion(
        id=FileVersionId("v"),
        file_id=file.id,
        project_id=project.id,
        version_number=1,
        created_at=T0,
    ).mark_failed(T0)
    snapshot = ProjectSnapshot(id=SnapshotId("s"), project_id=project.id, created_at=T0)
    with store.unit_of_work() as uow:
        uow.repo.add_project(project)
        uow.repo.add_file(file)
        importing = version.model_copy(update={"status": "IMPORTING", "finalized_at": None})
        uow.repo.add_file_version(importing)
        uow.repo.finalize_file_version(version, 0)
        uow.repo.add_snapshot(snapshot)
    connection = factory.connect()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE project_snapshots SET index_version = 'changed' WHERE id = 's'")
    with pytest.raises(sqlite3.IntegrityError, match="finalized"):
        connection.execute("UPDATE project_file_versions SET media_type = 'changed' WHERE id = 'v'")
    connection.close()


def test_runtime_and_privacy_connection_factories_are_physically_separate(tmp_path: Path) -> None:
    runtime_path = tmp_path / "runtime" / "runtime.db"
    runtime = RuntimeConnectionFactory(runtime_path)
    privacy = PrivacyConnectionFactory(tmp_path / "privacy", runtime_path)
    runtime_connection = runtime.connect()
    privacy_connection = privacy.connect(TaskId("task-1"))
    assert Path(runtime_connection.execute("PRAGMA database_list").fetchone()[2]) == runtime.path
    assert Path(
        privacy_connection.execute("PRAGMA database_list").fetchone()[2]
    ) == privacy.path_for(TaskId("task-1"))
    assert privacy.path_for(TaskId("task-1")) != runtime.path
    runtime_connection.close()
    privacy_connection.close()


def test_runtime_schema_has_no_blob_secret_or_pii_mapping_columns(tmp_path: Path) -> None:
    """以 schema 负向断言证明 Runtime DB 没有大文件、凭据或 PII 映射落点。"""
    connection = RuntimeConnectionFactory(tmp_path / "runtime.db").connect()
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    columns = [
        column
        for table in tables
        for column in connection.execute(f"PRAGMA table_info('{table['name']}')").fetchall()
    ]
    assert all(str(column["type"]).upper() != "BLOB" for column in columns)
    forbidden = ("secret", "password", "api_key", "pii_mapping", "model_payload")
    assert all(
        not any(word in str(column["name"]).casefold() for word in forbidden) for column in columns
    )
    connection.close()
