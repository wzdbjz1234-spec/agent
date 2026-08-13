"""AnalysisRuntime 的稳定请求、结果和能力返回值。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataharness.domain import ContentHash, FileId, FileVersionId, ProjectId, StepId
from dataharness.sandbox import ExecutionKind, ExecutionStatus
from dataharness.workspace import PublicationKind


class AnalysisMode(StrEnum):
    """跨文件分析的显式覆盖模式。"""

    RELEVANT = "RELEVANT"
    FULL_PROJECT = "FULL_PROJECT"


class InputReference(BaseModel):
    """当前 Snapshot 中的稳定输入引用；不携带宿主路径或原始正文。"""

    model_config = ConfigDict(frozen=True)

    file_version_id: FileVersionId
    file_id: FileId
    content_hash: ContentHash
    locator: dict[str, Any] = Field(default_factory=dict)


class OutputSpec(BaseModel):
    """一项写入当前 Step staging 的预期输出。"""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=255)
    kind: PublicationKind

    @model_validator(mode="after")
    def _leaf_name(self) -> OutputSpec:
        if self.name in {".", ".."} or "/" in self.name or "\\" in self.name:
            raise ValueError("输出名必须是 staging 内的单个文件名")
        return self


class AnalysisRequest(BaseModel):
    """每个 AnalysisStep 的可审计执行声明。"""

    model_config = ConfigDict(frozen=True)

    kind: ExecutionKind
    code: str = Field(min_length=1, max_length=1_000_000)
    inputs: tuple[InputReference, ...] = ()
    expected_outputs: tuple[OutputSpec, ...] = ()
    timeout_seconds: int = Field(gt=0)
    budget_units: int = Field(default=1, gt=0)
    staging_ref: str = Field(min_length=1, max_length=512)
    mode: AnalysisMode = AnalysisMode.RELEVANT

    @model_validator(mode="after")
    def _validate_staging_ref(self) -> AnalysisRequest:
        """只接受由当前 Task 派生的逻辑 staging 引用，不接受 Host 路径。"""
        if (
            not self.staging_ref.startswith("task:")
            or not self.staging_ref.endswith(":staging")
            or "/" in self.staging_ref
            or "\\" in self.staging_ref
            or ".." in self.staging_ref
        ):
            raise ValueError("AnalysisRequest staging_ref 必须是受控的 Task staging 引用")
        return self


class ProjectFileView(BaseModel):
    """文件列表的有界元数据视图。"""

    model_config = ConfigDict(frozen=True)

    project_id: ProjectId
    file_id: FileId
    file_version_id: FileVersionId
    name: str
    status: str
    content_hash: ContentHash | None
    media_type: str | None
    byte_size: int | None


class ProjectFileInspection(BaseModel):
    """项目文件受控检查结果，不包含宿主路径。"""

    model_config = ConfigDict(frozen=True)

    file_version_id: FileVersionId
    name: str
    media_type: str
    content_hash: ContentHash
    byte_size: int
    excerpt: str
    truncated: bool


class OutputReference(BaseModel):
    """staging 或已发布输出的稳定引用。"""

    model_config = ConfigDict(frozen=True)

    resource_id: str
    name: str
    kind: PublicationKind
    content_hash: ContentHash
    byte_size: int
    available: bool = False


class OutputInspection(BaseModel):
    """对当前 Task staging/正式输出的有界检查结果。"""

    model_config = ConfigDict(frozen=True)

    step_id: StepId
    name: str
    content_hash: ContentHash
    byte_size: int
    excerpt: str
    truncated: bool
    available: bool = False


class AnalysisSummary(BaseModel):
    """有界的执行摘要；完整输出仅在 Workspace staging 中保留。"""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    step_id: StepId
    request_hash: ContentHash
    status: ExecutionStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    code_hash: ContentHash
    input_refs: tuple[InputReference, ...]
    outputs: tuple[OutputReference, ...] = ()
    output_schema: dict[str, object] = Field(default_factory=dict, alias="schema")
    statistics: dict[str, int | float] = Field(default_factory=dict)
    resource_stats: dict[str, int | float] = Field(default_factory=dict)


class FullProjectResult(BaseModel):
    """FULL_PROJECT 的批处理结果及覆盖报告。"""

    model_config = ConfigDict(frozen=True)

    coverage_report_id: str
    total_files: int
    processed_files: int
    uncovered_files: int
    batches: tuple[AnalysisSummary, ...]
