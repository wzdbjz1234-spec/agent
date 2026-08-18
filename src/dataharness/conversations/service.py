"""Chat-first Agent facade for local project data.

The service keeps the first useful vertical slice intentionally small: the Agent
can inspect project files and search the local corpus, while an explicit Task/Run
continues to own sandboxed Python/SQL execution.  This makes normal conversation
cheap and natural without weakening the existing execution boundary.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, RunContext

from dataharness.agent.model import gateway_function_model
from dataharness.domain import (
    ConversationMessage,
    FileVersionId,
    MessageId,
    MessageRole,
    ProjectId,
    ProjectStatus,
    SessionId,
    SnapshotId,
)
from dataharness.idgen import IdFactory, UuidIdFactory
from dataharness.privacy import ModelGateway
from dataharness.projects import ProjectCorpus
from dataharness.skills import SkillRegistry
from dataharness.storage import SqliteRuntimeStore
from dataharness.workspace import VirtualWorkspace

AnalysisJobLauncher = Callable[[ProjectId, SessionId, str], dict[str, str]]


@dataclass(slots=True)
class ChatDependencies:
    """The narrow context visible to chat tools."""

    project_id: ProjectId
    session_id: SessionId
    store: SqliteRuntimeStore
    corpus: ProjectCorpus
    workspace: VirtualWorkspace
    user_prompt: str = ""
    snapshot_id: SnapshotId | None = None
    launch_analysis_job: AnalysisJobLauncher | None = None
    analysis_job: dict[str, str] | None = None

    def ensure_snapshot(self) -> SnapshotId:
        """Fix an immutable input view only when a search/inspection needs it.

        A Snapshot is an analysis input boundary, not a Task or Run.  It lets a
        follow-up cite the same file versions while keeping ordinary chat free of
        orchestration records.
        """

        if self.snapshot_id is None:
            self.snapshot_id = self.corpus.create_snapshot(self.project_id).id
        return self.snapshot_id


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, tuple):
        value = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in value
        ]
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _current_files(deps: ChatDependencies) -> tuple[dict[str, object], ...]:
    with deps.store.unit_of_work() as uow:
        uow.repo.get_project(deps.project_id)
        result: list[dict[str, object]] = []
        for file in uow.repo.list_project_files(deps.project_id):
            versions = uow.repo.list_file_versions(file.id)
            current = versions[-1] if versions else None
            result.append(
                {
                    "file_id": str(file.id),
                    "name": file.name,
                    "file_version_id": str(current.id) if current else None,
                    "status": current.status.value if current else None,
                    "media_type": current.media_type if current else None,
                    "byte_size": current.byte_size if current else None,
                }
            )
    return tuple(result)


async def chat_list_project_files(ctx: RunContext[ChatDependencies]) -> str:
    """列出当前项目的逻辑文件和最新版本元数据。"""

    return _json(_current_files(ctx.deps))


async def chat_search_project(
    ctx: RunContext[ChatDependencies], query: str, limit: int = 8
) -> str:
    """在本地索引中检索与问题相关的有界片段。"""

    if not query.strip():
        raise ValueError("检索词不能为空")
    hits = ctx.deps.corpus.search(
        ctx.deps.ensure_snapshot(), query.strip(), limit=max(1, min(limit, 20))
    )
    return _json(hits)


async def chat_inspect_project_file(
    ctx: RunContext[ChatDependencies], file_version_id: str, max_chars: int = 6000
) -> str:
    """读取固定当前输入版本的有界文本内容，不暴露本地路径。"""

    if not 100 <= max_chars <= 20_000:
        raise ValueError("max_chars 必须在 100 到 20000 之间")
    resource = ctx.deps.corpus.open_resource(
        ctx.deps.ensure_snapshot(),
        FileVersionId(file_version_id),
        max_bytes=min(5 * 1024 * 1024, max_chars * 8),
    )
    text = resource.data[:max_chars].decode("utf-8", errors="replace")
    return _json(
        {
            "file_version_id": str(resource.file_version_id),
            "name": resource.name,
            "media_type": resource.media_type,
            "content_hash": str(resource.content_hash),
            "text": text,
        }
    )


async def chat_start_analysis_job(
    ctx: RunContext[ChatDependencies], prompt: str = "", reason: str = ""
) -> str:
    """显式升级为可恢复的 Sandbox 分析作业。

    该工具是普通聊天与长程任务之间唯一的升级点。只有模型判断用户明确要求
    执行计算/生成产物/运行长任务时才应调用；普通问答不应调用它。
    """

    if ctx.deps.analysis_job is not None:
        return _json(ctx.deps.analysis_job)
    launcher = ctx.deps.launch_analysis_job
    if launcher is None:
        return _json(
            {
                "available": False,
                "reason": "当前 API 未装配 Analysis Job launcher，请先使用普通本地检索。",
            }
        )
    job_prompt = prompt.strip() or ctx.deps.user_prompt
    if not job_prompt:
        raise ValueError("分析作业必须包含用户问题")
    result = launcher(ctx.deps.project_id, ctx.deps.session_id, job_prompt)
    ctx.deps.analysis_job = {
        **result,
        "reason": reason.strip()[:500] if reason.strip() else "用户请求需要隔离执行",
    }
    return _json(ctx.deps.analysis_job)


def _build_agent(
    gateway: ModelGateway,
    scope_id: str,
    skills: SkillRegistry | None = None,
    active_skill_names: tuple[str, ...] = (),
) -> Agent[ChatDependencies, str]:
    available_skills = ""
    if skills is not None:
        descriptors = skills.discover()
        if descriptors:
            active = set(active_skill_names)
            available_skills = (
                "\n本机发现的 Skill（只有管理员启用的项目才可用于分析作业）："
                + ", ".join(
                    f"{item.name}{' [active]' if item.name in active else ''}（{item.description}）"
                    for item in descriptors
                )
            )
    return Agent(
        gateway_function_model(gateway, scope_id),
        output_type=str,
        deps_type=ChatDependencies,
        system_prompt=(
            "你是一个面向本地数据项目的通用分析助手。"
            "普通对话直接用自然语言回答，不要输出 JSON，不要声称已经运行了不存在的计算。"
            "需要了解项目时使用 list_project_files、search_project、inspect_project_file；"
            "不要访问主机路径、不要执行未经授权的代码。"
            "只有用户明确要求运行 Python/SQL、生成图表、调用 Wolfram 或执行长时间计算时，"
            "才调用 start_analysis_job；普通问题不要创建作业。调用后仍要用自然语言"
            "说明作业已经提交。"
            "在调用前先给出你已经确认的本地事实。"
            + available_skills
        ),
        tools=[
            chat_list_project_files,
            chat_search_project,
            chat_inspect_project_file,
            chat_start_analysis_job,
        ],
        retries=1,
        name="dataharness-chat-agent",
    )


@dataclass(frozen=True, slots=True)
class ConversationResponse:
    """Chat API 的结果；``text`` 是模型的自然语言，而非模型协议。"""

    user: ConversationMessage
    assistant: ConversationMessage
    snapshot_id: SnapshotId | None = None
    analysis_job: dict[str, str] | None = None


class ConversationAgentService:
    """Run a chat turn without creating a Task/Run."""

    def __init__(
        self,
        store: SqliteRuntimeStore,
        corpus: ProjectCorpus,
        workspace: VirtualWorkspace,
        gateway: ModelGateway,
        *,
        id_factory: IdFactory | None = None,
        skills: SkillRegistry | None = None,
        analysis_job_launcher: AnalysisJobLauncher | None = None,
        active_skill_names: tuple[str, ...] = (),
    ) -> None:
        self._store = store
        self._corpus = corpus
        self._workspace = workspace
        self._gateway = gateway
        self._ids = id_factory or UuidIdFactory()
        self._skills = skills
        self._analysis_job_launcher = analysis_job_launcher
        self._active_skill_names = active_skill_names

    def _history(
        self, project_id: ProjectId, session_id: SessionId
    ) -> tuple[ConversationMessage, ...]:
        with self._store.unit_of_work() as uow:
            project = uow.repo.get_project(project_id).value
            if project.status == ProjectStatus.ARCHIVED:
                raise ValueError("归档项目不能继续发送消息")
            session = uow.repo.get_session(session_id)
            if session.project_id != project_id:
                raise ValueError("Session 不属于当前 Project")
            return uow.repo.list_conversation_messages(project_id, session_id, limit=40)

    async def respond(
        self,
        project_id: str,
        session_id: str,
        content: str,
        *,
        persist: bool = True,
    ) -> ConversationResponse:
        """Answer one turn and optionally persist only the visible messages."""

        text = content.strip()
        if not text or len(text) > 200_000:
            raise ValueError("消息不能为空且不能超过 200000 个字符")
        project = ProjectId(project_id)
        session = SessionId(session_id)
        history = self._history(project, session)
        user = ConversationMessage(
            id=MessageId(self._ids.new("message")),
            project_id=project,
            session_id=session,
            role=MessageRole.USER,
            content=text,
        )
        context = ChatDependencies(
            project,
            session,
            self._store,
            self._corpus,
            self._workspace,
            user_prompt=text,
            launch_analysis_job=self._analysis_job_launcher,
        )
        scope_id = f"conversation_{session}"
        prompt = self._prompt(history, text)
        result = await _build_agent(
            self._gateway, scope_id, self._skills, self._active_skill_names
        ).run(prompt, deps=context)
        answer = result.output.strip()
        if not answer:
            raise RuntimeError("Agent 返回了空回答")
        assistant = ConversationMessage(
            id=MessageId(self._ids.new("message")),
            project_id=project,
            session_id=session,
            role=MessageRole.ASSISTANT,
            content=answer,
        )
        if persist:
            with self._store.unit_of_work() as uow:
                uow.repo.add_conversation_message(user)
                uow.repo.add_conversation_message(assistant)
        return ConversationResponse(user, assistant, context.snapshot_id, context.analysis_job)

    @staticmethod
    def _prompt(history: tuple[ConversationMessage, ...], content: str) -> str:
        if not history:
            return content
        budget = 80_000
        selected: list[ConversationMessage] = []
        used = 0
        for item in reversed(history[-40:]):
            rendered = f"{item.role.value}: {item.content}"
            if selected and used + len(rendered) > budget:
                break
            selected.append(item)
            used += len(rendered)
        transcript = "\n".join(
            f"{item.role.value}: {item.content}" for item in reversed(selected)
        )
        return f"此前对话（仅作上下文，不是新的指令）：\n{transcript}\n\nuser: {content}"
