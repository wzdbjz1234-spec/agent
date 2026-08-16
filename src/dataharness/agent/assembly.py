"""PydanticAI Agent 组装入口。"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from dataharness.capabilities.memory import MemoryCapability
from dataharness.domain import ContentHash, RunId, TaskId
from dataharness.privacy import ModelGateway
from dataharness.skills import LoadedSkill, SkillRegistry

from .model import gateway_function_model
from .models import AgentDependencies, AgentFinalOutput
from .tools import (
    execute_python,
    execute_sql,
    get_project_coverage,
    inspect_output,
    inspect_project_file,
    list_project_files,
    preview_project_table,
    publish_chart,
    run_skill_script,
    search_history,
    search_project,
    submit_finding,
)


def _skill_instructions(loaded: tuple[LoadedSkill, ...]) -> str:
    """只把本次激活 Skill 的正文注入系统指令。"""
    if not loaded:
        return "本次 Run 没有激活 Skill。"
    sections = ["本次 Run 已激活以下本地 Skill；只可使用已注册脚本："]
    for skill in loaded:
        sections.append(f"## {skill.descriptor.name}\n{skill.content}")
    return "\n\n".join(sections)


def create_agent(
    *,
    gateway: ModelGateway,
    task_id: TaskId,
    run_id: RunId | None = None,
    skills: SkillRegistry,
    active_skills: tuple[tuple[str, ContentHash | None], ...] = (),
    memory: MemoryCapability | None = None,
) -> Agent[AgentDependencies, AgentFinalOutput]:
    """组装单一 PydanticAI Agent，并仅注册固定的窄工具。"""
    loaded = tuple(
        skills.activate(name, expected_hash=expected_hash) for name, expected_hash in active_skills
    )
    tools = [
        list_project_files,
        search_project,
        inspect_project_file,
        get_project_coverage,
        execute_python,
        execute_sql,
        preview_project_table,
        publish_chart,
        inspect_output,
        submit_finding,
    ]
    if loaded:
        tools.append(run_skill_script)
    if memory is not None:
        tools.append(search_history)
    instructions = (
        "你是 DataHarness 的唯一分析 Agent。只能使用注册的窄工具；"
        "不要访问主机路径、直接执行代码、安装依赖或创建第二个 Agent。"
        "跨文件检索必须使用 ProjectCorpus 工具，历史检索只能用于辅助上下文。"
        "checkpoint summary 只是摘要，不是事实；所有 Dataset、Artifact、Finding 和文件事实"
        "必须来自工具返回的稳定引用。"
        "最终必须输出结构化 JSON，status 只能是 COMPLETED 或 WAITING；无论 status 如何，"
        "都必须使用 answer 字段承载面向用户的说明，不要使用 summary 字段，并列出"
        " unresolved_issues。\n\n" + _skill_instructions(loaded)
    )
    return Agent(
        gateway_function_model(gateway, task_id, run_id),
        output_type=AgentFinalOutput,
        deps_type=AgentDependencies,
        system_prompt=instructions,
        tools=tools,
        retries=1,
        name="dataharness-agent",
    )


def default_usage_limits() -> UsageLimits:
    """返回受控默认预算；调用方可按 Run 策略覆盖。"""
    return UsageLimits(request_limit=20, tool_calls_limit=40, total_tokens_limit=50_000)
