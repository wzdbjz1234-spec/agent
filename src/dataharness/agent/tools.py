"""Agent 可见的窄工具集合；所有分析执行仍由 AnalysisRuntime 负责。"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai import RunContext

from dataharness.domain import EvidenceRef

from .models import AgentDependencies


def _safe_tool_result(ctx: RunContext[AgentDependencies], value: Any) -> str:
    """工具返回模型前经过统一脱敏边界，避免旁路泄露原始结果。"""
    if hasattr(value, "model_dump"):
        payload: Any = value.model_dump(mode="json")
    elif isinstance(value, tuple):
        payload = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value
        ]
    else:
        payload = value
    text = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
    return ctx.deps.gateway.sanitize_tool_result(ctx.deps.task_id, text).cloud_text


async def list_project_files(ctx: RunContext[AgentDependencies]) -> str:
    """列出固定 ProjectSnapshot 中的文件版本元数据。"""
    return _safe_tool_result(ctx, ctx.deps.analysis.list_project_files())


async def search_project(ctx: RunContext[AgentDependencies], query: str, limit: int = 20) -> str:
    """通过 ProjectCorpus 的 FTS5/BM25 检索当前项目 Snapshot。"""
    return _safe_tool_result(ctx, ctx.deps.analysis.search_project(query, limit=limit))


async def inspect_project_file(
    ctx: RunContext[AgentDependencies], file_version_id: str, max_chars: int = 4096
) -> str:
    """读取固定文件版本的有界片段，不暴露主机路径。"""
    result = ctx.deps.analysis.inspect_project_file(file_version_id, max_chars=max_chars)
    return _safe_tool_result(ctx, result)


async def execute_python(
    ctx: RunContext[AgentDependencies],
    code: str,
    timeout_seconds: int = 300,
    budget_units: int = 1,
) -> str:
    """把 Python 代码交给 AnalysisRuntime，再由 OpenSandbox 执行。"""
    result = await ctx.deps.analysis.execute_python(
        code, timeout_seconds=timeout_seconds, budget_units=budget_units
    )
    return _safe_tool_result(ctx, result)


async def execute_sql(
    ctx: RunContext[AgentDependencies],
    query: str,
    timeout_seconds: int = 300,
    budget_units: int = 1,
) -> str:
    """把 SQL 查询交给 AnalysisRuntime，再由受控 Sandbox 执行。"""
    result = await ctx.deps.analysis.execute_sql(
        query, timeout_seconds=timeout_seconds, budget_units=budget_units
    )
    return _safe_tool_result(ctx, result)


async def inspect_output(
    ctx: RunContext[AgentDependencies], step_id: str, output_name: str, max_chars: int = 4096
) -> str:
    """检查当前 Task 的受控 staging 输出。"""
    result = ctx.deps.analysis.inspect_output(step_id, output_name, max_chars=max_chars)
    return _safe_tool_result(ctx, result)


async def submit_finding(
    ctx: RunContext[AgentDependencies], summary: str, evidence: tuple[EvidenceRef, ...]
) -> str:
    """提交带证据的 FindingCandidate，正式验证仍由 Host Gate 负责。"""
    result = ctx.deps.analysis.submit_finding(summary, evidence)
    return _safe_tool_result(ctx, result)


async def run_skill_script(
    ctx: RunContext[AgentDependencies],
    skill_name: str,
    script_name: str,
    timeout_seconds: int = 300,
    budget_units: int = 1,
) -> str:
    """加载已激活 Skill 脚本，并强制通过 AnalysisRuntime/OpenSandbox 执行。"""
    script = ctx.deps.skills.load_active_script(skill_name, script_name)
    result = await ctx.deps.analysis.execute_python(
        script.code,
        timeout_seconds=timeout_seconds,
        budget_units=budget_units,
    )
    return _safe_tool_result(ctx, result)


async def search_history(ctx: RunContext[AgentDependencies], query: str, limit: int = 20) -> str:
    """检索独立对话历史；不读取 ProjectCorpus 或向量索引。"""
    if ctx.deps.memory is None:
        return _safe_tool_result(ctx, {"hits": [], "disabled": True})
    return _safe_tool_result(ctx, ctx.deps.memory.search(query, limit=limit))
