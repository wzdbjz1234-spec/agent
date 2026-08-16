"""Agent 可见的窄工具集合；所有分析执行仍由 AnalysisRuntime 负责。"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai import RunContext

from dataharness.analysis import InputReference, OutputSpec
from dataharness.domain import EvidenceRef

from .diagnostics import log_tool_call, log_tool_error, log_tool_result
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


def _call_log(ctx: RunContext[AgentDependencies], name: str, arguments: dict[str, Any]) -> None:
    log_tool_call(
        ctx.deps.gateway,
        ctx.deps.task_id,
        name,
        arguments,
        run_id=ctx.deps.run_id,
    )


def _result_log(ctx: RunContext[AgentDependencies], name: str, result: Any) -> None:
    log_tool_result(
        ctx.deps.gateway,
        ctx.deps.task_id,
        name,
        result,
        run_id=ctx.deps.run_id,
    )


def _error_log(ctx: RunContext[AgentDependencies], name: str, error: BaseException) -> None:
    log_tool_error(
        ctx.deps.task_id,
        name,
        run_id=ctx.deps.run_id,
        error_type=type(error).__name__,
    )


async def list_project_files(ctx: RunContext[AgentDependencies]) -> str:
    """列出固定 ProjectSnapshot 中的文件版本元数据。"""
    name = "list_project_files"
    _call_log(ctx, name, {})
    try:
        result = ctx.deps.analysis.list_project_files()
    except Exception as error:
        _error_log(ctx, name, error)
        raise
    _result_log(ctx, name, result)
    return _safe_tool_result(ctx, result)


async def search_project(ctx: RunContext[AgentDependencies], query: str, limit: int = 20) -> str:
    """通过 ProjectCorpus 的 FTS5/BM25 检索当前项目 Snapshot。"""
    name = "search_project"
    _call_log(ctx, name, {"query": query, "limit": limit})
    try:
        result = ctx.deps.analysis.search_project(query, limit=limit)
    except Exception as error:
        _error_log(ctx, name, error)
        raise
    _result_log(ctx, name, result)
    return _safe_tool_result(ctx, result)


async def inspect_project_file(
    ctx: RunContext[AgentDependencies], file_version_id: str, max_chars: int = 4096
) -> str:
    """读取固定文件版本的有界片段，不暴露主机路径。"""
    name = "inspect_project_file"
    _call_log(ctx, name, {"file_version_id": file_version_id, "max_chars": max_chars})
    try:
        result = ctx.deps.analysis.inspect_project_file(file_version_id, max_chars=max_chars)
    except Exception as error:
        _error_log(ctx, name, error)
        raise
    _result_log(ctx, name, result)
    return _safe_tool_result(ctx, result)


async def get_project_coverage(ctx: RunContext[AgentDependencies]) -> str:
    """返回当前固定 Snapshot 的 FULL_PROJECT 覆盖报告。"""
    name = "get_project_coverage"
    _call_log(ctx, name, {})
    try:
        result = ctx.deps.analysis.get_project_coverage()
    except Exception as error:
        _error_log(ctx, name, error)
        raise
    _result_log(ctx, name, result)
    return _safe_tool_result(ctx, result)


async def execute_python(
    ctx: RunContext[AgentDependencies],
    code: str,
    inputs: tuple[InputReference, ...] = (),
    expected_outputs: tuple[OutputSpec, ...] = (),
    timeout_seconds: int = 300,
    budget_units: int = 1,
) -> str:
    """把 Python 代码交给 AnalysisRuntime，再由 OpenSandbox 执行。"""
    name = "execute_python"
    _call_log(
        ctx,
        name,
        {
            "code": code,
            "inputs": inputs,
            "expected_outputs": expected_outputs,
            "timeout_seconds": timeout_seconds,
            "budget_units": budget_units,
        },
    )
    try:
        result = await ctx.deps.analysis.execute_python(
            code,
            inputs=inputs,
            expected_outputs=expected_outputs,
            timeout_seconds=timeout_seconds,
            budget_units=budget_units,
        )
    except Exception as error:
        _error_log(ctx, name, error)
        raise
    _result_log(ctx, name, result)
    return _safe_tool_result(ctx, result)


async def execute_sql(
    ctx: RunContext[AgentDependencies],
    query: str,
    inputs: tuple[InputReference, ...] = (),
    expected_outputs: tuple[OutputSpec, ...] = (),
    timeout_seconds: int = 300,
    budget_units: int = 1,
) -> str:
    """把 SQL 查询交给 AnalysisRuntime，再由受控 Sandbox 执行。"""
    name = "execute_sql"
    _call_log(
        ctx,
        name,
        {
            "query": query,
            "inputs": inputs,
            "expected_outputs": expected_outputs,
            "timeout_seconds": timeout_seconds,
            "budget_units": budget_units,
        },
    )
    try:
        result = await ctx.deps.analysis.execute_sql(
            query,
            inputs=inputs,
            expected_outputs=expected_outputs,
            timeout_seconds=timeout_seconds,
            budget_units=budget_units,
        )
    except Exception as error:
        _error_log(ctx, name, error)
        raise
    _result_log(ctx, name, result)
    return _safe_tool_result(ctx, result)


async def preview_project_table(
    ctx: RunContext[AgentDependencies],
    table_name: str,
    rows: int = 20,
    timeout_seconds: int = 300,
) -> str:
    """通过 Sandbox SQL runner 获取有界表格预览，不在 Host 执行 SQL。"""
    name = "preview_project_table"
    _call_log(
        ctx,
        name,
        {"table_name": table_name, "rows": rows, "timeout_seconds": timeout_seconds},
    )
    try:
        result = await ctx.deps.analysis.preview_project_table(
            table_name, rows=rows, timeout_seconds=timeout_seconds
        )
    except Exception as error:
        _error_log(ctx, name, error)
        raise
    _result_log(ctx, name, result)
    return _safe_tool_result(ctx, result)


async def publish_chart(
    ctx: RunContext[AgentDependencies],
    dataset_id: str,
    dataset_hash: str,
    name: str,
    spec: dict[str, object],
) -> str:
    """提交受控 Vega-Lite JSON；Dataset 归属、hash 和安全字段由 Host 校验。"""
    tool_name = "publish_chart"
    _call_log(
        ctx,
        tool_name,
        {"dataset_id": dataset_id, "dataset_hash": dataset_hash, "name": name, "spec": spec},
    )
    try:
        result = ctx.deps.analysis.publish_chart(dataset_id, dataset_hash, name, spec)
    except Exception as error:
        _error_log(ctx, tool_name, error)
        raise
    _result_log(ctx, tool_name, result)
    return _safe_tool_result(ctx, result)


async def inspect_output(
    ctx: RunContext[AgentDependencies], step_id: str, output_name: str, max_chars: int = 4096
) -> str:
    """检查当前 Task 的受控 staging 输出。"""
    name = "inspect_output"
    _call_log(ctx, name, {"step_id": step_id, "output_name": output_name, "max_chars": max_chars})
    try:
        result = ctx.deps.analysis.inspect_output(step_id, output_name, max_chars=max_chars)
    except Exception as error:
        _error_log(ctx, name, error)
        raise
    _result_log(ctx, name, result)
    return _safe_tool_result(ctx, result)


async def submit_finding(
    ctx: RunContext[AgentDependencies], summary: str, evidence: tuple[EvidenceRef, ...]
) -> str:
    """提交带证据的 FindingCandidate，正式验证仍由 Host Gate 负责。"""
    name = "submit_finding"
    _call_log(ctx, name, {"summary": summary, "evidence": evidence})
    try:
        result = ctx.deps.analysis.submit_finding(summary, evidence)
    except Exception as error:
        _error_log(ctx, name, error)
        raise
    _result_log(ctx, name, result)
    return _safe_tool_result(ctx, result)


async def run_skill_script(
    ctx: RunContext[AgentDependencies],
    skill_name: str,
    script_name: str,
    timeout_seconds: int = 300,
    budget_units: int = 1,
) -> str:
    """加载已激活 Skill 脚本，并强制通过 AnalysisRuntime/OpenSandbox 执行。"""
    name = "run_skill_script"
    _call_log(
        ctx,
        name,
        {
            "skill_name": skill_name,
            "script_name": script_name,
            "timeout_seconds": timeout_seconds,
            "budget_units": budget_units,
        },
    )
    try:
        script = ctx.deps.skills.load_active_script(skill_name, script_name)
        result = await ctx.deps.analysis.execute_python(
            script.code,
            timeout_seconds=timeout_seconds,
            budget_units=budget_units,
        )
    except Exception as error:
        _error_log(ctx, name, error)
        raise
    _result_log(ctx, name, result)
    return _safe_tool_result(ctx, result)


async def search_history(ctx: RunContext[AgentDependencies], query: str, limit: int = 20) -> str:
    """检索独立对话历史；不读取 ProjectCorpus 或向量索引。"""
    name = "search_history"
    _call_log(ctx, name, {"query": query, "limit": limit})
    try:
        result = (
            {"hits": [], "disabled": True}
            if ctx.deps.memory is None
            else ctx.deps.memory.search(query, limit=limit)
        )
    except Exception as error:
        _error_log(ctx, name, error)
        raise
    _result_log(ctx, name, result)
    return _safe_tool_result(ctx, result)
