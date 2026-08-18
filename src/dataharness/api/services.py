"""控制面应用服务。

FastAPI 路由只做参数校验、调用本服务和序列化。SQLite、Workspace、ProjectCorpus 与
编排服务均被封装在这里，保证 HTTP 层没有直接基础设施访问和路径拼接逻辑。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from dataharness.analysis import ProjectFileView, VerificationService
from dataharness.config import Settings
from dataharness.conversations import ConversationAgentService
from dataharness.domain import (
    ArtifactId,
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
from dataharness.orchestration import RunService, SessionService, TaskService
from dataharness.privacy import (
    ModelGateway,
    ModelProviderError,
    PlaceholderStore,
    PrivacyPolicy,
    SecretDetectedError,
)
from dataharness.projects import ProjectCorpus
from dataharness.providers.model import OpenAICompatibleCloudModelProvider
from dataharness.providers.observability import OpenTelemetryAdapter
from dataharness.providers.workspace import LocalWorkspace, normalize_filename
from dataharness.skills import SkillRegistry
from dataharness.storage import (
    EventRecord,
    PrivacyConnectionFactory,
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
        sessions: SessionService | None = None,
        bridge: WorkspaceBridge | None = None,
        verification: VerificationService | None = None,
        observability: OpenTelemetryAdapter | None = None,
        workspace: LocalWorkspace | None = None,
        diagnostics: dict[str, object] | None = None,
        worker_health_file: Path | None = None,
        conversation_agent: ConversationAgentService | None = None,
        skills: SkillRegistry | None = None,
    ) -> None:
        self.store = store
        self.corpus = corpus
        self.tasks = tasks
        self.runs = runs
        self.sessions = sessions or SessionService(store)
        self.bridge = bridge
        self.verification = verification
        self.observability = observability or OpenTelemetryAdapter()
        # 仅供 import_bytes 的临时源文件使用，不把路径暴露到 API DTO。
        self._workspace = workspace
        # API 只读取 Worker 的安全心跳投影，不读取 Worker 日志、prompt 或 Runtime
        # 私密内容。文件不存在时保留 ``not_reported``，避免把“没有监督证据”伪装成健康。
        self._worker_health_file = worker_health_file
        # Chat is deliberately an optional seam so API tests and offline installs can
        # inject a fake Agent without assembling a model provider.
        self.conversation_agent = conversation_agent
        self.skills = skills
        # 诊断抽屉只需要运行状态摘要；这里不保存或返回 API Key、SQLite 内容和
        # Workspace 原始文件清单。Fake Service 未提供时使用明确的未知状态。
        self._diagnostics = diagnostics or {
            "api": "ready",
            "worker": "not_reported",
            "model": {"configured": False},
            "sandbox": {"configured": False},
        }

    @property
    def workspace(self) -> LocalWorkspace | None:
        """返回内部 Workspace 适配器，供本地 worker/E2E 装配复用。"""
        return self._workspace

    @property
    def max_import_bytes(self) -> int:
        """返回已装配 Workspace 的导入上限，供 HTTP 在读取 body 前执行限制。"""
        if self._workspace is None:
            raise ApiError(500, "SERVICE_NOT_CONFIGURED", "文件导入服务未完成装配")
        return self._workspace.max_file_bytes

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
        sessions = SessionService(store)
        provider = OpenAICompatibleCloudModelProvider.from_config(settings.model)
        privacy = PlaceholderStore(
            PrivacyConnectionFactory(
                settings.paths.privacy_root or settings.paths.runtime_data_root / "privacy",
                settings.paths.runtime_db,
            )
        )
        skills = SkillRegistry(settings.paths.skills_root or Path("skills"))
        service = cls(
            store,
            corpus,
            tasks,
            runs,
            sessions=sessions,
            bridge=bridge,
            verification=VerificationService(store, corpus, workspace, bridge),
            workspace=workspace,
            diagnostics={
                "api": "ready",
                "worker": "not_reported",
                "model": {
                    "provider": settings.model.provider,
                    "model": settings.model.model,
                    # 只显示配置状态，绝不返回 TOML 中的秘密值。
                    "configured": bool(settings.model.api_key),
                },
                "sandbox": {
                    "endpoint": settings.sandbox.endpoint,
                    "runtime": settings.sandbox.runtime,
                    "image_digest": settings.sandbox.image_digest or "未锁定",
                    "configured": bool(settings.sandbox.image_digest),
                },
                "paths": {
                    "runtime_data_root": str(settings.paths.runtime_data_root),
                    "disk_free_bytes": shutil.disk_usage(settings.paths.runtime_data_root).free,
                },
            },
            worker_health_file=(
                Path(value) if (value := os.getenv("DATAHARNESS_WORKER_HEALTH_FILE")) else None
            ),
            skills=skills,
        )
        service.conversation_agent = ConversationAgentService(
            store,
            corpus,
            workspace,
            ModelGateway(provider, PrivacyPolicy(privacy)),
            skills=skills,
            analysis_job_launcher=service.launch_analysis_job,
            active_skill_names=settings.skills.active,
        )
        return service

    def list_projects(self) -> tuple[Any, ...]:
        with self.store.unit_of_work() as uow:
            return uow.repo.list_projects()

    def get_project(self, project_id: str):
        with self.store.unit_of_work() as uow:
            return uow.repo.get_project(ProjectId(project_id)).value

    def create_project(self, name: str):
        return self.corpus.create_project(name)

    def archive_project(self, project_id: str):
        """归档项目但保留历史文件、Snapshot 和任务事实，供 WebUI 显式确认后调用。"""
        return self.corpus.archive_project(ProjectId(project_id))

    def list_tasks(self, project_id: str, session_id: str | None = None):
        """返回项目任务的安全生命周期视图，不读取 Workspace 中的原始问题正文。"""
        project = ProjectId(project_id)
        selected_session = SessionId(session_id) if session_id else None
        with self.store.unit_of_work() as uow:
            tasks = uow.repo.list_tasks_for_project(project)
        if selected_session is not None:
            tasks = tuple(item for item in tasks if item.session_id == selected_session)
        return tasks

    def diagnostics(self) -> dict[str, object]:
        """返回可在本地诊断抽屉展示的脱敏配置摘要。"""
        result = dict(self._diagnostics)
        worker = result.get("worker", "not_reported")
        if self._worker_health_file is not None:
            try:
                raw = self._worker_health_file.read_bytes()[:8192]
                payload = json.loads(raw.decode("utf-8"))
                if isinstance(payload, dict) and payload.get("status") in {
                    "STARTING",
                    "IDLE",
                    "RUNNING",
                    "STOPPING",
                    "STOPPED",
                    "FAILED",
                }:
                    # UI 只需要状态文本；PID/时间戳在 status.ps1 中展示，避免 API
                    # 诊断接口逐渐演化成进程控制面。
                    worker = str(payload["status"])
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
                worker = "heartbeat_unavailable"
        result["worker"] = worker
        return result

    def create_snapshot(self, project_id: str):
        """为用户提交问题显式固定当前 Project 视图。"""
        return self.corpus.create_snapshot(ProjectId(project_id))

    def import_file_bytes(self, project_id: str, name: str, data: bytes):
        """把一次 HTTP body 转成短生命周期的普通文件，再交给 ProjectCorpus。

        导入后的事实由 ProjectCorpus/Workspace 接管；临时文件在 finally 中删除，API
        不会把调用方路径传入底层，也不会绕过原有大小、hash、格式和可执行文件校验。
        """
        if self._workspace is None:
            raise ApiError(500, "SERVICE_NOT_CONFIGURED", "文件导入服务未完成装配")
        if not data:
            raise ApiError(400, "EMPTY_FILE", "文件内容不能为空")
        if len(data) > self.max_import_bytes:
            raise ApiError(413, "FILE_TOO_LARGE", "文件超过允许的大小上限")
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

    def import_file_path(self, project_id: str, name: str, source: Path):
        """导入已由 HTTP 层有界落盘的临时文件，避免再把完整请求复制到内存。"""
        if self._workspace is None:
            raise ApiError(500, "SERVICE_NOT_CONFIGURED", "文件导入服务未完成装配")
        try:
            safe_name = normalize_filename(name)
            return self.corpus.import_file(ProjectId(project_id), source, logical_name=safe_name)
        except (OSError, ResourceIntegrityError, UnsafePathError) as error:
            raise ApiError(400, "FILE_IMPORT_FAILED", "文件导入失败，请检查格式和大小") from error

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

    def create_session(self, project_id: str, label: str | None = None):
        return self.sessions.create(ProjectId(project_id), label)

    def list_sessions(self, project_id: str):
        return self.sessions.list_for_project(ProjectId(project_id))

    def list_skills(self):
        if self.skills is None:
            return ()
        return self.skills.discover()

    def list_conversation_messages(self, project_id: str, session_id: str):
        project = ProjectId(project_id)
        session = SessionId(session_id)
        with self.store.unit_of_work() as uow:
            stored = uow.repo.get_session(session)
            if stored.project_id != project:
                raise ApiError(404, "SESSION_NOT_FOUND", "连续对话不存在，或不属于当前项目")
            return uow.repo.list_conversation_messages(project, session)

    async def send_message(
        self, project_id: str, session_id: str, content: str, *, persist: bool = True
    ):
        if self.conversation_agent is None:
            raise ApiError(503, "CHAT_NOT_CONFIGURED", "聊天 Agent 尚未装配模型 Provider")
        try:
            return await self.conversation_agent.respond(
                project_id, session_id, content, persist=persist
            )
        except ApiError:
            raise
        except SecretDetectedError as error:
            raise ApiError(
                400, "SENSITIVE_INPUT_BLOCKED", "消息包含不能发送到模型的凭据"
            ) from error
        except ModelProviderError as error:
            raise ApiError(503, error.code, "模型服务暂不可用，请检查本地配置") from error
        except ValueError as error:
            raise ApiError(400, "INVALID_MESSAGE", "消息不符合当前对话的约束") from error

    def create_task(
        self, project_id: str, snapshot_id: str, session_id: str | None, prompt: str | None = None
    ):
        task = self.tasks.create(
            ProjectId(project_id),
            SessionId(session_id) if session_id else None,
            prompt=prompt,
        )
        try:
            run = self.runs.create(task.id, SnapshotId(snapshot_id))
        except Exception:
            # Task/Run 创建跨两个服务调用，Run 失败时主动取消尚未执行的 Task，避免
            # API 返回错误却在 Runtime 中留下孤立 QUEUED Task。
            self.tasks.cancel(task.id)
            raise
        return self.tasks.get(task.id), run

    def launch_analysis_job(
        self, project_id: ProjectId, session_id: SessionId, prompt: str
    ) -> dict[str, str]:
        """由 Chat Agent 明确调用时创建一个可恢复的 Analysis Job。"""
        snapshot = self.corpus.create_snapshot(project_id)
        task, run = self.create_task(
            str(project_id), str(snapshot.id), str(session_id), prompt.strip()
        )
        return {
            "task_id": str(task.id),
            "run_id": str(run.id),
            "snapshot_id": str(snapshot.id),
            "status": str(task.status),
        }

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
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            # Task 终态不可回退。用户重试改为从受控 PROMPT.json 复制同一目标，创建新的
            # Task/Run；既保留历史事实，也满足终态执行必须生成新 Run 的重试语义。
            if self._workspace is None or task.prompt_ref is None:
                raise ApiError(409, "RETRY_PROMPT_UNAVAILABLE", "原始问题不可读取，无法重试")
            try:
                payload = json.loads(
                    self._workspace.read_task_state(task.project_id, task.id, "PROMPT.json")
                )
                prompt = payload.get("prompt") if isinstance(payload, dict) else None
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
                raise ApiError(
                    409, "RETRY_PROMPT_UNAVAILABLE", "原始问题不可读取，无法重试"
                ) from error
            if not isinstance(prompt, str) or not prompt.strip():
                raise ApiError(409, "RETRY_PROMPT_UNAVAILABLE", "原始问题不可读取，无法重试")
            selected = snapshot_id or (str(runs[-1].project_snapshot_id) if runs else None)
            if selected is None:
                raise ApiError(400, "SNAPSHOT_REQUIRED", "重试必须指定 ProjectSnapshot")
            return self.create_task(
                str(task.project_id),
                selected,
                str(task.session_id) if task.session_id else None,
                prompt,
            )
        if task.status != TaskStatus.ACTIVE:
            raise ApiError(409, "TASK_NOT_RETRYABLE", "当前 Task 状态不可重试")
        selected = snapshot_id or (str(runs[-1].project_snapshot_id) if runs else None)
        if selected is None:
            raise ApiError(400, "SNAPSHOT_REQUIRED", "重试必须指定 ProjectSnapshot")
        return task, self.runs.create(task.id, SnapshotId(selected))

    def task_events(self, task_id: str, *, after_id: int = 0) -> tuple[EventRecord, ...]:
        task_id_value = TaskId(task_id)
        with self.store.unit_of_work() as uow:
            events = list(uow.repo.list_events("task", str(task_id_value)))
            for run in uow.repo.list_runs_for_task(task_id_value):
                events.extend(uow.repo.list_events("run", str(run.id)))
                for step in uow.repo.list_steps_for_run(run.id):
                    events.extend(uow.repo.list_events("step", str(step.id)))
                for finding in uow.repo.list_findings_for_run(run.id):
                    events.extend(uow.repo.list_events("finding", str(finding.id)))
            return tuple(
                sorted((event for event in events if event.id > after_id), key=lambda item: item.id)
            )

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
            answer = None
            if self._workspace is not None:
                try:
                    answer = self._workspace.read_task_state(
                        task.project_id, task.id, "ANSWER.txt"
                    ).decode("utf-8")
                except (FileNotFoundError, OSError, UnicodeDecodeError):
                    answer = None
        return TaskAnswer(
            task_id=str(task.id),
            task_status=str(task.status),
            answer=answer,
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

    def artifact_content(self, project_id: str, artifact_id: str) -> tuple[bytes, str]:
        """只读取指定 Project 下 AVAILABLE 的正式产物，不接受 Workspace 路径。"""
        if self._workspace is None or self.bridge is None:
            raise ApiError(500, "SERVICE_NOT_CONFIGURED", "产物读取服务未完成装配")
        project = ProjectId(project_id)
        with self.store.unit_of_work() as uow:
            artifact = uow.repo.get_artifact(ArtifactId(artifact_id))
            if artifact.project_id != project:
                raise ApiError(404, "ARTIFACT_NOT_FOUND", "项目中不存在该产物")
        record = next(
            (
                item
                for item in self.bridge.available(project)
                if item.resource_id == artifact_id and item.kind.value == "ARTIFACT"
            ),
            None,
        )
        if record is None:
            raise ApiError(404, "ARTIFACT_NOT_AVAILABLE", "产物尚未发布")
        media_type = (
            "image/svg+xml"
            if record.output_name.casefold().endswith(".svg")
            else (
                "image/png"
                if record.output_name.casefold().endswith(".png")
                else "application/json"
            )
        )
        return self._workspace.read_published_resource(record), media_type


def build_default_service(settings: Settings | None = None) -> ApiService:
    """构造默认本地 API 服务，供 CLI 和测试使用。"""
    return ApiService.from_settings(settings or Settings())
