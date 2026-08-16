"""Agent 目标、计划、进度与 PydanticAI 消息的可恢复上下文。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from dataharness.domain import (
    FileVersionId,
    ProjectId,
    ResourceRef,
    RunId,
    RunPhase,
    SnapshotId,
    TaskId,
    compute_content_hash,
    utcnow,
)
from dataharness.privacy import ModelGateway
from dataharness.storage import CheckpointMetadata, SqliteRuntimeStore
from dataharness.workspace import VirtualWorkspace


class ContextCheckpointError(RuntimeError):
    """上下文 checkpoint 无法保存或恢复。"""


class CheckpointCorruptError(ContextCheckpointError):
    """checkpoint 文件哈希、序列或 JSON 内容不一致。"""


class AgentContextState(BaseModel):
    """可恢复的结构化上下文；事实通过稳定领域引用表达。"""

    model_config = ConfigDict(frozen=True)

    goal: str = Field(min_length=1, max_length=20_000)
    plan: tuple[str, ...] = ()
    progress: tuple[str, ...] = ()
    project_snapshot_id: SnapshotId
    file_version_ids: tuple[FileVersionId, ...] = ()
    domain_refs: tuple[ResourceRef, ...] = ()
    unresolved_issues: tuple[str, ...] = ()
    summary: str | None = None


class AgentCheckpointEnvelope(BaseModel):
    """checkpoint 文件的版本化载荷；summary 不是事实来源。"""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    state: AgentContextState
    messages_json: str


@dataclass(frozen=True, slots=True)
class RestoredContext:
    """恢复后的元数据、结构化状态和 PydanticAI 消息。"""

    metadata: CheckpointMetadata
    state: AgentContextState
    messages: tuple[ModelMessage, ...]


class ContextCheckpointManager:
    """把上下文载荷写入 Workspace，并把可审计元数据写入 Runtime SQLite。"""

    def __init__(
        self,
        workspace: VirtualWorkspace,
        store: SqliteRuntimeStore,
        gateway: ModelGateway,
        *,
        project_id: ProjectId,
        task_id: TaskId,
        run_id: RunId,
        snapshot_id: SnapshotId,
    ) -> None:
        self._workspace = workspace
        self._store = store
        self._gateway = gateway
        self.project_id = project_id
        self.task_id = task_id
        self.run_id = run_id
        self.snapshot_id = snapshot_id

    def _next_sequence(self) -> int:
        with self._store.unit_of_work() as uow:
            latest = uow.repo.latest_checkpoint(self.run_id)
        return 1 if latest is None else latest.sequence + 1

    def _safe_envelope(self, state: AgentContextState, messages: tuple[ModelMessage, ...]) -> bytes:
        if state.project_snapshot_id != self.snapshot_id:
            raise ContextCheckpointError("上下文 Snapshot 与当前 Run 不一致")
        messages_json = ModelMessagesTypeAdapter.dump_json(
            list(messages), ensure_ascii=False
        ).decode("utf-8")
        raw = AgentCheckpointEnvelope(
            state=state,
            messages_json=messages_json,
        ).model_dump_json(indent=None)
        safe = self._gateway.sanitize_compaction(self.task_id, raw).cloud_text
        try:
            envelope = AgentCheckpointEnvelope.model_validate_json(safe)
        except ValueError as error:
            raise ContextCheckpointError("脱敏后的 checkpoint 不是有效 JSON") from error
        return envelope.model_dump_json(indent=None, ensure_ascii=False).encode("utf-8")

    def save(
        self,
        state: AgentContextState,
        messages: tuple[ModelMessage, ...],
        *,
        created_at: datetime | None = None,
        phase: RunPhase | None = None,
        run_lease_epoch: int | None = None,
        sandbox_id: str | None = None,
        sandbox_image_digest: str | None = None,
    ) -> CheckpointMetadata:
        """原子保存带哈希的 checkpoint，并记录 Runtime 元数据。"""
        sequence = self._next_sequence()
        payload = self._safe_envelope(state, messages)
        resource = self._workspace.write_task_checkpoint(
            self.project_id, self.task_id, sequence, payload
        )
        metadata = CheckpointMetadata(
            id=f"checkpoint:{self.run_id}:{sequence}",
            run_id=self.run_id,
            sequence=sequence,
            checkpoint_ref=f"task:{self.task_id}:checkpoint:{sequence}",
            content_hash=resource.content_hash,
            created_at=created_at or utcnow(),
            project_snapshot_id=self.snapshot_id,
            sandbox_id=sandbox_id,
            sandbox_image_digest=sandbox_image_digest,
            run_lease_epoch=run_lease_epoch,
            phase=phase,
        )
        with self._store.unit_of_work(immediate=True) as uow:
            uow.repo.add_checkpoint(metadata)
        return metadata

    def load_latest(self) -> RestoredContext | None:
        """只恢复最新已登记 checkpoint，并重新验证 Snapshot、哈希和消息格式。"""
        with self._store.unit_of_work() as uow:
            metadata = uow.repo.latest_checkpoint(self.run_id)
        if metadata is None:
            return None
        if metadata.project_snapshot_id != self.snapshot_id:
            raise CheckpointCorruptError("checkpoint 引用了错误的 ProjectSnapshot")
        try:
            payload = self._workspace.read_task_checkpoint(
                self.project_id, self.task_id, metadata.sequence
            )
        except (FileNotFoundError, OSError) as error:
            raise CheckpointCorruptError("checkpoint 文件不存在") from error
        if compute_content_hash(payload) != metadata.content_hash:
            raise CheckpointCorruptError("checkpoint 文件哈希不匹配")
        try:
            envelope = AgentCheckpointEnvelope.model_validate_json(payload)
            messages = tuple(ModelMessagesTypeAdapter.validate_json(envelope.messages_json))
        except ValueError as error:
            raise CheckpointCorruptError("checkpoint 内容不是有效的上下文载荷") from error
        if envelope.state.project_snapshot_id != self.snapshot_id:
            raise CheckpointCorruptError("checkpoint 状态引用了错误的 ProjectSnapshot")
        return RestoredContext(metadata=metadata, state=envelope.state, messages=messages)


@dataclass(frozen=True, slots=True)
class CompactedContext:
    """压缩后的消息与结构化上下文。"""

    state: AgentContextState
    messages: tuple[ModelMessage, ...]
    checkpoint: CheckpointMetadata


class ContextCompactor:
    """通过 ModelGateway 生成摘要，再保留领域引用并写入新 checkpoint。"""

    def __init__(
        self,
        manager: ContextCheckpointManager,
        gateway: ModelGateway,
        *,
        keep_messages: int = 6,
    ) -> None:
        if keep_messages <= 0:
            raise ValueError("keep_messages 必须为正数")
        self._manager = manager
        self._gateway = gateway
        self._keep_messages = keep_messages

    def compact(
        self,
        state: AgentContextState,
        messages: tuple[ModelMessage, ...],
        *,
        created_at: datetime | None = None,
        phase: RunPhase | None = None,
        run_lease_epoch: int | None = None,
        sandbox_id: str | None = None,
        sandbox_image_digest: str | None = None,
    ) -> CompactedContext:
        """调用唯一模型边界生成摘要；summary 不会被转换成事实引用。"""
        messages_json = ModelMessagesTypeAdapter.dump_json(
            list(messages), ensure_ascii=False
        ).decode("utf-8")
        prompt = json.dumps(
            {
                "instruction": (
                    "请压缩对话上下文，只总结目标、计划、进度与未解决问题。不要新增事实。"
                ),
                "state": state.model_dump(mode="json"),
                "messages": messages_json,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        summary = self._gateway.complete(self._manager.task_id, prompt).cloud_text
        safe_summary = self._gateway.sanitize_compaction(self._manager.task_id, summary).cloud_text
        compacted_state = state.model_copy(update={"summary": safe_summary})
        compacted_messages = messages[-self._keep_messages :]
        checkpoint = self._manager.save(
            compacted_state,
            compacted_messages,
            created_at=created_at,
            phase=phase,
            run_lease_epoch=run_lease_epoch,
            sandbox_id=sandbox_id,
            sandbox_image_digest=sandbox_image_digest,
        )
        return CompactedContext(
            state=compacted_state,
            messages=compacted_messages,
            checkpoint=checkpoint,
        )
