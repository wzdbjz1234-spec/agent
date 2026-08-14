"""Agent 依赖、结构化输出与运行结果模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dataharness.analysis import AnalysisRuntime
from dataharness.capabilities.memory import MemoryCapability
from dataharness.domain import ResourceRef, RunId, SnapshotId, TaskId
from dataharness.privacy import ModelGateway
from dataharness.skills import SkillRegistry

from .context import ContextCheckpointManager


class AgentFinalOutput(BaseModel):
    """Agent 唯一结构化最终输出；正式资源必须通过稳定引用表达。"""

    model_config = ConfigDict(frozen=True)

    status: Literal["COMPLETED", "WAITING"]
    answer: str = Field(min_length=1, max_length=50_000)
    references: tuple[ResourceRef, ...] = ()
    unresolved_issues: tuple[str, ...] = ()


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


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """结构化结果及其最终 checkpoint。"""

    output: AgentFinalOutput
    checkpoint_ref: str
    messages_count: int
