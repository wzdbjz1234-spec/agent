"""AnalysisRuntime 窄接口契约：禁止 Host 工具和跨 Snapshot 引用。"""

from __future__ import annotations

import pytest

from dataharness.analysis import AnalysisRequest, AnalysisRuntime
from dataharness.sandbox import ExecutionKind


def test_analysis_request_schema_has_no_host_or_network_capabilities() -> None:
    """公开请求只描述 Python/SQL、输入、输出、预算和超时。"""
    fields = set(AnalysisRequest.model_fields)
    assert fields == {
        "kind",
        "code",
        "inputs",
        "expected_outputs",
        "timeout_seconds",
        "budget_units",
        "staging_ref",
        "mode",
    }
    assert set(ExecutionKind) == {ExecutionKind.PYTHON, ExecutionKind.SQL, ExecutionKind.SKILL}
    assert not hasattr(AnalysisRuntime, "run_shell")
    assert not hasattr(AnalysisRuntime, "install_package")
    assert not hasattr(AnalysisRuntime, "enable_network")


def test_analysis_request_rejects_host_staging_paths() -> None:
    """staging 只能是当前 Task 的逻辑引用，不能注入 Host 路径。"""
    with pytest.raises(ValueError):
        AnalysisRequest(
            kind=ExecutionKind.PYTHON,
            code="print(1)",
            timeout_seconds=1,
            staging_ref="C:\\secrets",
        )
