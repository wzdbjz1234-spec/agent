"""Sandbox 边界的稳定值对象；不泄漏 OpenSandbox SDK 或宿主路径。"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataharness.domain import ProjectId, RunId, SnapshotId, StepId, TaskId, utcnow

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}$")


class ExecutionKind(StrEnum):
    """允许在镜像内执行的不可信载荷类别，不提供通用 shell。"""

    PYTHON = "PYTHON"
    SQL = "SQL"
    SKILL = "SKILL"


class ExecutionStatus(StrEnum):
    """一次独立 Step 进程的稳定结果分类。"""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    SANDBOX_LOST = "SANDBOX_LOST"
    OUTPUT_LIMIT_EXCEEDED = "OUTPUT_LIMIT_EXCEEDED"


class SandboxMount(BaseModel):
    """一项已解析的受控挂载；``source_ref`` 是不含宿主路径的内部资源引用。"""

    model_config = ConfigDict(frozen=True)

    source_ref: str = Field(min_length=1, max_length=512)
    target: str
    read_only: bool

    @model_validator(mode="after")
    def _validate_target(self) -> SandboxMount:
        """只允许三种 V1 挂载目标，从结构上排除数据库、凭据和任意 Host 路径。"""
        allowed = {"/project", "/task/working", "/task/staging"}
        if self.target not in allowed:
            raise ValueError("Sandbox 挂载目标不在 V1 白名单内")
        if not self.target.startswith("/") or ".." in self.target:
            raise ValueError("Sandbox 挂载目标必须是受控绝对路径")
        return self


class SandboxResources(BaseModel):
    """创建和 attestation 必须一致的资源上限。"""

    model_config = ConfigDict(frozen=True)

    cpu_limit: float | None = Field(default=None, gt=0)
    memory_mb: int = Field(gt=0)
    disk_mb: int = Field(gt=0)
    max_processes: int = Field(default=32, gt=0)
    max_output_bytes: int = Field(gt=0)
    step_timeout_seconds: int = Field(gt=0)


class SandboxSpec(BaseModel):
    """Run-scoped 安全运行规格；创建后不得改用另一个镜像或挂载集合。"""

    model_config = ConfigDict(frozen=True)

    project_id: ProjectId
    task_id: TaskId
    run_id: RunId
    project_snapshot_id: SnapshotId
    runtime: str = "secure-analysis"
    image_digest: str
    network_enabled: bool = False
    privileged: bool = False
    root_read_only: bool = True
    user: str = "sandbox"
    mounts: tuple[SandboxMount, ...]
    resources: SandboxResources

    @model_validator(mode="after")
    def _validate_security_contract(self) -> SandboxSpec:
        """安全策略不接受开发期宽松值；不符的规格在调用 Provider 前直接失败。"""
        if self.runtime != "secure-analysis":
            raise ValueError("V1 仅允许 secure-analysis 运行时")
        if not _DIGEST.fullmatch(self.image_digest):
            raise ValueError("镜像必须使用锁定的 sha256 digest")
        if (
            self.network_enabled
            or self.privileged
            or not self.root_read_only
            or self.user != "sandbox"
        ):
            raise ValueError("Sandbox 必须断网、非特权、只读根且使用 sandbox 用户")
        mounts = {item.target: item for item in self.mounts}
        if set(mounts) != {"/project", "/task/working", "/task/staging"}:
            raise ValueError("必须且只能挂载 project、working 和 staging 三个受控目录")
        if not mounts["/project"].read_only:
            raise ValueError("ProjectSnapshot 必须只读挂载")
        if mounts["/task/working"].read_only or mounts["/task/staging"].read_only:
            raise ValueError("当前 Task 的 working/staging 必须可写")
        expected = {
            "/project": f"snapshot:{self.project_snapshot_id}",
            "/task/working": f"task:{self.task_id}:working",
            "/task/staging": f"task:{self.task_id}:staging",
        }
        if {target: mount.source_ref for target, mount in mounts.items()} != expected:
            raise ValueError("挂载必须精确绑定当前 Run 固定 Snapshot 与当前 Task 写域")
        return self


class SandboxAttestation(BaseModel):
    """Provider 从实际 Sandbox 读取并返回的安全配置快照。"""

    model_config = ConfigDict(frozen=True)

    image_digest: str
    network_enabled: bool
    privileged: bool
    root_read_only: bool
    user: str
    mounts: tuple[SandboxMount, ...]
    resources: SandboxResources


class SandboxLease(BaseModel):
    """当前 Run 可替换 Sandbox 的不透明 lease，不暴露 SDK 对象或 Host 路径。"""

    model_config = ConfigDict(frozen=True)

    sandbox_id: str
    run_id: RunId
    task_id: TaskId
    project_id: ProjectId
    project_snapshot_id: SnapshotId
    image_digest: str
    created_at: datetime = Field(default_factory=utcnow)


class ExecutionRequest(BaseModel):
    """单一 Step 的不可信执行请求；代码只会交给 Sandbox Provider。"""

    model_config = ConfigDict(frozen=True)

    step_id: StepId
    kind: ExecutionKind
    code: str = Field(min_length=1, max_length=1_000_000)
    timeout_seconds: int = Field(gt=0)
    expected_output_names: tuple[str, ...] = ()
    input_refs: tuple[str, ...] = ()
    staging_ref: str | None = None
    budget_units: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_output_names(self) -> ExecutionRequest:
        """输出名、输入引用和 staging 引用都不能携带 Host 路径。"""
        for name in self.expected_output_names:
            if not name or "/" in name or "\\" in name or name in {".", ".."}:
                raise ValueError("输出名必须是 staging 内的单个文件名")
        for reference in self.input_refs:
            if not reference or "/" in reference or "\\" in reference or ".." in reference:
                raise ValueError("输入引用必须是稳定的资源 ID")
        if self.staging_ref is not None and (
            not self.staging_ref.startswith("task:")
            or "/" in self.staging_ref
            or "\\" in self.staging_ref
            or ".." in self.staging_ref
        ):
            raise ValueError("staging 引用必须是受控的 Task 资源引用")
        return self


class ExecutionResult(BaseModel):
    """已截断的独立进程执行结果；完整结果只能经受控 staging 发布。"""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    status: ExecutionStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int = Field(ge=0)
    output_schema: dict[str, object] = Field(default_factory=dict, alias="schema")
    statistics: dict[str, int | float] = Field(default_factory=dict)
    resource_stats: dict[str, int | float] = Field(default_factory=dict)
    process_id: str | None = None
