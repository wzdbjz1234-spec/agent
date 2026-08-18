"""Agent 依赖与自然语言运行结果模型。

Agent 的最终回答是面向用户的自然语言，不再使用一个强制的 JSON 输出协议。
工具调用仍由 PydanticAI 根据函数签名校验；只有正式报告和发布产物才需要独立的
结构化模型。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from dataharness.analysis import AnalysisRuntime
from dataharness.capabilities.memory import MemoryCapability
from dataharness.domain import ResourceRef, RunId, RunPhase, SnapshotId, TaskId
from dataharness.privacy import ModelGateway
from dataharness.sandbox import SandboxLease
from dataharness.skills import SkillRegistry

from .context import ContextCheckpointManager


@dataclass(frozen=True, slots=True)
class AgentTextOutput:
    """一次 Agent 的自然语言结果。

    ``answer``、``status`` 和 ``references`` 属性只为迁移期调用方保留兼容读取，
    它们不是模型输出 schema，也不会触发 JSON 生成或结构化重试。
    """

    text: str

    @property
    def answer(self) -> str:
        return self.text

    @property
    def status(self) -> Literal["COMPLETED"]:
        return "COMPLETED"

    @property
    def references(self) -> tuple[ResourceRef, ...]:
        return ()

    @property
    def unresolved_issues(self) -> tuple[str, ...]:
        return ()


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
    """自然语言结果及其最终 checkpoint。"""

    output: AgentTextOutput
    checkpoint_ref: str
    messages_count: int
