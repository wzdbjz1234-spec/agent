"""Agent Run 驱动、checkpoint 保存与预算耗尽处理。"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits

from .context import AgentContextState, ContextCompactor
from .models import AgentDependencies, AgentRunResult, AgentTextOutput


class AgentBudgetExhausted(RuntimeError):
    """Agent 达到请求、工具或 Token 预算，应由 orchestration 转为 WAITING。"""


class AgentRunner:
    """负责恢复历史消息、运行单一 Agent 并保存可恢复上下文。"""

    def __init__(
        self,
        agent: Agent[AgentDependencies, str],
        *,
        compactor: ContextCompactor | None = None,
        context_budget_chars: int = 120_000,
    ) -> None:
        if context_budget_chars <= 0:
            raise ValueError("context_budget_chars 必须为正数")
        self._agent = agent
        self._compactor = compactor
        self._context_budget_chars = context_budget_chars

    async def run(
        self,
        prompt: str,
        deps: AgentDependencies,
        *,
        usage_limits: UsageLimits | None = None,
    ) -> AgentRunResult:
        """恢复最新 checkpoint 后执行，并在成功或等待时写入新 checkpoint。"""
        restored = deps.context.load_latest()
        history: tuple[ModelMessage, ...] | None = restored.messages if restored else None
        state = (
            restored.state
            if restored
            else AgentContextState(
                goal=prompt,
                project_snapshot_id=deps.snapshot_id,
            )
        )
        try:
            result = await self._agent.run(
                prompt,
                deps=deps,
                message_history=history,
                usage_limits=usage_limits,
            )
        except UsageLimitExceeded as error:
            unresolved = tuple(dict.fromkeys((*state.unresolved_issues, "模型预算已耗尽")))
            deps.refresh_sandbox_metadata()
            deps.context.save(
                state.model_copy(update={"unresolved_issues": unresolved}),
                history or (),
                phase=deps.checkpoint_phase,
                run_lease_epoch=deps.run_lease_epoch,
                sandbox_id=deps.sandbox_id,
                sandbox_image_digest=deps.sandbox_image_digest,
            )
            raise AgentBudgetExhausted("Agent 达到 UsageLimits") from error
        output = AgentTextOutput(result.output.strip())
        if not output.text:
            raise RuntimeError("Agent 返回了空回答")
        next_state = state.model_copy(
            update={
                "progress": (*state.progress, "Agent 返回自然语言回答"),
            }
        )
        messages = tuple(result.all_messages())
        deps.refresh_sandbox_metadata()
        checkpoint = deps.context.save(
            next_state,
            messages,
            phase=deps.checkpoint_phase,
            run_lease_epoch=deps.run_lease_epoch,
            sandbox_id=deps.sandbox_id,
            sandbox_image_digest=deps.sandbox_image_digest,
        )
        if self._compactor is not None:
            # 估算只以序列化大小为上限信号，不把摘要当作事实；ContextCompactor 会
            # 保留结构化 state、稳定资源引用和最近消息，再经 ModelGateway 保存。
            message_size = sum(len(str(message)) for message in messages)
            if message_size > self._context_budget_chars:
                deps.refresh_sandbox_metadata()
                compacted = self._compactor.compact(
                    next_state,
                    messages,
                    phase=deps.checkpoint_phase,
                    run_lease_epoch=deps.run_lease_epoch,
                    sandbox_id=deps.sandbox_id,
                    sandbox_image_digest=deps.sandbox_image_digest,
                )
                checkpoint = compacted.checkpoint
        return AgentRunResult(
            output=output,
            checkpoint_ref=checkpoint.checkpoint_ref,
            messages_count=len(messages),
        )
