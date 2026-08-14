"""Finding 领域对象。

Agent 只能提交 :class:`FindingCandidate`；只有 Host Verification Gate 能把正式
:class:`Finding` 从 DRAFT 晋级为 VERIFIED/WARNING/REJECTED。每个候选必须引用
至少一条可追溯的证据（文件版本、步骤、数据集或产物）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .clock import utcnow
from .enums import FindingStatus
from .errors import InvalidEvidenceError
from .ids import ContentHash, CoverageReportId, FindingId, RunId, SnapshotId, TaskId
from .state_machine import check_transition


class EvidenceKind(StrEnum):
    """证据来源类型。"""

    FILE = "FILE"
    STEP = "STEP"
    DATASET = "DATASET"
    ARTIFACT = "ARTIFACT"


class EvidenceRef(BaseModel):
    """一条证据引用：类型 + 稳定 ID + 内容哈希 + 可选定位。"""

    model_config = ConfigDict(frozen=True)

    kind: EvidenceKind
    target_id: str
    content_hash: ContentHash
    locator: str | None = None


class FindingCandidate(BaseModel):
    """Agent 提交的结构化结论候选，必须绑定 Task/Run/Snapshot 并引用证据。"""

    model_config = ConfigDict(frozen=True)

    task_id: TaskId
    run_id: RunId
    project_snapshot_id: SnapshotId
    summary: str
    evidence: tuple[EvidenceRef, ...]
    # FULL_PROJECT 结论必须把覆盖事实作为结构化引用持久化，不能只在自然语言中声称“已全量分析”。
    coverage_report_id: CoverageReportId | None = None
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _require_evidence(self) -> FindingCandidate:
        """最终结论必须至少引用一条有效证据。"""
        if not self.evidence:
            raise InvalidEvidenceError("FindingCandidate 必须至少引用一条证据")
        return self


# Finding 正式状态迁移表：只有 Host Gate 能触发，且终态不可回退
FINDING_TRANSITIONS: dict[FindingStatus, frozenset[FindingStatus]] = {
    FindingStatus.DRAFT: frozenset(
        {FindingStatus.VERIFIED, FindingStatus.WARNING, FindingStatus.REJECTED}
    ),
}


class Finding(BaseModel):
    """正式结论，由 Host Verification Gate 决定正式状态。"""

    model_config = ConfigDict(frozen=True)

    id: FindingId
    candidate: FindingCandidate
    status: FindingStatus = FindingStatus.DRAFT
    verified_at: datetime | None = None

    def verify(self, at: datetime | None = None) -> Finding:
        """DRAFT -> VERIFIED（仅 Host Gate 调用）。"""
        check_transition(FINDING_TRANSITIONS, self.status, FindingStatus.VERIFIED, "Finding")
        return self.model_copy(
            update={"status": FindingStatus.VERIFIED, "verified_at": at or utcnow()}
        )

    def mark_warning(self, at: datetime | None = None) -> Finding:
        """DRAFT -> WARNING（仅 Host Gate 调用）。"""
        check_transition(FINDING_TRANSITIONS, self.status, FindingStatus.WARNING, "Finding")
        return self.model_copy(
            update={"status": FindingStatus.WARNING, "verified_at": at or utcnow()}
        )

    def reject(self, at: datetime | None = None) -> Finding:
        """DRAFT -> REJECTED（仅 Host Gate 调用）。"""
        check_transition(FINDING_TRANSITIONS, self.status, FindingStatus.REJECTED, "Finding")
        return self.model_copy(
            update={"status": FindingStatus.REJECTED, "verified_at": at or utcnow()}
        )
