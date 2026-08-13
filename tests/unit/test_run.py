"""Run 状态机测试：生命周期、固定 Snapshot 与 phase 前向推进。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from dataharness.domain import (
    IllegalStateTransitionError,
    InvalidStateError,
    Run,
    RunPhase,
    RunStatus,
    WaitReason,
)
from dataharness.domain.ids import ProjectId, RunId, SnapshotId, TaskId

# 模块级固定时间：@given 测试不能混用 pytest fixture，故用常量保证确定性
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def make_run(
    status: RunStatus = RunStatus.QUEUED,
    phase: RunPhase = RunPhase.PREPARING,
    wait_reason: WaitReason | None = None,
) -> Run:
    return Run(
        id=RunId("r1"),
        task_id=TaskId("t1"),
        project_id=ProjectId("p1"),
        project_snapshot_id=SnapshotId("s1"),
        status=status,
        phase=phase,
        wait_reason=wait_reason,
    )


_ACTIONS = ("start", "wait", "resume", "succeed", "fail", "cancel")


def _apply(run: Run, action: str, t0: datetime) -> Run:
    if action == "start":
        return run.start(t0)
    if action == "wait":
        return run.wait(WaitReason.USER_INPUT, t0)
    if action == "resume":
        return run.resume(t0)
    if action == "succeed":
        return run.succeed(t0)
    if action == "fail":
        return run.fail(t0)
    if action == "cancel":
        return run.cancel(t0)
    raise AssertionError(f"unknown action {action}")


def _wait_reason_for(status: RunStatus) -> WaitReason | None:
    return WaitReason.USER_INPUT if status is RunStatus.WAITING else None


@pytest.mark.parametrize(
    ("start_status", "action", "expected"),
    [
        (RunStatus.QUEUED, "start", RunStatus.RUNNING),
        (RunStatus.QUEUED, "cancel", RunStatus.CANCELLED),
        (RunStatus.RUNNING, "wait", RunStatus.WAITING),
        (RunStatus.RUNNING, "succeed", RunStatus.SUCCEEDED),
        (RunStatus.RUNNING, "fail", RunStatus.FAILED),
        (RunStatus.RUNNING, "cancel", RunStatus.CANCELLED),
        (RunStatus.WAITING, "resume", RunStatus.RUNNING),
    ],
)
def test_legal_transition(
    start_status: RunStatus, action: str, expected: RunStatus, t0: datetime
) -> None:
    run = make_run(status=start_status, wait_reason=_wait_reason_for(start_status))
    assert _apply(run, action, t0).status is expected


def test_terminal_run_cannot_reopen(t0: datetime) -> None:
    for terminal in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
        run = make_run(status=terminal)
        for action in _ACTIONS:
            try:
                result = _apply(run, action, t0)
            except IllegalStateTransitionError:
                continue
            assert result.status is terminal


def test_run_fixes_project_snapshot_id(t0: datetime) -> None:
    run = make_run()
    assert run.project_snapshot_id == SnapshotId("s1")
    # project_snapshot_id 为必填字段，缺失时构造失败
    with pytest.raises(ValidationError):
        Run.model_validate({"id": "r1", "task_id": "t1", "project_id": "p1"})


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunPhase.PREPARING, RunPhase.REASONING),
        (RunPhase.REASONING, RunPhase.EXECUTING),
        (RunPhase.EXECUTING, RunPhase.VERIFYING),
        (RunPhase.VERIFYING, RunPhase.FINALIZING),
    ],
)
def test_phase_advances_forward(current: RunPhase, target: RunPhase, t0: datetime) -> None:
    run = make_run(status=RunStatus.RUNNING, phase=current)
    assert run.advance_phase(target, t0).phase is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunPhase.PREPARING, RunPhase.EXECUTING),  # 不允许跳过
        (RunPhase.EXECUTING, RunPhase.REASONING),  # 不允许回退
        (RunPhase.FINALIZING, RunPhase.PREPARING),  # 终态阶段无出边
    ],
)
def test_phase_illegal_transition_raises(current: RunPhase, target: RunPhase, t0: datetime) -> None:
    run = make_run(status=RunStatus.RUNNING, phase=current)
    with pytest.raises(IllegalStateTransitionError):
        run.advance_phase(target, t0)


def test_phase_cannot_advance_when_not_running(t0: datetime) -> None:
    run = make_run(status=RunStatus.QUEUED, phase=RunPhase.PREPARING)
    with pytest.raises(InvalidStateError):
        run.advance_phase(RunPhase.REASONING, t0)


def test_constructing_waiting_without_reason_raises() -> None:
    with pytest.raises(ValueError):
        make_run(status=RunStatus.WAITING, wait_reason=None)


def test_constructing_running_with_reason_raises() -> None:
    with pytest.raises(ValueError):
        make_run(status=RunStatus.RUNNING, wait_reason=WaitReason.USER_INPUT)


@given(st.lists(st.sampled_from(_ACTIONS), max_size=40))
def test_random_walk_preserves_invariants(actions: list[str]) -> None:
    run = make_run()
    for action in actions:
        if run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
            break
        try:
            run = _apply(run, action, T0)
        except IllegalStateTransitionError:
            continue
        if run.status is RunStatus.WAITING:
            assert run.wait_reason is not None
        else:
            assert run.wait_reason is None
