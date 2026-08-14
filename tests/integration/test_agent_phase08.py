"""Phase 08：PydanticAI Agent、Skill、checkpoint 与历史检索集成测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic_ai.usage import UsageLimits

from dataharness.agent import (
    AgentBudgetExhausted,
    AgentContextState,
    AgentDependencies,
    AgentRunner,
    ContextCheckpointManager,
    ContextCompactor,
    create_agent,
)
from dataharness.analysis import AnalysisRuntime
from dataharness.capabilities.memory import MemoryCapability
from dataharness.domain import (
    ContentHash,
    Project,
    ProjectId,
    ProjectSnapshot,
    ResourceKind,
    ResourceRef,
    Run,
    RunId,
    SnapshotId,
    Task,
    TaskId,
)
from dataharness.privacy import ModelGateway, PlaceholderStore, PrivacyPolicy
from dataharness.providers.memory import SqliteHistoryStore
from dataharness.providers.workspace import FakeWorkspace
from dataharness.skills import SkillChangedError, SkillRegistry
from dataharness.storage import (
    PrivacyConnectionFactory,
    RuntimeConnectionFactory,
    SqliteRuntimeStore,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class ScriptedProvider:
    """按顺序返回固定模型文本，验证所有调用都穿过 ModelGateway。"""

    responses: list[str]
    calls: list[str] = field(default_factory=list)

    def complete(self, request: str) -> str:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("模型调用次数超出测试脚本")
        return self.responses.pop(0)


class FakeAnalysis:
    """只实现工具循环本测试需要的窄分析接口。"""

    def list_project_files(self) -> tuple[object, ...]:
        return ()


@dataclass
class System:
    """构造 Runtime、Workspace、隐私网关和固定 Run。"""

    store: SqliteRuntimeStore
    workspace: FakeWorkspace
    gateway: ModelGateway
    provider: ScriptedProvider
    manager: ContextCheckpointManager
    task_id: TaskId
    run_id: RunId
    snapshot_id: SnapshotId


def _system(tmp_path: Path, responses: list[str]) -> System:
    provider = ScriptedProvider(responses)
    store = SqliteRuntimeStore(RuntimeConnectionFactory(tmp_path / "runtime.db"))
    project = Project(id=ProjectId("project"), name="phase-08", created_at=T0)
    snapshot = ProjectSnapshot(id=SnapshotId("snapshot"), project_id=project.id, created_at=T0)
    task = Task(id=TaskId("task"), project_id=project.id, created_at=T0, updated_at=T0)
    run = Run(
        id=RunId("run"),
        task_id=task.id,
        project_id=project.id,
        project_snapshot_id=snapshot.id,
        created_at=T0,
        updated_at=T0,
    )
    with store.unit_of_work() as uow:
        uow.repo.add_project(project)
        uow.repo.add_snapshot(snapshot)
        uow.repo.add_task(task)
        uow.repo.add_run(run)
    workspace = FakeWorkspace(tmp_path / "workspace")
    gateway = ModelGateway(
        provider,
        PrivacyPolicy(
            PlaceholderStore(
                PrivacyConnectionFactory(tmp_path / "privacy", tmp_path / "privacy-runtime.db")
            )
        ),
    )
    manager = ContextCheckpointManager(
        workspace,
        store,
        gateway,
        project_id=project.id,
        task_id=task.id,
        run_id=run.id,
        snapshot_id=snapshot.id,
    )
    return System(store, workspace, gateway, provider, manager, task.id, run.id, snapshot.id)


def test_skill_registry_is_progressive_and_rejects_changes(tmp_path: Path) -> None:
    """未激活时只返回描述，激活后修改正文会阻止旧 Run 继续使用。"""
    skill_dir = tmp_path / "skills" / "quality"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# 质量检查\n只检查输入数据。\n", encoding="utf-8")
    (skill_dir / "scripts" / "check.py").write_text("print('ok')\n", encoding="utf-8")

    registry = SkillRegistry(tmp_path / "skills")
    descriptor = registry.discover()[0]
    assert descriptor.name == "quality"
    assert descriptor.content_hash
    loaded = registry.activate("quality", expected_hash=descriptor.content_hash)
    assert loaded.content.startswith("# 质量检查")
    assert registry.load_active_script("quality", "check.py").code.replace("\r\n", "\n") == (
        "print('ok')\n"
    )

    (skill_dir / "SKILL.md").write_text("# 被修改的 Skill\n", encoding="utf-8")
    with pytest.raises(SkillChangedError):
        registry.load_active_script("quality", "check.py")


def test_checkpoint_compaction_preserves_domain_refs_and_restores_messages(tmp_path: Path) -> None:
    """压缩摘要可更新，但 Dataset 引用仍来自结构化状态且可恢复。"""
    system = _system(tmp_path, ["压缩后的上下文摘要"])
    reference = ResourceRef(
        kind=ResourceKind.DATASET,
        resource_id="dataset-1",
        content_hash=ContentHash("hash-dataset"),
    )
    state = AgentContextState(
        goal="检查数据质量",
        plan=("检索项目", "执行检查"),
        progress=("已完成检索",),
        project_snapshot_id=system.snapshot_id,
        domain_refs=(reference,),
        unresolved_issues=("等待人工确认",),
    )
    checkpoint = system.manager.save(state, ())
    assert checkpoint.sequence == 1
    compacted = ContextCompactor(system.manager, system.gateway, keep_messages=1).compact(state, ())
    assert compacted.checkpoint.sequence == 2
    restored = system.manager.load_latest()
    assert restored is not None
    assert restored.state.domain_refs == (reference,)
    assert restored.state.summary == "压缩后的上下文摘要"
    assert restored.state.unresolved_issues == ("等待人工确认",)


def test_memory_capability_uses_separate_fts5_history_and_gateway_redaction(tmp_path: Path) -> None:
    """历史检索使用独立 FTS5，写入前经过 Gateway 后不保留原始邮箱。"""
    system = _system(tmp_path, [])
    memory = MemoryCapability(SqliteHistoryStore(tmp_path / "history.db"), system.gateway)
    entry = memory.remember(
        task_id=system.task_id,
        run_id=system.run_id,
        text="analysis report for alice@example.test",
        created_at=T0,
    )
    assert "alice@example.test" not in entry.text
    hits = memory.search("analysis")
    assert len(hits) == 1
    assert hits[0].entry.content_hash == entry.content_hash


@pytest.mark.asyncio
async def test_agent_tool_loop_and_structured_output_use_gateway(tmp_path: Path) -> None:
    """Fake Provider 先请求窄工具，再返回结构化结果，且两次都经过网关。"""
    final = '{"status":"COMPLETED","answer":"检查完成","references":[],"unresolved_issues":[]}'
    system = _system(
        tmp_path,
        ['{"tool_call":{"name":"list_project_files","args":{}}}', final],
    )
    registry = SkillRegistry(tmp_path / "skills")
    agent = create_agent(gateway=system.gateway, task_id=system.task_id, skills=registry)
    deps = AgentDependencies(
        task_id=system.task_id,
        run_id=system.run_id,
        snapshot_id=system.snapshot_id,
        analysis=cast(AnalysisRuntime, FakeAnalysis()),
        skills=registry,
        context=system.manager,
        gateway=system.gateway,
    )
    result = await AgentRunner(agent).run("检查项目", deps)
    assert result.output.status == "COMPLETED"
    assert result.output.answer == "检查完成"
    assert result.messages_count >= 4
    assert len(system.provider.calls) == 2
    assert all("tools" in request for request in system.provider.calls)
    assert system.manager.load_latest() is not None


@pytest.mark.asyncio
async def test_agent_budget_exhaustion_writes_recovery_checkpoint(tmp_path: Path) -> None:
    """模型持续请求工具时触发 UsageLimits，并保存可恢复的 WAITING 上下文。"""
    system = _system(
        tmp_path,
        ['{"tool_call":{"name":"list_project_files","args":{}}}'] * 3,
    )
    registry = SkillRegistry(tmp_path / "skills")
    agent = create_agent(gateway=system.gateway, task_id=system.task_id, skills=registry)
    deps = AgentDependencies(
        task_id=system.task_id,
        run_id=system.run_id,
        snapshot_id=system.snapshot_id,
        analysis=cast(AnalysisRuntime, FakeAnalysis()),
        skills=registry,
        context=system.manager,
        gateway=system.gateway,
    )
    with pytest.raises(AgentBudgetExhausted):
        await AgentRunner(agent).run(
            "持续检查项目",
            deps,
            usage_limits=UsageLimits(request_limit=1, tool_calls_limit=1),
        )
    restored = system.manager.load_latest()
    assert restored is not None
    assert "模型预算已耗尽" in restored.state.unresolved_issues
