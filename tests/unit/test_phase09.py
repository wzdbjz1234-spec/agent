"""Phase 09：Finding Gate、数据告警与观测隐私边界。"""

from __future__ import annotations

import pytest

from dataharness.analysis import (
    AnalysisSummary,
    DataWarningDetector,
    DataWarningKind,
    ExecutionGate,
)
from dataharness.domain import ContentHash, StepId, TaskId
from dataharness.providers.observability import (
    ObservabilityPrivacyError,
    ObservationContext,
    OpenTelemetryAdapter,
)
from dataharness.sandbox import ExecutionStatus


def _summary(*, status: ExecutionStatus = ExecutionStatus.SUCCEEDED) -> AnalysisSummary:
    """构造只含 hash/状态元数据的最小 Step 摘要。"""
    return AnalysisSummary(
        step_id=StepId("step-1"),
        request_hash=ContentHash("request-hash"),
        status=status,
        exit_code=0 if status is ExecutionStatus.SUCCEEDED else 1,
        stdout="",
        stderr="",
        duration_ms=1,
        code_hash=ContentHash("code-hash"),
        input_refs=(),
    )


def test_execution_gate_rejects_non_successful_step() -> None:
    """未成功结束的 Sandbox Step 不能把 Finding 晋级为 VERIFIED。"""
    report = ExecutionGate.check(_summary(status=ExecutionStatus.FAILED))
    assert report.passed is False
    assert report.gate == "ExecutionGate"


def test_data_warning_detector_keeps_quality_anomalies_as_warnings() -> None:
    """行数、Join、缺失值、转换和重复值异常只产出结构化 Warning。"""
    warnings = DataWarningDetector.detect(
        {
            "rows": 500,
            "expected_rows": 10,
            "input_rows": 10,
            "join_rows": 200,
            "missing_values": 1,
            "conversion_failures": 2,
            "duplicates": 3,
        }
    )
    assert {item.kind for item in warnings} == {
        DataWarningKind.ROW_COUNT_ANOMALY,
        DataWarningKind.JOIN_EXPLOSION,
        DataWarningKind.MISSING_VALUES,
        DataWarningKind.TYPE_CONVERSION,
        DataWarningKind.DUPLICATE_VALUES,
    }


def test_observability_drops_only_backend_failures_and_requires_privacy_for_text() -> None:
    """无文本的观测可记录；文本没有 ModelGateway 时必须 fail closed。"""
    adapter = OpenTelemetryAdapter()
    adapter.record(
        "step.completed",
        ObservationContext(task_id=TaskId("task-1"), run_id="run-1"),
        {"status": "SUCCEEDED", "duration_ms": 2},
    )
    assert adapter.records[0].attributes["task_id"] == "task-1"
    with pytest.raises(ObservabilityPrivacyError):
        adapter.record(
            "step.output",
            ObservationContext(task_id=TaskId("task-1")),
            {"message": "可能含有原始业务内容"},
        )
