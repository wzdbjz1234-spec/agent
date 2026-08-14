"""Host 侧 Execution/Integrity/Evidence 三道 Finding Gate。

Agent 只能写入 DRAFT Finding。此模块是唯一把草稿状态持久化为 VERIFIED、WARNING 或
REJECTED 的应用服务；每一道 Gate 都从 Runtime、Snapshot 和 Workspace 事实源重新核对，
不信任模型返回的自然语言、路径或旧的内存对象。
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from dataharness.domain import (
    ArtifactId,
    DatasetId,
    EvidenceKind,
    FileVersionId,
    Finding,
    FindingId,
    FindingStatus,
    Run,
    StepId,
    TaskId,
)
from dataharness.projects import ProjectCorpus
from dataharness.storage import RecordNotFoundError, SqliteRuntimeStore
from dataharness.workspace import (
    PublicationStatus,
    VirtualWorkspace,
    WorkspaceBridge,
    WorkspaceResource,
)

from .models import AnalysisSummary
from .warnings import DataWarning, DataWarningDetector


class GateName(StrEnum):
    """三个 Gate 的稳定名称，写入验证结果而不是原始异常文本。"""

    EXECUTION = "ExecutionGate"
    INTEGRITY = "IntegrityGate"
    EVIDENCE = "EvidenceGate"


class GateReport(BaseModel):
    """单道 Gate 的脱敏结果。"""

    model_config = ConfigDict(frozen=True)

    gate: GateName
    passed: bool
    messages: tuple[str, ...] = ()


class FindingVerificationResult(BaseModel):
    """Host 验证后的 Finding 与所有 Gate 证据。"""

    model_config = ConfigDict(frozen=True)

    finding: Finding
    reports: tuple[GateReport, ...]
    warnings: tuple[DataWarning, ...] = ()


class VerificationError(ValueError):
    """验证请求或事实源不满足 Gate 前置条件。"""


class ExecutionGate:
    """检查 Sandbox 进程结果和输出声明，不判断业务结论真假。"""

    @staticmethod
    def check(summary: AnalysisSummary) -> GateReport:
        """只有明确成功、零退出码且输出元数据完整的 Step 才能通过。"""
        errors: list[str] = []
        if str(summary.status) != "SUCCEEDED":
            errors.append("Step 未以成功状态结束")
        if summary.exit_code not in (0, None):
            errors.append("Step 退出码非零")
        for output in summary.outputs:
            if not output.resource_id or not output.content_hash or output.byte_size < 0:
                errors.append(f"输出 {output.name} 的资源元数据不完整")
        return GateReport(gate=GateName.EXECUTION, passed=not errors, messages=tuple(errors))


class IntegrityGate:
    """重新计算 Snapshot 输入和 Workspace 输出 hash，防止旧结果或漂移文件冒充证据。"""

    def __init__(
        self,
        store: SqliteRuntimeStore,
        corpus: ProjectCorpus,
        workspace: VirtualWorkspace,
        bridge: WorkspaceBridge | None = None,
    ) -> None:
        self._store = store
        self._corpus = corpus
        self._workspace = workspace
        self._bridge = bridge

    def check(self, run: Run, summary: AnalysisSummary) -> GateReport:
        errors: list[str] = []
        try:
            with self._store.unit_of_work() as uow:
                stored_run = uow.repo.get_run(run.id).value
                snapshot = uow.repo.get_snapshot(run.project_snapshot_id)
                step = uow.repo.get_step(summary.step_id).value
            if stored_run.project_snapshot_id != run.project_snapshot_id:
                errors.append("Run 的固定 Snapshot 已发生漂移")
            if step.run_id != run.id or str(step.status) != "SUCCEEDED":
                errors.append("分析摘要的 Step 不属于当前 Run 或未成功完成")
            for reference in summary.input_refs:
                entry = snapshot.entry_for(reference.file_version_id)
                if entry is None or entry.content_hash != reference.content_hash:
                    errors.append("分析输入不属于 Run 的固定 Snapshot 或 hash 不匹配")
                    continue
                try:
                    self._corpus.open_resource(snapshot.id, reference.file_version_id)
                except Exception:
                    errors.append("分析输入原件无法通过 hash 完整性校验")
            for output in summary.outputs:
                try:
                    resource = self._output_resource(run, summary, output)
                    if (
                        resource.content_hash != output.content_hash
                        or resource.byte_size != output.byte_size
                    ):
                        errors.append(f"输出 {output.name} 的 hash 或大小已漂移")
                except Exception:
                    errors.append(f"输出 {output.name} 不存在或不在受控发布位置")
        except RecordNotFoundError:
            errors.append("Run、ProjectSnapshot 或 Step 不存在")
        return GateReport(gate=GateName.INTEGRITY, passed=not errors, messages=tuple(errors))

    def _output_resource(self, run: Run, summary: AnalysisSummary, output):
        """只通过 Workspace 的受控接口读取输出，不接受 API 传入的宿主路径。"""
        if output.available:
            if self._bridge is None:
                raise VerificationError("正式输出缺少发布桥接器")
            record = next(
                (
                    item
                    for item in self._bridge.available(run.project_id)
                    if item.resource_id == output.resource_id
                    and item.output_name == output.name
                    and item.step_id == summary.step_id
                    and item.status == PublicationStatus.AVAILABLE
                ),
                None,
            )
            if record is None:
                raise VerificationError("正式输出发布记录不存在")
            return self._workspace.published_resource(record)
        path = self._workspace.staging_path(
            run.project_id, TaskId(run.task_id), summary.step_id, output.name
        )
        if not path.is_file():
            raise VerificationError("staging 输出不存在")
        from dataharness.domain import compute_content_hash

        data = path.read_bytes()
        return WorkspaceResource(
            project_id=run.project_id,
            namespace="staging",
            resource_id=str(summary.step_id),
            name=output.name,
            content_hash=compute_content_hash(data),
            byte_size=len(data),
        )


class EvidenceGate:
    """确认 Finding 证据属于当前 Task/Run/Snapshot 且内容仍可复核。"""

    def __init__(
        self,
        store: SqliteRuntimeStore,
        corpus: ProjectCorpus,
        workspace: VirtualWorkspace,
        bridge: WorkspaceBridge | None = None,
    ) -> None:
        self._store = store
        self._corpus = corpus
        self._workspace = workspace
        self._bridge = bridge

    def check(self, finding: Finding) -> GateReport:
        errors: list[str] = []
        notices: list[str] = []
        candidate = finding.candidate
        try:
            with self._store.unit_of_work() as uow:
                task = uow.repo.get_task(candidate.task_id).value
                run = uow.repo.get_run(candidate.run_id).value
                snapshot = uow.repo.get_snapshot(candidate.project_snapshot_id)
                if task.project_id != run.project_id or run.task_id != task.id:
                    errors.append("Finding 的 Task/Run 归属不一致")
                if run.project_snapshot_id != snapshot.id or snapshot.project_id != task.project_id:
                    errors.append("Finding 不属于当前 ProjectSnapshot")
                if candidate.coverage_report_id is not None:
                    coverage = uow.repo.get_coverage_report(candidate.coverage_report_id)
                    if coverage.snapshot_id != snapshot.id:
                        errors.append("FULL_PROJECT 覆盖报告与 Snapshot 不符")
                    elif coverage.has_uncovered():
                        # 覆盖缺口必须披露，但架构明确要求它成为可见 Warning，不能把
                        # 已有证据全部判成 REJECTED；最终回答需携带此处的审计说明。
                        notices.append("FULL_PROJECT 覆盖报告存在未覆盖文件")
                for evidence in candidate.evidence:
                    self._check_evidence(uow.repo, evidence, task.id, run, snapshot)
        except (RecordNotFoundError, VerificationError) as error:
            errors.append(str(error))
        except Exception:
            # 原件损坏、路径越界等底层错误不应穿透 HTTP；Gate 只输出稳定的拒绝原因。
            errors.append("证据无法从受控事实源复核")
        return GateReport(
            gate=GateName.EVIDENCE,
            passed=not errors,
            messages=tuple([*errors, *notices]),
        )

    def _check_evidence(self, repo, evidence, task_id: TaskId, run: Run, snapshot) -> None:
        if not evidence.target_id or not evidence.content_hash:
            raise VerificationError("证据缺少稳定 ID 或内容 hash")
        if evidence.kind == EvidenceKind.FILE:
            entry = snapshot.entry_for(FileVersionId(evidence.target_id))
            if entry is None or entry.content_hash != evidence.content_hash:
                raise VerificationError("文件证据不属于当前 Snapshot 或 hash 不匹配")
            self._corpus.open_resource(snapshot.id, FileVersionId(evidence.target_id))
            return
        if evidence.kind == EvidenceKind.STEP:
            step = repo.get_step(StepId(evidence.target_id)).value
            if step.run_id != run.id or str(step.status) != "SUCCEEDED":
                raise VerificationError("Step 证据不属于当前 Run 或未成功完成")
            return
        if evidence.kind == EvidenceKind.DATASET:
            resource = repo.get_dataset(DatasetId(evidence.target_id))
            if (
                resource.project_id != run.project_id
                or resource.task_id != task_id
                or resource.run_id != run.id
                or resource.content_hash != evidence.content_hash
            ):
                raise VerificationError("Dataset 证据归属或 hash 不匹配")
            self._check_published_resource(resource.id, resource.name, run)
            return
        if evidence.kind == EvidenceKind.ARTIFACT:
            resource = repo.get_artifact(ArtifactId(evidence.target_id))
            if (
                resource.project_id != run.project_id
                or resource.task_id != task_id
                or resource.run_id != run.id
                or resource.content_hash != evidence.content_hash
            ):
                raise VerificationError("Artifact 证据归属或 hash 不匹配")
            self._check_published_resource(resource.id, resource.name, run)
            return
        raise VerificationError("未知证据类型")

    def _check_published_resource(self, resource_id, name: str, run: Run) -> None:
        """正式资源必须能从 AVAILABLE 发布记录重读；数据库记录本身不是文件事实。"""
        if self._bridge is None:
            raise VerificationError("正式证据缺少发布桥接器")
        if not any(
            item.resource_id == str(resource_id)
            and item.output_name == name
            and item.status == PublicationStatus.AVAILABLE
            for item in self._bridge.available(run.project_id)
        ):
            raise VerificationError("正式证据对应的 Workspace 文件不可用")


class VerificationService:
    """唯一执行 Host Gate 并持久化 Finding 终态的服务。"""

    def __init__(
        self,
        store: SqliteRuntimeStore,
        corpus: ProjectCorpus,
        workspace: VirtualWorkspace,
        bridge: WorkspaceBridge | None = None,
    ) -> None:
        self._store = store
        self._execution = ExecutionGate()
        self._integrity = IntegrityGate(store, corpus, workspace, bridge)
        self._evidence = EvidenceGate(store, corpus, workspace, bridge)

    def verify(
        self,
        finding_id: FindingId,
        summaries: Iterable[AnalysisSummary] = (),
    ) -> FindingVerificationResult:
        """按 Gate 顺序复核并 CAS 保存 Finding 终态；重复调用是幂等读取。"""
        with self._store.unit_of_work() as uow:
            stored = uow.repo.get_finding(finding_id)
            finding = stored.value
            run = uow.repo.get_run(finding.candidate.run_id).value
        if finding.status != FindingStatus.DRAFT:
            return FindingVerificationResult(finding=finding, reports=())

        summaries_tuple = tuple(summaries)
        execution_reports = tuple(self._execution.check(item) for item in summaries_tuple)
        integrity_reports = tuple(self._integrity.check(run, item) for item in summaries_tuple)
        reports = (*execution_reports, *integrity_reports, self._evidence.check(finding))
        warnings = tuple(
            warning
            for summary in summaries_tuple
            for warning in DataWarningDetector.detect(summary.statistics)
        )
        failed = any(not report.passed for report in reports)
        if failed:
            updated = finding.reject()
            event = "FINDING_REJECTED"
        elif warnings:
            updated = finding.mark_warning()
            event = "FINDING_WARNING"
        else:
            updated = finding.verify()
            event = "FINDING_VERIFIED"
        with self._store.unit_of_work() as uow:
            saved = uow.repo.save_finding(updated, stored.version, event).value
            if finding.candidate.coverage_report_id is not None:
                coverage = uow.repo.get_coverage_report(finding.candidate.coverage_report_id)
                if coverage.has_uncovered():
                    # 覆盖缺口进入事件序列，UI/回答层可以据此披露，而不必解析模型摘要。
                    uow.repo.append_event(
                        "finding",
                        str(finding.id),
                        "FINDING_COVERAGE_NOTICE",
                        saved.verified_at or saved.candidate.created_at,
                        {
                            "coverage_report_id": str(coverage.id),
                            "uncovered_files": coverage.total - coverage.processed,
                        },
                    )
            if warnings:
                uow.repo.append_event(
                    "finding",
                    str(finding.id),
                    "FINDING_DATA_WARNINGS",
                    saved.verified_at or saved.candidate.created_at,
                    {"warning_count": len(warnings)},
                )
        return FindingVerificationResult(finding=saved, reports=reports, warnings=warnings)
