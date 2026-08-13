"""Task/Run 生命周期服务。

服务只负责把用户动作转换成领域对象和 Runtime SQLite 事务；worker 的领取、恢复、
外部副作用与重试由 :mod:`dataharness.providers.durable` 负责，避免两套事实源分叉。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from dataharness.domain import (
    ProjectId,
    Run,
    RunId,
    RunStatus,
    SessionId,
    SnapshotId,
    Task,
    TaskId,
    TaskStatus,
    WaitReason,
    utcnow,
)
from dataharness.idgen import IdFactory, UuidIdFactory
from dataharness.storage import SqliteRuntimeStore
from dataharness.workspace import VirtualWorkspace


class TaskService:
    """创建和推进一个 Project 绑定的 Task。"""

    def __init__(
        self,
        store: SqliteRuntimeStore,
        *,
        id_factory: IdFactory | None = None,
        clock: Callable[[], datetime] = utcnow,
        workspace: VirtualWorkspace | None = None,
    ) -> None:
        self._store = store
        self._ids = id_factory or UuidIdFactory()
        self._clock = clock
        self._workspace = workspace

    def create(self, project_id: ProjectId, session_id: SessionId | None = None) -> Task:
        """创建 QUEUED Task，并建立隔离的 Task working/staging/state 命名空间。"""
        now = self._clock()
        task = Task(
            id=TaskId(self._ids.new("task")),
            project_id=project_id,
            session_id=session_id,
            created_at=now,
            updated_at=now,
        )
        with self._store.unit_of_work() as uow:
            uow.repo.get_project(project_id)
            uow.repo.add_task(task)
        if self._workspace is not None:
            self._workspace.create_task(project_id, task.id)
        return task

    def get(self, task_id: TaskId) -> Task:
        with self._store.unit_of_work() as uow:
            return uow.repo.get_task(task_id).value

    def wait(self, task_id: TaskId, reason: WaitReason) -> Task:
        return self._transition(task_id, lambda task, now: task.wait(reason, now), "TASK_WAITING")

    def resume(self, task_id: TaskId) -> Task:
        return self._transition(task_id, lambda task, now: task.resume(now), "TASK_RESUMED")

    def cancel(self, task_id: TaskId) -> Task:
        """幂等取消 Task；已运行的 Run 仍需由 RunService/worker 清理 Sandbox。"""
        return self._transition(task_id, lambda task, now: task.cancel(now), "TASK_CANCELLED")

    def _transition(
        self,
        task_id: TaskId,
        transition: Callable[[Task, datetime], Task],
        event_type: str,
    ) -> Task:
        now = self._clock()
        with self._store.unit_of_work() as uow:
            stored = uow.repo.get_task(task_id)
            return uow.repo.save_task(
                transition(stored.value, now), stored.version, event_type
            ).value


class RunService:
    """创建固定 Snapshot 的 Run，并提供取消/等待/恢复入口。"""

    def __init__(
        self,
        store: SqliteRuntimeStore,
        *,
        id_factory: IdFactory | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._store = store
        self._ids = id_factory or UuidIdFactory()
        self._clock = clock

    def create(self, task_id: TaskId, project_snapshot_id: SnapshotId) -> Run:
        """创建绑定 Task Project 与指定 Snapshot 的新 Run。

        Snapshot 必须显式传入，避免恢复或重试时意外读取 Project 的最新文件版本。
        """
        now = self._clock()
        with self._store.unit_of_work(immediate=True) as uow:
            task = uow.repo.get_task(task_id).value
            snapshot = uow.repo.get_snapshot(project_snapshot_id)
            if snapshot.project_id != task.project_id:
                raise ValueError("Run 的 ProjectSnapshot 不属于 Task 的 Project")
            if task.status == TaskStatus.QUEUED:
                started = uow.repo.save_task(task.start(now), 0, "TASK_STARTED")
                task = started.value
            elif task.status != TaskStatus.ACTIVE:
                raise ValueError("只有 QUEUED/ACTIVE Task 可以创建 Run")
            run = Run(
                id=RunId(self._ids.new("run")),
                task_id=task.id,
                project_id=task.project_id,
                project_snapshot_id=project_snapshot_id,
                created_at=now,
                updated_at=now,
            )
            uow.repo.add_run(run)
            return run

    def get(self, run_id: RunId) -> Run:
        with self._store.unit_of_work() as uow:
            return uow.repo.get_run(run_id).value

    def cancel(self, run_id: RunId) -> Run:
        """记录幂等取消意图；运行中的外部副作用由 Executor 负责停止。"""
        result = self._store.request_cancel(run_id, self._clock())
        if result.status == RunStatus.CANCELLED:
            with self._store.unit_of_work(immediate=True) as uow:
                stored = uow.repo.get_task(result.task_id)
                if stored.value.status in (TaskStatus.QUEUED, TaskStatus.ACTIVE):
                    uow.repo.save_task(
                        stored.value.cancel(self._clock()), stored.version, "TASK_CANCELLED"
                    )
        return result

    def resume(self, run_id: RunId) -> Run:
        """恢复 WAITING Run；恢复仍使用创建时的 Snapshot。"""
        return self._store.resume_waiting(run_id, self._clock())

    def wait(self, run_id: RunId, reason: WaitReason) -> Run:
        now = self._clock()
        with self._store.unit_of_work(immediate=True) as uow:
            stored = uow.repo.get_run(run_id)
            return uow.repo.save_run(
                stored.value.wait(reason, now), stored.version, "RUN_WAITING"
            ).value

    def latest_checkpoint(self, run_id: RunId):
        with self._store.unit_of_work() as uow:
            return uow.repo.latest_checkpoint(run_id)
