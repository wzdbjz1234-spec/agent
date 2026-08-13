"""AnalysisStep 状态机测试：失败不回退、failure_kind 一致性、重试新 Step。"""

from __future__ import annotations

from datetime import datetime

import pytest

from dataharness.domain import (
    AnalysisStep,
    IllegalStateTransitionError,
    StepFailureKind,
    StepStatus,
)
from dataharness.domain.ids import RunId, StepId


def make_step(status: StepStatus = StepStatus.PENDING) -> AnalysisStep:
    return AnalysisStep(id=StepId("s1"), run_id=RunId("r1"), status=status)


def test_pending_to_running_to_succeeded(t0: datetime) -> None:
    step = make_step().start(t0).succeed(t0)
    assert step.status is StepStatus.SUCCEEDED
    assert step.started_at == t0
    assert step.finished_at == t0


def test_fail_requires_failure_kind(t0: datetime) -> None:
    step = make_step().start(t0).fail(StepFailureKind.SANDBOX_ERROR, t0)
    assert step.status is StepStatus.FAILED
    assert step.failure_kind is StepFailureKind.SANDBOX_ERROR


def test_timeout(t0: datetime) -> None:
    assert make_step().start(t0).timeout(t0).status is StepStatus.TIMED_OUT


def test_cancel_from_pending_and_running_and_idempotent(t0: datetime) -> None:
    assert make_step().cancel(t0).status is StepStatus.CANCELLED
    running_cancelled = make_step().start(t0).cancel(t0)
    assert running_cancelled.status is StepStatus.CANCELLED
    assert running_cancelled.cancel(t0) is running_cancelled


def test_failed_step_cannot_return_to_running(t0: datetime) -> None:
    step = make_step().start(t0).fail(StepFailureKind.RESOURCE_LIMIT, t0)
    for action in (step.start, step.succeed, step.timeout):
        with pytest.raises(IllegalStateTransitionError):
            action(t0)


def test_retry_creates_new_step_linked_by_retry_of(t0: datetime) -> None:
    original = make_step().start(t0).fail(StepFailureKind.MODEL_CORRECTABLE, t0)
    retry = AnalysisStep(
        id=StepId("s2"),
        run_id=RunId("r1"),
        retry_of_step_id=original.id,
    )
    assert retry.status is StepStatus.PENDING
    assert retry.retry_of_step_id == original.id
    assert retry.id != original.id


def test_failure_kind_consistency_on_construction() -> None:
    with pytest.raises(ValueError):
        AnalysisStep(id=StepId("s1"), run_id=RunId("r1"), status=StepStatus.FAILED)
    with pytest.raises(ValueError):
        AnalysisStep(
            id=StepId("s1"),
            run_id=RunId("r1"),
            status=StepStatus.SUCCEEDED,
            failure_kind=StepFailureKind.INTERNAL_ERROR,
        )
