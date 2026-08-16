"""Task/Run 生命周期服务。

服务只负责把用户动作转换成领域对象和 Runtime SQLite 事务；worker 的领取、恢复、
外部副作用与重试由 :mod:`dataharness.providers.durable` 负责，避免两套事实源分叉。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from dataharness.domain import (
    ProjectId,
    ProjectStatus,
    Run,
    RunId,
    RunStatus,
    Session,
    SessionId,
    SnapshotId,
    Task,
    TaskId,
    TaskStatus,
    WaitReason,
    compute_content_hash,
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

    def create(
        self,
        project_id: ProjectId,
        session_id: SessionId | None = None,
        *,
        prompt: str | None = None,
    ) -> Task:
        """创建 QUEUED Task，并持久化不可变的受控用户问题载荷。

        Runtime 只接收 ``prompt_ref`` 与 ``prompt_hash``；真正的文本写入 Task state
        目录，Worker 恢复时重新校验哈希后才交给 ModelGateway。旧的内部调用可以不传
        prompt，但用户可见的 Phase 11 提交路径应始终传入非空问题。
        """
        if prompt is not None:
            prompt = prompt.strip()
            if not prompt:
                raise ValueError("用户问题不能为空")
            if len(prompt) > 100_000:
                raise ValueError("用户问题超过 100000 字符上限")
        now = self._clock()
        task_id = TaskId(self._ids.new("task"))
        prompt_ref = None
        prompt_hash = None
        prompt_payload: bytes | None = None
        if prompt is not None:
            prompt_ref = f"task:{task_id}:state:PROMPT.json"
            prompt_payload = json.dumps(
                {
                    "schema_version": 1,
                    "task_id": str(task_id),
                    "project_id": str(project_id),
                    "session_id": str(session_id) if session_id else None,
                    "prompt": prompt,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            prompt_hash = compute_content_hash(prompt_payload)
        task = Task(
            id=task_id,
            project_id=project_id,
            session_id=session_id,
            prompt_ref=prompt_ref,
            prompt_hash=prompt_hash,
            created_at=now,
            updated_at=now,
        )
        # 先只读校验 Project/Session 归属，避免参数错误时创建孤立 Workspace 目录。
        with self._store.unit_of_work() as uow:
            project = uow.repo.get_project(project_id).value
            if project.status != ProjectStatus.ACTIVE:
                raise ValueError("归档项目不能创建新 Task")
            if session_id is not None:
                session = uow.repo.get_session(session_id)
                if session.project_id is not None and session.project_id != project_id:
                    raise ValueError("Session 只能绑定一个 Project")
        if self._workspace is not None:
            # 先准备 Workspace 载荷，再提交 Runtime 事实；磁盘失败不会留下一个无法
            # 恢复 prompt 的 QUEUED Task。若后续 DB 事务失败，遗留目录只是可重建的
            # Workspace 派生物，不会成为控制面事实。
            self._workspace.create_task(project_id, task.id)
            if prompt_payload is not None:
                # PROMPT.json 由 Workspace 自己执行不可变与路径校验；这里不把文件路径
                # 写回 Runtime，worker 只使用固定逻辑引用。
                self._workspace.write_task_state(project_id, task.id, "PROMPT.json", prompt_payload)
        with self._store.unit_of_work() as uow:
            project = uow.repo.get_project(project_id).value
            if project.status != ProjectStatus.ACTIVE:
                raise ValueError("归档项目不能创建新 Task")
            uow.repo.add_task(task)
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
        workspace: VirtualWorkspace | None = None,
    ) -> None:
        self._store = store
        self._ids = id_factory or UuidIdFactory()
        self._clock = clock
        self._workspace = workspace

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
            if self._workspace is not None:
                self._workspace.cleanup_staging(result.project_id, result.task_id)
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


class SessionService:
    """Project-scoped Session 的创建和查询服务。"""

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

    def create(self, project_id: ProjectId, label: str | None = None) -> Session:
        """创建固定属于一个 Project 的 Session。"""
        now = self._clock()
        session = Session(
            id=SessionId(self._ids.new("session")),
            project_id=project_id,
            label=label.strip() if label and label.strip() else None,
            created_at=now,
        )
        with self._store.unit_of_work() as uow:
            project = uow.repo.get_project(project_id).value
            if project.status != ProjectStatus.ACTIVE:
                raise ValueError("归档项目不能创建新 Session")
            uow.repo.add_session(session)
        return session

    def get(self, session_id: SessionId) -> Session:
        with self._store.unit_of_work() as uow:
            return uow.repo.get_session(session_id)

    def list_for_project(self, project_id: ProjectId) -> tuple[Session, ...]:
        with self._store.unit_of_work() as uow:
            uow.repo.get_project(project_id)
            return uow.repo.list_sessions(project_id)
