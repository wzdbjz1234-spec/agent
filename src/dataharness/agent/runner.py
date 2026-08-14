"""Agent Run 驱动、checkpoint 保存与预算耗尽处理。"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits

from .context import AgentContextState
from .models import AgentDependencies, AgentFinalOutput, AgentRunResult


class AgentBudgetExhausted(RuntimeError):
    """Agent 达到请求、工具或 Token 预算，应由 orchestration 转为 WAITING。"""


class AgentRunner:
    """负责恢复历史消息、运行单一 Agent 并记录最终结构化状态。"""

    def __init__(self, agent: Agent[AgentDependencies, AgentFinalOutput]) -> None:
        self._agent = agent

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
            deps.context.save(
                state.model_copy(update={"unresolved_issues": unresolved}),
                history or (),
            )
            raise AgentBudgetExhausted("Agent 达到 UsageLimits") from error
        output = result.output
        refs = tuple(dict.fromkeys((*state.domain_refs, *output.references)))
        next_state = state.model_copy(
            update={
                "domain_refs": refs,
                "unresolved_issues": output.unresolved_issues,
                "progress": (*state.progress, f"Agent 返回 {output.status}"),
            }
        )
        messages = tuple(result.all_messages())
        checkpoint = deps.context.save(next_state, messages)
        return AgentRunResult(
            output=output,
            checkpoint_ref=checkpoint.checkpoint_ref,
            messages_count=len(messages),
        )
