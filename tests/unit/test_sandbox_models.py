"""SandboxSpec、挂载与请求的 fail-closed 单元测试。"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dataharness.domain import ProjectId, RunId, SnapshotId, TaskId
from dataharness.sandbox import SandboxMount, SandboxResources, SandboxSpec

DIGEST = "sha256:" + "a" * 64


def _kwargs() -> dict[str, Any]:
    return {
        "project_id": ProjectId("project-1"),
        "task_id": TaskId("task-1"),
        "run_id": RunId("run-1"),
        "project_snapshot_id": SnapshotId("snapshot-1"),
        "image_digest": DIGEST,
        "mounts": (
            SandboxMount(source_ref="snapshot:snapshot-1", target="/project", read_only=True),
            SandboxMount(source_ref="task:task-1:working", target="/task/working", read_only=False),
            SandboxMount(source_ref="task:task-1:staging", target="/task/staging", read_only=False),
        ),
        "resources": SandboxResources(
            memory_mb=128, disk_mb=256, max_output_bytes=1024, step_timeout_seconds=10
        ),
    }


@pytest.mark.parametrize(
    "override",
    [
        {"image_digest": "python:3.12"},
        {"network_enabled": True},
        {"privileged": True},
        {"root_read_only": False},
        {"user": "root"},
        {
            "mounts": (
                SandboxMount(source_ref="snapshot:snapshot-1", target="/project", read_only=True),
                SandboxMount(
                    source_ref="task:task-1:working", target="/task/working", read_only=False
                ),
                SandboxMount(
                    source_ref="runtime:runtime.db", target="/task/staging", read_only=False
                ),
            )
        },
    ],
)
def test_spec_rejects_any_security_relaxation_or_wrong_mount(override: dict[str, Any]) -> None:
    """无法通过多余/错误挂载间接暴露 Runtime、Privacy、Docker 或其他 Task。"""
    with pytest.raises(ValidationError):
        SandboxSpec(**(_kwargs() | override))


def test_mount_rejects_non_v1_target() -> None:
    """任意宿主路径、Docker socket 和 privacy 路径均不是允许的容器挂载目标。"""
    with pytest.raises(ValidationError):
        SandboxMount(source_ref="privacy:task-1", target="/var/run/docker.sock", read_only=True)
