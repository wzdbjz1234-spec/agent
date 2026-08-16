"""Agent 依赖、结构化输出与运行结果模型。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataharness.analysis import AnalysisRuntime
from dataharness.capabilities.memory import MemoryCapability
from dataharness.domain import ResourceRef, RunId, RunPhase, SnapshotId, TaskId
from dataharness.privacy import ModelGateway
from dataharness.sandbox import SandboxLease
from dataharness.skills import SkillRegistry

from .context import ContextCheckpointManager


class AgentFinalOutput(BaseModel):
    """Agent 唯一结构化最终输出；正式资源必须通过稳定引用表达。"""

    model_config = ConfigDict(frozen=True)

    status: Literal["COMPLETED", "WAITING"]
    answer: str = Field(min_length=1, max_length=50_000)
    references: tuple[ResourceRef, ...] = ()
    unresolved_issues: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _normalize_waiting_summary(cls, value: object) -> object:
        """兼容模型把 WAITING 说明写成 ``summary`` 的常见结构变体。

        DeepSeek 等 OpenAI-compatible 模型有时会依据自身训练过的 Agent schema，
        在 ``status=WAITING`` 时返回 ``summary`` 而不是本项目约定的 ``answer``。
        ``answer`` 是统一的用户可见说明字段，因此这里只做窄字段重命名，不接受任意
        字段拼接，也不改变 COMPLETED 输出的严格校验。
        """
        if (
            not isinstance(value, dict)
            or value.get("status") != "WAITING"
            or "answer" in value
        ):
            return value
        summary = value.get("summary")
        if isinstance(summary, str) and summary.strip():
            normalized = dict(value)
            normalized["answer"] = summary
            return normalized
        return value


@dataclass(slots=True)
class AgentDependencies:
    """一次 Agent Run 可见的最小依赖集合。"""

    task_id: TaskId
    run_id: RunId
    snapshot_id: SnapshotId
    analysis: AnalysisRuntime
    skills: SkillRegistry
    context: ContextCheckpointManager
    gateway: ModelGateway
    memory: MemoryCapability | None = None
    # checkpoint 需要把当前 Run 的 lease 事实一并保存，恢复时才能重新 attestation
    # 同一个 Sandbox 或按固定 Snapshot 安全重建。
    sandbox_id: str | None = None
    sandbox_image_digest: str | None = None
    run_lease_epoch: int | None = None
    checkpoint_phase: RunPhase | None = None
    sandbox_lease_getter: Callable[[], SandboxLease | None] | None = None

    def refresh_sandbox_metadata(self) -> None:
        """在 checkpoint 前同步按需创建的 Sandbox lease 事实。"""
        if self.sandbox_lease_getter is None:
            return
        lease = self.sandbox_lease_getter()
        self.sandbox_id = lease.sandbox_id if lease is not None else None
        self.sandbox_image_digest = lease.image_digest if lease is not None else None


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """结构化结果及其最终 checkpoint。"""

    output: AgentFinalOutput
    checkpoint_ref: str
    messages_count: int
