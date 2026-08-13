"""Task 状态机测试：表驱动合法/非法迁移 + Hypothesis 随机游走不变量。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dataharness.domain import IllegalStateTransitionError, Task, TaskStatus, WaitReason
from dataharness.domain.ids import ProjectId, TaskId

# 模块级固定时间：@given 测试不能混用 pytest fixture，故用常量保证确定性
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def make_task(
    status: TaskStatus = TaskStatus.QUEUED,
    wait_reason: WaitReason | None = None,
) -> Task:
    return Task(
        id=TaskId("t1"),
        project_id=ProjectId("p1"),
        status=status,
        wait_reason=wait_reason,
    )


_ACTIONS = ("start", "wait", "resume", "complete", "fail", "cancel")


def _apply(task: Task, action: str, t0: datetime) -> Task:
    if action == "start":
        return task.start(t0)
    if action == "wait":
        return task.wait(WaitReason.USER_INPUT, t0)
    if action == "resume":
        return task.resume(t0)
    if action == "complete":
        return task.complete(t0)
    if action == "fail":
        return task.fail(t0)
    if action == "cancel":
        return task.cancel(t0)
    raise AssertionError(f"unknown action {action}")


def _wait_reason_for(status: TaskStatus) -> WaitReason | None:
    return WaitReason.USER_INPUT if status is TaskStatus.WAITING else None


@pytest.mark.parametrize(
    ("start_status", "action", "expected"),
    [
        (TaskStatus.QUEUED, "start", TaskStatus.ACTIVE),
        (TaskStatus.QUEUED, "cancel", TaskStatus.CANCELLED),
        (TaskStatus.ACTIVE, "wait", TaskStatus.WAITING),
        (TaskStatus.ACTIVE, "complete", TaskStatus.COMPLETED),
        (TaskStatus.ACTIVE, "fail", TaskStatus.FAILED),
        (TaskStatus.ACTIVE, "cancel", TaskStatus.CANCELLED),
        (TaskStatus.WAITING, "resume", TaskStatus.ACTIVE),
    ],
)
def test_legal_transition(
    start_status: TaskStatus, action: str, expected: TaskStatus, t0: datetime
) -> None:
    task = make_task(status=start_status, wait_reason=_wait_reason_for(start_status))
    assert _apply(task, action, t0).status is expected


@pytest.mark.parametrize(
    ("start_status", "action"),
    [
        # start 只能从 QUEUED
        (TaskStatus.ACTIVE, "start"),
        (TaskStatus.WAITING, "start"),
        (TaskStatus.COMPLETED, "start"),
        (TaskStatus.FAILED, "start"),
        (TaskStatus.CANCELLED, "start"),
        # wait 只能从 ACTIVE
        (TaskStatus.QUEUED, "wait"),
        (TaskStatus.WAITING, "wait"),
        (TaskStatus.COMPLETED, "wait"),
        (TaskStatus.FAILED, "wait"),
        (TaskStatus.CANCELLED, "wait"),
        # resume 只能从 WAITING
        (TaskStatus.QUEUED, "resume"),
        (TaskStatus.ACTIVE, "resume"),
        (TaskStatus.COMPLETED, "resume"),
        (TaskStatus.FAILED, "resume"),
        (TaskStatus.CANCELLED, "resume"),
        # complete/fail 只能从 ACTIVE
        (TaskStatus.QUEUED, "complete"),
        (TaskStatus.WAITING, "complete"),
        (TaskStatus.COMPLETED, "complete"),
        (TaskStatus.FAILED, "complete"),
        (TaskStatus.CANCELLED, "complete"),
        (TaskStatus.QUEUED, "fail"),
        (TaskStatus.WAITING, "fail"),
        (TaskStatus.COMPLETED, "fail"),
        (TaskStatus.FAILED, "fail"),
        (TaskStatus.CANCELLED, "fail"),
        # 依架构状态图：WAITING 无直接 -> CANCELLED 出边，终态不可取消
        (TaskStatus.WAITING, "cancel"),
        (TaskStatus.COMPLETED, "cancel"),
        (TaskStatus.FAILED, "cancel"),
    ],
)
def test_illegal_transition_raises(start_status: TaskStatus, action: str, t0: datetime) -> None:
    task = make_task(status=start_status, wait_reason=_wait_reason_for(start_status))
    with pytest.raises(IllegalStateTransitionError):
        _apply(task, action, t0)


def test_cancel_is_idempotent(t0: datetime) -> None:
    cancelled = make_task(TaskStatus.QUEUED).cancel(t0)
    assert cancelled.cancel(t0) is cancelled


def test_transition_returns_new_instance_and_keeps_original(t0: datetime) -> None:
    task = make_task()
    started = task.start(t0)
    assert started is not task
    assert task.status is TaskStatus.QUEUED
    assert started.status is TaskStatus.ACTIVE


def test_wait_sets_reason_and_resume_clears(t0: datetime) -> None:
    waited = make_task().start(t0).wait(WaitReason.BUDGET_EXHAUSTED, t0)
    assert waited.wait_reason is WaitReason.BUDGET_EXHAUSTED
    assert waited.resume(t0).wait_reason is None


def test_constructing_waiting_without_reason_raises() -> None:
    with pytest.raises(ValueError):
        make_task(status=TaskStatus.WAITING, wait_reason=None)


def test_constructing_active_with_reason_raises() -> None:
    with pytest.raises(ValueError):
        make_task(status=TaskStatus.ACTIVE, wait_reason=WaitReason.USER_INPUT)


@given(st.lists(st.sampled_from(_ACTIONS), max_size=40))
def test_random_walk_preserves_invariants(actions: list[str]) -> None:
    task = make_task()
    for action in actions:
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            break
        try:
            task = _apply(task, action, T0)
        except IllegalStateTransitionError:
            continue
        if task.status is TaskStatus.WAITING:
            assert task.wait_reason is not None
        else:
            assert task.wait_reason is None
