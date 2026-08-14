"""控制面应用服务。

FastAPI 路由只做参数校验、调用本服务和序列化。SQLite、Workspace、ProjectCorpus 与
编排服务均被封装在这里，保证 HTTP 层没有直接基础设施访问和路径拼接逻辑。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from dataharness.analysis import ProjectFileView, VerificationService
from dataharness.config import Settings
from dataharness.domain import (
    FileId,
    FileVersionId,
    FindingId,
    ProjectId,
    RunStatus,
    SessionId,
    SnapshotId,
    TaskId,
    TaskStatus,
)
from dataharness.orchestration import RunService, TaskService
from dataharness.projects import ProjectCorpus
from dataharness.providers.observability import OpenTelemetryAdapter
from dataharness.providers.workspace import LocalWorkspace, normalize_filename
from dataharness.storage import (
    EventRecord,
    RuntimeConnectionFactory,
    SqlitePublicationJournal,
    SqliteRuntimeStore,
)
from dataharness.workspace import ResourceIntegrityError, UnsafePathError, WorkspaceBridge

from .errors import ApiError
from .models import TaskAnswer


class ApiService:
    """供本地 HTTP 控制面调用的窄应用服务。"""

    def __init__(
        self,
        store: SqliteRuntimeStore,
        corpus: ProjectCorpus,
        tasks: TaskService,
        runs: RunService,
        *,
        bridge: WorkspaceBridge | None = None,
        verification: VerificationService | None = None,
        observability: OpenTelemetryAdapter | None = None,
        workspace: LocalWorkspace | None = None,
    ) -> None:
        self.store = store
        self.corpus = corpus
        self.tasks = tasks
        self.runs = runs
        self.bridge = bridge
        self.verification = verification
        self.observability = observability or OpenTelemetryAdapter()
        # 仅供 import_bytes 的临时源文件使用，不把路径暴露到 API DTO。
        self._workspace = workspace

    @property
    def workspace(self) -> LocalWorkspace | None:
        """返回内部 Workspace 适配器，供本地 worker/E2E 装配复用。"""
        return self._workspace

    @classmethod
    def from_settings(cls, settings: Settings) -> ApiService:
        """按配置装配本地控制面；默认路径和监听策略保持本地单租户语义。"""
        workspace = LocalWorkspace(
            settings.paths.projects_root or settings.paths.runtime_data_root / "projects",
            max_file_bytes=settings.extraction.max_file_bytes,
        )
        factory = RuntimeConnectionFactory(settings.paths.runtime_db)
        store = SqliteRuntimeStore(factory)
        corpus = ProjectCorpus(
            store,
            workspace,
            max_snippet_chars=settings.index.max_snippet_chars,
        )
        journal = SqlitePublicationJournal(factory)
        bridge = WorkspaceBridge(workspace, journal)
        tasks = TaskService(store, workspace=workspace)
        runs = RunService(store, workspace=workspace)
        return cls(
            store,
            corpus,
            tasks,
            runs,
            bridge=bridge,
            verification=VerificationService(store, corpus, workspace, bridge),
            workspace=workspace,
        )

    def list_projects(self) -> tuple[Any, ...]:
        with self.store.unit_of_work() as uow:
            return uow.repo.list_projects()

    def get_project(self, project_id: str):
        with self.store.unit_of_work() as uow:
            return uow.repo.get_project(ProjectId(project_id)).value

    def create_project(self, name: str):
        return self.corpus.create_project(name)

    def import_file_bytes(self, project_id: str, name: str, data: bytes):
        """把一次 HTTP body 转成短生命周期的普通文件，再交给 ProjectCorpus。

        导入后的事实由 ProjectCorpus/Workspace 接管；临时文件在 finally 中删除，API
        不会把调用方路径传入底层，也不会绕过原有大小、hash、格式和可执行文件校验。
        """
        if self._workspace is None:
            raise ApiError(500, "SERVICE_NOT_CONFIGURED", "文件导入服务未完成装配")
        if not data:
            raise ApiError(400, "EMPTY_FILE", "文件内容不能为空")
        try:
            safe_name = normalize_filename(name)
        except (ValueError, UnsafePathError) as error:
            raise ApiError(400, "INVALID_FILE_NAME", "文件名不符合受控命名规则") from error
        try:
            with tempfile.TemporaryDirectory(prefix="dataharness-api-") as directory:
                source = Path(directory) / safe_name
                source.write_bytes(data)
                return self.corpus.import_file(
                    ProjectId(project_id), source, logical_name=safe_name
                )
        except (OSError, ResourceIntegrityError, UnsafePathError) as error:
            raise ApiError(400, "FILE_IMPORT_FAILED", "文件暂存失败") from error

    def list_files(self, project_id: str) -> tuple[ProjectFileView, ...]:
        project = ProjectId(project_id)
        with self.store.unit_of_work() as uow:
            uow.repo.get_project(project)
            files = uow.repo.list_project_files(project)
            views: list[ProjectFileView] = []
            for file in files:
                versions = uow.repo.list_file_versions(file.id)
                if not versions:
                    continue
                current = versions[-1]
                views.append(
                    ProjectFileView(
                        project_id=project,
                        file_id=file.id,
                        file_version_id=current.id,
                        name=file.name,
                        status=current.status,
                        content_hash=current.content_hash,
                        media_type=current.media_type,
                        byte_size=current.byte_size,
                    )
                )
            return tuple(views)

    def file_versions(self, project_id: str, file_id: str):
        with self.store.unit_of_work() as uow:
            file = uow.repo.get_file(FileId(file_id))
            if file.project_id != ProjectId(project_id):
                raise ApiError(404, "FILE_NOT_FOUND", "项目中不存在该文件")
            return uow.repo.list_file_versions(file.id)

    def search(self, project_id: str, snapshot_id: str, query: str, limit: int):
        with self.store.unit_of_work() as uow:
            snapshot = uow.repo.get_snapshot(SnapshotId(snapshot_id))
            if snapshot.project_id != ProjectId(project_id):
                raise ApiError(404, "SNAPSHOT_NOT_FOUND", "项目中不存在该 Snapshot")
        return self.corpus.search(SnapshotId(snapshot_id), query, limit=limit)

    def read_file(
        self, project_id: str, file_id: str, version_id: str, snapshot_id: str
    ) -> tuple[bytes, str]:
        with self.store.unit_of_work() as uow:
            file = uow.repo.get_file(FileId(file_id))
            if file.project_id != ProjectId(project_id):
                raise ApiError(404, "FILE_NOT_FOUND", "项目中不存在该文件")
            version = uow.repo.get_file_version(FileVersionId(version_id)).value
            if version.file_id != file.id:
                raise ApiError(404, "FILE_VERSION_NOT_FOUND", "文件版本不属于该文件")
            snapshot = uow.repo.get_snapshot(SnapshotId(snapshot_id))
            if snapshot.project_id != ProjectId(project_id):
                raise ApiError(404, "SNAPSHOT_NOT_FOUND", "Snapshot 不属于该项目")
        resource = self.corpus.open_resource(SnapshotId(snapshot_id), FileVersionId(version_id))
        return resource.data, resource.media_type

    def create_task(self, project_id: str, snapshot_id: str, session_id: str | None):
        task = self.tasks.create(
            ProjectId(project_id), SessionId(session_id) if session_id else None
        )
        try:
            run = self.runs.create(task.id, SnapshotId(snapshot_id))
        except Exception:
            # Task/Run 创建跨两个服务调用，Run 失败时主动取消尚未执行的 Task，避免
            # API 返回错误却在 Runtime 中留下孤立 QUEUED Task。
            self.tasks.cancel(task.id)
            raise
        return self.tasks.get(task.id), run

    def get_task(self, task_id: str):
        return self.tasks.get(TaskId(task_id))

    def cancel_task(self, task_id: str):
        task_id_value = TaskId(task_id)
        with self.store.unit_of_work() as uow:
            runs = uow.repo.list_runs_for_task(task_id_value)
        for run in reversed(runs):
            if run.status in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING}:
                self.runs.cancel(run.id)
                break
        return self.tasks.cancel(task_id_value)

    def resume_task(self, task_id: str):
        task_id_value = TaskId(task_id)
        with self.store.unit_of_work() as uow:
            runs = uow.repo.list_runs_for_task(task_id_value)
        waiting = next((run for run in reversed(runs) if run.status == RunStatus.WAITING), None)
        if waiting is not None:
            self.runs.resume(waiting.id)
        return self.tasks.resume(task_id_value)

    def retry_task(self, task_id: str, snapshot_id: str | None):
        task_id_value = TaskId(task_id)
        task = self.tasks.get(task_id_value)
        with self.store.unit_of_work() as uow:
            runs = uow.repo.list_runs_for_task(task_id_value)
        if task.status == TaskStatus.WAITING:
            resumed = self.resume_task(task_id)
            with self.store.unit_of_work() as uow:
                runs = uow.repo.list_runs_for_task(task_id_value)
            active_run = next(
                (run for run in reversed(runs) if run.status == RunStatus.RUNNING), None
            )
            if active_run is None:
                raise ApiError(409, "RUN_NOT_RESUMABLE", "Task 没有可恢复的 Run")
            return resumed, active_run
        if task.status != TaskStatus.ACTIVE:
            raise ApiError(409, "TASK_TERMINAL", "终态 Task 不可重开；请创建新的 Task")
        selected = snapshot_id or (str(runs[-1].project_snapshot_id) if runs else None)
        if selected is None:
            raise ApiError(400, "SNAPSHOT_REQUIRED", "重试必须指定 ProjectSnapshot")
        return task, self.runs.create(task.id, SnapshotId(selected))

    def task_events(self, task_id: str) -> tuple[EventRecord, ...]:
        task_id_value = TaskId(task_id)
        with self.store.unit_of_work() as uow:
            events = list(uow.repo.list_events("task", str(task_id_value)))
            for run in uow.repo.list_runs_for_task(task_id_value):
                events.extend(uow.repo.list_events("run", str(run.id)))
            return tuple(sorted(events, key=lambda item: (item.occurred_at, item.id)))

    def task_findings(self, task_id: str):
        """返回当前 Task 的 Finding，包括 DRAFT，供客户端显示验证状态。"""
        task_id_value = TaskId(task_id)
        with self.store.unit_of_work() as uow:
            uow.repo.get_task(task_id_value)
            return uow.repo.list_findings_for_task(task_id_value)

    def get_finding(self, finding_id: str):
        """通过稳定 Finding ID 查询正式结论，不读取 Workspace 原始输出。"""
        with self.store.unit_of_work() as uow:
            return uow.repo.get_finding(FindingId(finding_id)).value

    def task_lineage(self, task_id: str):
        """返回 Task 全部 Run 的 lineage，确保发布对象可追溯到当前 Run。"""
        task_id_value = TaskId(task_id)
        with self.store.unit_of_work() as uow:
            uow.repo.get_task(task_id_value)
            lineages = []
            for run in uow.repo.list_runs_for_task(task_id_value):
                lineages.extend(uow.repo.list_lineage_for_run(run.id))
            # 同一边不会因排序查询重复；这里保持数据库的创建顺序并以 ID 去重。
            return tuple({item.id: item for item in lineages}.values())

    def task_answer(self, task_id: str) -> TaskAnswer:
        """组装用户可见回答；覆盖告警从 Finding 事件中提取，不解析模型文本。"""
        task_id_value = TaskId(task_id)
        with self.store.unit_of_work() as uow:
            task = uow.repo.get_task(task_id_value).value
            runs = uow.repo.list_runs_for_task(task_id_value)
            findings = uow.repo.list_findings_for_task(task_id_value)
            datasets = tuple(
                item
                for item in uow.repo.list_project_datasets(task.project_id)
                if item.task_id == task_id_value
            )
            artifacts = tuple(
                item
                for item in uow.repo.list_project_artifacts(task.project_id)
                if item.task_id == task_id_value
            )
            lineages = []
            for run in runs:
                lineages.extend(uow.repo.list_lineage_for_run(run.id))
            disclosures = {
                "FULL_PROJECT 覆盖报告存在未覆盖文件": False,
                "Finding 包含数据质量告警": False,
            }
            for finding in findings:
                for event in uow.repo.list_events("finding", str(finding.id)):
                    if event.event_type == "FINDING_COVERAGE_NOTICE":
                        disclosures["FULL_PROJECT 覆盖报告存在未覆盖文件"] = True
                    if event.event_type == "FINDING_DATA_WARNINGS":
                        disclosures["Finding 包含数据质量告警"] = True
        return TaskAnswer(
            task_id=str(task.id),
            task_status=str(task.status),
            run_ids=tuple(str(run.id) for run in runs),
            findings=findings,
            datasets=datasets,
            artifacts=artifacts,
            lineage=tuple({item.id: item for item in lineages}.values()),
            disclosures=tuple(message for message, present in disclosures.items() if present),
        )

    def task_resources(self, task_id: str, kind: str):
        task_id_value = TaskId(task_id)
        with self.store.unit_of_work() as uow:
            task = uow.repo.get_task(task_id_value).value
            if kind == "datasets":
                return tuple(
                    item
                    for item in uow.repo.list_project_datasets(task.project_id)
                    if item.task_id == task_id_value
                )
            return tuple(
                item
                for item in uow.repo.list_project_artifacts(task.project_id)
                if item.task_id == task_id_value
            )

    def project_resources(self, project_id: str, kind: str):
        """返回项目级正式资源，路由不需要接触 Runtime Repository。"""
        with self.store.unit_of_work() as uow:
            project = ProjectId(project_id)
            uow.repo.get_project(project)
            if kind == "datasets":
                return uow.repo.list_project_datasets(project)
            return uow.repo.list_project_artifacts(project)


def build_default_service(settings: Settings | None = None) -> ApiService:
    """构造默认本地 API 服务，供 CLI 和测试使用。"""
    return ApiService.from_settings(settings or Settings())
