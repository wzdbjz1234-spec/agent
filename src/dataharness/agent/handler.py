"""生产 AgentRunHandler：把单一 Agent 接到既有 Analysis/Verification seam。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from pydantic_ai.exceptions import UnexpectedModelBehavior

from dataharness.analysis import AnalysisRuntime, VerificationService
from dataharness.capabilities.memory import MemoryCapability
from dataharness.domain import ContentHash, RunPhase, WaitReason, compute_content_hash
from dataharness.orchestration import ExecutionDecision, RunExecutionContext, RunOutcome
from dataharness.privacy import ModelGateway, ModelProviderError, SecretDetectedError
from dataharness.projects import ProjectCorpus
from dataharness.providers.memory import HistoryStore
from dataharness.sandbox import SandboxProvider
from dataharness.skills import SkillRegistry
from dataharness.storage import SqliteRuntimeStore
from dataharness.workspace import VirtualWorkspace, WorkspaceBridge

from .assembly import create_agent, default_usage_limits
from .context import ContextCheckpointManager, ContextCompactor
from .intent import PromptIntent, casual_reply, classify_prompt
from .models import AgentDependencies
from .runner import AgentBudgetExhausted, AgentRunner


class AgentPromptError(ValueError):
    """Task prompt 缺失、篡改或载荷格式错误。"""


class AgentRunHandler:
    """一次 Run 的生产装配入口。

    Handler 不创建第二个 Agent，也不执行 Python/SQL。它只负责从固定 Run/Snapshot
    组装已有的 AgentRunner、AnalysisRuntime、Context、Memory 与 Verification 服务；
    AnalysisRuntime 通过上下文 seam 按需取得 Sandbox，并把模型可见结果收口成
    ``RunOutcome``。
    """

    def __init__(
        self,
        store: SqliteRuntimeStore,
        corpus: ProjectCorpus,
        workspace: VirtualWorkspace,
        sandbox: SandboxProvider,
        gateway: ModelGateway,
        skills: SkillRegistry,
        *,
        bridge: WorkspaceBridge | None = None,
        verification: VerificationService | None = None,
        history_store: HistoryStore | None = None,
        active_skills: tuple[tuple[str, ContentHash | None], ...] = (),
        usage_limits=None,
        clock: Callable[[], datetime] | None = None,
        context_budget_chars: int = 120_000,
    ) -> None:
        self._store = store
        self._corpus = corpus
        self._workspace = workspace
        self._sandbox = sandbox
        self._gateway = gateway
        self._skills = skills
        self._bridge = bridge
        self._verification = verification
        self._history_store = history_store
        self._active_skills = active_skills
        self._usage_limits = usage_limits or default_usage_limits()
        self._clock = clock or datetime.now
        self._context_budget_chars = context_budget_chars

    def _read_prompt_payload(self, project_id, task_id, task) -> str:
        """读取并校验固定 Prompt 载荷；路由和正式 Handler 共享同一校验。"""
        if task.prompt_ref is None or task.prompt_hash is None:
            raise AgentPromptError("Task 没有可恢复的用户问题")
        expected_ref = f"task:{task.id}:state:PROMPT.json"
        if task.prompt_ref != expected_ref:
            raise AgentPromptError("Task prompt 引用不符合受控 Workspace 规则")
        try:
            raw = self._workspace.read_task_state(project_id, task_id, "PROMPT.json")
        except (FileNotFoundError, OSError) as error:
            raise AgentPromptError("Task prompt 载荷不存在") from error
        if compute_content_hash(raw) != task.prompt_hash:
            raise AgentPromptError("Task prompt 载荷哈希不匹配")
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise AgentPromptError("Task prompt 载荷不是有效 JSON") from error
        if not isinstance(payload, dict) or payload.get("task_id") not in {None, str(task.id)}:
            raise AgentPromptError("Task prompt 载荷归属不一致")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise AgentPromptError("Task prompt 文本为空")
        return prompt

    def _prompt(self, context: RunExecutionContext) -> str:
        """读取并校验不可变 PROMPT.json；任何漂移都阻止模型调用。"""
        task = self._get_task(context)
        return self._read_prompt_payload(context.run.project_id, context.run.task_id, task)

    def _get_task(self, context: RunExecutionContext):
        with self._store.unit_of_work() as uow:
            return uow.repo.get_task(context.run.task_id).value

    def requires_sandbox(self, run) -> bool:
        """声明 Handler 支持按需 Sandbox；真正执行由 AnalysisRuntime 触发。"""
        del run
        return False

    def _write_answer(self, context: RunExecutionContext, *, status: str, answer: str) -> None:
        """把已脱敏的用户可见回答写入 Task Workspace，供 API 稳定读取。"""
        payload = json.dumps(
            {
                "schema_version": 1,
                "task_id": str(context.run.task_id),
                "status": status,
                "answer": answer,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._workspace.write_task_state(
            context.run.project_id, context.run.task_id, "ANSWER.json", payload
        )

    @staticmethod
    def _checkpoint(manager: ContextCheckpointManager):
        """避免在条件表达式中重复读取 Workspace/Runtime checkpoint。"""
        restored = manager.load_latest()
        return restored.metadata if restored else None

    def _event(self, context: RunExecutionContext, event_type: str, **metadata: object) -> None:
        """写入不含原始 prompt/响应的 Run 事件，供 API/SSE 投影。"""
        with self._store.unit_of_work() as uow:
            uow.repo.append_event("run", str(context.run.id), event_type, context.now, metadata)

    async def __call__(self, context: RunExecutionContext) -> RunOutcome:
        """执行一次 Agent，并在输出后运行 Host Verification Gate。"""
        try:
            prompt = self._prompt(context)
        except AgentPromptError:
            self._event(context, "AGENT_WAITING", reason=WaitReason.USER_INPUT.value)
            return RunOutcome(
                decision=ExecutionDecision.WAITING,
                wait_reason=WaitReason.USER_INPUT,
            )
        if classify_prompt(prompt) is PromptIntent.CASUAL:
            answer = casual_reply(prompt)
            self._write_answer(context, status="COMPLETED", answer=answer)
            self._event(context, "AGENT_COMPLETED", references=0, messages=0, mode="CASUAL")
            return RunOutcome(decision=ExecutionDecision.SUCCEEDED, phase=RunPhase.REASONING)
        if self._sandbox is None:
            self._event(context, "AGENT_WAITING", reason=WaitReason.MISSING_DEPENDENCY.value)
            return RunOutcome(
                decision=ExecutionDecision.WAITING,
                wait_reason=WaitReason.MISSING_DEPENDENCY,
            )
        if self._bridge is None:
            self._event(context, "AGENT_WAITING", reason="PUBLICATION_NOT_CONFIGURED")
            return RunOutcome(
                decision=ExecutionDecision.WAITING,
                wait_reason=WaitReason.MISSING_DEPENDENCY,
            )
        try:
            prompt = self._prompt(context)
        except AgentPromptError:
            self._event(context, "AGENT_WAITING", reason=WaitReason.USER_INPUT.value)
            return RunOutcome(
                decision=ExecutionDecision.WAITING,
                wait_reason=WaitReason.USER_INPUT,
            )

        task = self._get_task(context)
        manager = ContextCheckpointManager(
            self._workspace,
            self._store,
            self._gateway,
            project_id=context.run.project_id,
            task_id=context.run.task_id,
            run_id=context.run.id,
            snapshot_id=context.run.project_snapshot_id,
        )
        memory = None
        if self._history_store is not None:
            memory = MemoryCapability(
                self._history_store,
                self._gateway,
                project_id=context.run.project_id,
                session_id=task.session_id,
            )
        analysis = AnalysisRuntime(
            self._store,
            self._corpus,
            self._workspace,
            self._sandbox,
            context.sandbox_lease,
            run=context.run,
            sandbox_lease_factory=context.ensure_sandbox,
            bridge=self._bridge,
        )
        dependencies = AgentDependencies(
            task_id=context.run.task_id,
            run_id=context.run.id,
            snapshot_id=context.run.project_snapshot_id,
            analysis=analysis,
            skills=self._skills,
            context=manager,
            gateway=self._gateway,
            memory=memory,
            sandbox_id=(context.sandbox_lease.sandbox_id if context.sandbox_lease else None),
            sandbox_image_digest=(
                context.sandbox_lease.image_digest if context.sandbox_lease else None
            ),
            run_lease_epoch=context.lease_epoch,
            checkpoint_phase=RunPhase.REASONING,
            sandbox_lease_getter=lambda: context.sandbox_lease,
        )
        self._event(context, "AGENT_STARTED", phase=RunPhase.REASONING.value)
        agent = create_agent(
            gateway=self._gateway,
            task_id=context.run.task_id,
            run_id=context.run.id,
            skills=self._skills,
            active_skills=self._active_skills,
            memory=memory,
        )
        runner = AgentRunner(
            agent,
            compactor=ContextCompactor(manager, self._gateway),
            context_budget_chars=self._context_budget_chars,
        )
        try:
            result = await runner.run(prompt, dependencies, usage_limits=self._usage_limits)
        except AgentBudgetExhausted:
            self._event(context, "AGENT_WAITING", reason=WaitReason.BUDGET_EXHAUSTED.value)
            return RunOutcome(
                decision=ExecutionDecision.WAITING,
                wait_reason=WaitReason.BUDGET_EXHAUSTED,
                checkpoint=self._checkpoint(manager),
            )
        except ModelProviderError as error:
            if error.code in {
                "MODEL_API_KEY_MISSING",
                "MODEL_AUTHENTICATION_FAILED",
                "MODEL_ACCESS_DENIED",
                "MODEL_REQUEST_INVALID",
                "MODEL_RESPONSE_INVALID",
                "MODEL_RESPONSE_TOO_LARGE",
            }:
                self._event(context, "AGENT_WAITING", reason=error.code)
                return RunOutcome(
                    decision=ExecutionDecision.WAITING,
                    wait_reason=WaitReason.MISSING_DEPENDENCY,
                )
            raise
        except UnexpectedModelBehavior:
            # PydanticAI 在结构化输出重试耗尽后抛出该稳定异常；它和 Provider 的
            # MODEL_RESPONSE_INVALID 属于同一类可诊断的模型协议问题，不应让整个
            # Run 变成无原因的 INTERNAL_ERROR/FAILED。保留用户可恢复的 WAITING 语义。
            self._event(context, "AGENT_WAITING", reason="MODEL_RESPONSE_INVALID")
            return RunOutcome(
                decision=ExecutionDecision.WAITING,
                wait_reason=WaitReason.MISSING_DEPENDENCY,
                checkpoint=self._checkpoint(manager),
            )
        except SecretDetectedError:
            self._event(context, "AGENT_WAITING", reason="POLICY_DENIED")
            return RunOutcome(
                decision=ExecutionDecision.WAITING,
                wait_reason=WaitReason.USER_INPUT,
            )

        # Agent 的 WAITING 是用户交互语义；模型不得写入任意状态字符串。
        if result.output.status == "WAITING":
            self._write_answer(context, status="WAITING", answer=result.output.answer)
            self._event(context, "AGENT_WAITING", reason=WaitReason.USER_INPUT.value)
            return RunOutcome(
                decision=ExecutionDecision.WAITING,
                wait_reason=WaitReason.USER_INPUT,
                checkpoint=self._checkpoint(manager),
            )

        if memory is not None:
            memory.remember(
                task_id=context.run.task_id,
                run_id=context.run.id,
                project_id=context.run.project_id,
                session_id=task.session_id,
                text=result.output.answer,
                references=result.output.references,
                created_at=context.now,
            )
        self._write_answer(context, status="COMPLETED", answer=result.output.answer)
        if self._verification is not None:
            summaries = analysis.summaries_for_run()
            with self._store.unit_of_work() as uow:
                findings = uow.repo.list_findings_for_run(context.run.id)
            for finding in findings:
                if str(finding.status) == "DRAFT":
                    self._verification.verify(finding.id, summaries)
        self._event(
            context,
            "AGENT_COMPLETED",
            references=len(result.output.references),
            messages=result.messages_count,
        )
        restored = manager.load_latest()
        checkpoint = restored.metadata if restored else None
        # Executor 每次只推进一个 Run phase；从 PREPARING 进入 REASONING，恢复时保持
        # 已有 phase，避免用一个 Outcome 跳过中间状态。
        phase = RunPhase.REASONING if context.run.phase == RunPhase.PREPARING else None
        return RunOutcome(
            decision=ExecutionDecision.SUCCEEDED,
            phase=phase,
            checkpoint=checkpoint,
        )
