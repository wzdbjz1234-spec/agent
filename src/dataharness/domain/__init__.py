"""纯领域层：领域模型、状态机、不变量与领域错误。

本层不依赖 FastAPI、PydanticAI、OpenSandbox、sqlite3 或遥测 SDK，也不执行 I/O。
所有对象为不可变（frozen）值对象与聚合，状态迁移通过迁移表驱动并返回新实例。
"""

from __future__ import annotations

from .artifact import Artifact, Dataset
from .clock import utcnow
from .conversation import ConversationMessage, MessageRole
from .enums import (
    CoverageItemStatus,
    FileVersionStatus,
    FindingStatus,
    ProjectStatus,
    RunPhase,
    RunStatus,
    StepFailureKind,
    StepStatus,
    TaskStatus,
    WaitReason,
)
from .errors import (
    DomainError,
    FileVersionImmutableError,
    IllegalStateTransitionError,
    InvalidEvidenceError,
    InvalidStateError,
)
from .finding import EvidenceKind, EvidenceRef, Finding, FindingCandidate
from .hashes import compute_content_hash
from .ids import (
    ArtifactId,
    ContentHash,
    CoverageReportId,
    DatasetId,
    FileId,
    FileVersionId,
    FindingId,
    LineageId,
    MessageId,
    ProjectId,
    RunId,
    SessionId,
    SnapshotId,
    StepId,
    TaskId,
)
from .lineage import Lineage, ResourceKind, ResourceRef
from .project import (
    CoverageItem,
    Project,
    ProjectCoverageReport,
    ProjectFile,
    ProjectFileVersion,
    ProjectSnapshot,
    SnapshotEntry,
)
from .run import Run
from .session import Session
from .state_machine import TransitionTable, check_transition
from .step import AnalysisStep
from .task import Task

__all__ = [
    "AnalysisStep",
    "Artifact",
    "ArtifactId",
    "ContentHash",
    "CoverageItem",
    "CoverageItemStatus",
    "CoverageReportId",
    "Dataset",
    "DatasetId",
    "DomainError",
    "EvidenceKind",
    "EvidenceRef",
    "FileId",
    "FileVersionId",
    "FileVersionImmutableError",
    "FileVersionStatus",
    "Finding",
    "FindingCandidate",
    "FindingId",
    "FindingStatus",
    "IllegalStateTransitionError",
    "InvalidEvidenceError",
    "InvalidStateError",
    "Lineage",
    "LineageId",
    "MessageId",
    "ConversationMessage",
    "MessageRole",
    "Project",
    "ProjectCoverageReport",
    "ProjectFile",
    "ProjectFileVersion",
    "ProjectId",
    "ProjectSnapshot",
    "ProjectStatus",
    "ResourceKind",
    "ResourceRef",
    "Run",
    "RunId",
    "RunPhase",
    "RunStatus",
    "Session",
    "SessionId",
    "SnapshotEntry",
    "SnapshotId",
    "StepFailureKind",
    "StepId",
    "StepStatus",
    "Task",
    "TaskId",
    "TaskStatus",
    "TransitionTable",
    "WaitReason",
    "check_transition",
    "compute_content_hash",
    "utcnow",
]
