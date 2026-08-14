"""把窄工具调用转换为可审计、可发布的 AnalysisStep。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import datetime

from dataharness.domain import (
    AnalysisStep,
    Artifact,
    ArtifactId,
    ContentHash,
    CoverageReportId,
    Dataset,
    DatasetId,
    EvidenceKind,
    EvidenceRef,
    FileVersionId,
    FileVersionStatus,
    Finding,
    FindingCandidate,
    FindingId,
    Lineage,
    LineageId,
    ProjectCoverageReport,
    ProjectId,
    ProjectSnapshot,
    ResourceKind,
    ResourceRef,
    RunId,
    SnapshotId,
    StepFailureKind,
    StepId,
    TaskId,
    compute_content_hash,
    utcnow,
)
from dataharness.idgen import IdFactory, UuidIdFactory
from dataharness.projects import ProjectCorpus
from dataharness.sandbox import (
    ExecutionKind,
    ExecutionRequest,
    ExecutionStatus,
    SandboxCancelledError,
    SandboxError,
    SandboxLease,
    SandboxLostError,
    SandboxOutputLimitError,
    SandboxProvider,
    SandboxTimeoutError,
)
from dataharness.storage import IdempotencyRecord, RecordNotFoundError, SqliteRuntimeStore
from dataharness.workspace import (
    PublicationKind,
    PublicationStatus,
    VirtualWorkspace,
    WorkspaceBridge,
)

from .errors import AnalysisBudgetError, AnalysisCircuitOpenError, AnalysisContextError
from .models import (
    AnalysisMode,
    AnalysisRequest,
    AnalysisSummary,
    FullProjectResult,
    InputReference,
    OutputInspection,
    OutputReference,
    OutputSpec,
    ProjectFileInspection,
    ProjectFileView,
)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class AnalysisRuntime:
    """单个 Run 的分析执行深模块。

    它只依赖 SandboxProvider、ProjectCorpus、Workspace 和 RuntimeRepository 的公开行为。
    生成的 Python/SQL/Skill 内容不会在 Host 解析或执行；Host 只校验引用、记录 Step、保存
    有界摘要，并将 Sandbox 返回结果写入当前 Task 的 staging。
    """

    def __init__(
        self,
        store: SqliteRuntimeStore,
        corpus: ProjectCorpus,
        workspace: VirtualWorkspace,
        sandbox: SandboxProvider,
        lease: SandboxLease,
        *,
        bridge: WorkspaceBridge | None = None,
        id_factory: IdFactory | None = None,
        clock: Callable[[], datetime] = utcnow,
        max_summary_chars: int = 4096,
        max_consecutive_failures: int = 3,
        max_budget_units: int = 100,
    ) -> None:
        self._store = store
        self._corpus = corpus
        self._workspace = workspace
        self._sandbox = sandbox
        self._lease = lease
        self._bridge = bridge
        self._ids = id_factory or UuidIdFactory()
        self._clock = clock
        self._max_summary_chars = max_summary_chars
        self._max_consecutive_failures = max_consecutive_failures
        self._max_budget_units = max_budget_units
        self._cache: dict[str, AnalysisSummary] = {}
        self._failure_counts: dict[str, int] = {}

    def _context(self) -> tuple[TaskId, RunId, ProjectId, SnapshotId]:
        """从 lease 得到不可伪造的 Run 上下文，并确认 Runtime DB 中的 Run 一致。"""
        with self._store.unit_of_work() as uow:
            run = uow.repo.get_run(self._lease.run_id).value
        if (
            run.task_id != self._lease.task_id
            or run.project_id != self._lease.project_id
            or run.project_snapshot_id != self._lease.project_snapshot_id
        ):
            raise AnalysisContextError("Sandbox lease 与 Runtime Run 的固定上下文不一致")
        return run.task_id, run.id, run.project_id, run.project_snapshot_id

    def _snapshot(self) -> ProjectSnapshot:
        """返回 lease 固定的 Snapshot；不读取项目最新版本替代它。"""
        with self._store.unit_of_work() as uow:
            return uow.repo.get_snapshot(self._lease.project_snapshot_id)

    def _validate_inputs(self, inputs: tuple[InputReference, ...]) -> None:
        """校验所有输入属于当前 Snapshot 且 hash 与事实源一致。"""
        snapshot = self._snapshot()
        for reference in inputs:
            entry = snapshot.entry_for(reference.file_version_id)
            if (
                entry is None
                or entry.status != FileVersionStatus.READY
                or entry.file_id != reference.file_id
                or entry.content_hash != reference.content_hash
            ):
                raise AnalysisContextError("分析输入不是当前 Run Snapshot 的 READY 版本")

    @staticmethod
    def _normalized_request(request: AnalysisRequest, image_digest: str) -> dict[str, object]:
        """去除 step_id 后稳定序列化，作为幂等和熔断键的事实来源。"""
        return {
            "kind": request.kind,
            "code_hash": str(compute_content_hash(request.code.encode("utf-8"))),
            "inputs": [item.model_dump(mode="json") for item in request.inputs],
            "expected_outputs": [item.model_dump(mode="json") for item in request.expected_outputs],
            "timeout_seconds": request.timeout_seconds,
            "budget_units": request.budget_units,
            "mode": request.mode,
            "image_digest": image_digest,
        }

    def _request_hash(self, request: AnalysisRequest) -> ContentHash:
        normalized = self._normalized_request(request, self._lease.image_digest)
        payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return compute_content_hash(payload)

    def _default_staging_ref(self) -> str:
        return f"task:{self._lease.task_id}:staging"

    def _validate_staging_ref(self, staging_ref: str) -> None:
        if staging_ref != self._default_staging_ref():
            raise AnalysisContextError("staging 引用必须绑定当前 Task")

    def _idempotency_key(self, request_hash: ContentHash) -> str:
        """返回绑定 Run 的幂等键；同一规范请求不能跨 Run 复用结果。"""
        return f"{self._lease.run_id}:{request_hash}"

    def _reserve_idempotency(self, request_hash: ContentHash) -> IdempotencyRecord:
        key = self._idempotency_key(request_hash)
        record = IdempotencyRecord(
            scope="analysis",
            key=key,
            request_hash=request_hash,
            result_ref=None,
            created_at=self._clock(),
        )
        with self._store.unit_of_work() as uow:
            return uow.repo.reserve_idempotency(record)

    def _summary_path(self, step_id: StepId):
        """返回内部摘要文件位置；完整业务输出仍由 Workspace 发布协议管理。"""
        return self._workspace.staging_path(
            self._lease.project_id,
            self._lease.task_id,
            step_id,
            "analysis-summary.json",
        )

    def _load_durable_summary(self, step_id: StepId) -> AnalysisSummary | None:
        path = self._summary_path(step_id)
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            return None
        try:
            summary = AnalysisSummary.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return summary if summary.step_id == step_id else None

    def _new_step(self) -> tuple[AnalysisStep, int]:
        step = AnalysisStep(
            id=StepId(self._ids.new("step")), run_id=self._lease.run_id, created_at=self._clock()
        )
        with self._store.unit_of_work() as uow:
            uow.repo.add_step(step)
        with self._store.unit_of_work() as uow:
            started = uow.repo.save_step(step.start(self._clock()), 0, "STEP_STARTED")
        return started.value, started.version

    def _save_step(self, step: AnalysisStep, version: int, event: str) -> None:
        with self._store.unit_of_work() as uow:
            uow.repo.save_step(step, version, event)

    @staticmethod
    def _failure_kind(error: BaseException) -> StepFailureKind:
        if isinstance(error, (SandboxTimeoutError,)):
            return StepFailureKind.RESOURCE_LIMIT
        if isinstance(error, (SandboxOutputLimitError,)):
            return StepFailureKind.RESOURCE_LIMIT
        if isinstance(error, (SandboxCancelledError,)):
            return StepFailureKind.POLICY_DENIED
        if isinstance(error, (SandboxLostError, SandboxError)):
            return StepFailureKind.SANDBOX_ERROR
        return StepFailureKind.INTERNAL_ERROR

    def _write_staging(self, step_id: StepId, name: str, data: bytes) -> ContentHash:
        """Host 只把 Sandbox 已返回的结果写入当前 Step staging，并以原子替换落盘。"""
        target = self._workspace.staging_path(
            self._lease.project_id, self._lease.task_id, step_id, name
        )
        temporary = target.with_name(f".{target.name}.writing")
        temporary.unlink(missing_ok=True)
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return compute_content_hash(data)

    def _publish_outputs(
        self,
        step_id: StepId,
        outputs: tuple[OutputSpec, ...],
        data: bytes,
        inputs: tuple[InputReference, ...],
        code_hash: ContentHash,
    ) -> tuple[OutputReference, ...]:
        """将结果写 staging，并在配置了 WorkspaceBridge 时完成 STAGED -> AVAILABLE。"""
        references: list[OutputReference] = []
        for output in outputs:
            content_hash = self._write_staging(step_id, output.name, data)
            resource_id = self._ids.new(output.kind.value.lower())
            available = False
            if self._bridge is not None:
                record = self._bridge.stage(
                    project_id=self._lease.project_id,
                    task_id=self._lease.task_id,
                    run_id=self._lease.run_id,
                    step_id=step_id,
                    output_name=output.name,
                    kind=output.kind,
                    resource_id=resource_id,
                    content_hash=content_hash,
                    byte_size=len(data),
                )
                published = self._bridge.publish(record.idempotency_key)
                available = True
                with self._store.unit_of_work() as uow:
                    if output.kind == PublicationKind.DATASET:
                        uow.repo.add_dataset(
                            Dataset(
                                id=DatasetId(resource_id),
                                project_id=self._lease.project_id,
                                task_id=self._lease.task_id,
                                run_id=self._lease.run_id,
                                name=published.name,
                                content_hash=published.content_hash,
                                created_at=self._clock(),
                            )
                        )
                    else:
                        uow.repo.add_artifact(
                            Artifact(
                                id=ArtifactId(resource_id),
                                project_id=self._lease.project_id,
                                task_id=self._lease.task_id,
                                run_id=self._lease.run_id,
                                name=published.name,
                                content_hash=published.content_hash,
                                created_at=self._clock(),
                            )
                        )
                    for input_ref in inputs:
                        uow.repo.add_lineage(
                            Lineage(
                                id=LineageId(self._ids.new("lineage")),
                                run_id=self._lease.run_id,
                                source=ResourceRef(
                                    kind=ResourceKind.FILE_VERSION,
                                    resource_id=str(input_ref.file_version_id),
                                    content_hash=input_ref.content_hash,
                                ),
                                target=ResourceRef(
                                    kind=(
                                        ResourceKind.DATASET
                                        if output.kind == PublicationKind.DATASET
                                        else ResourceKind.ARTIFACT
                                    ),
                                    resource_id=resource_id,
                                    content_hash=content_hash,
                                ),
                                created_at=self._clock(),
                            )
                        )
                    uow.repo.add_lineage(
                        Lineage(
                            id=LineageId(self._ids.new("lineage")),
                            run_id=self._lease.run_id,
                            source=ResourceRef(
                                kind=ResourceKind.STEP,
                                resource_id=str(step_id),
                                content_hash=code_hash,
                            ),
                            target=ResourceRef(
                                kind=(
                                    ResourceKind.DATASET
                                    if output.kind == PublicationKind.DATASET
                                    else ResourceKind.ARTIFACT
                                ),
                                resource_id=resource_id,
                                content_hash=content_hash,
                            ),
                            created_at=self._clock(),
                        )
                    )
            references.append(
                OutputReference(
                    resource_id=resource_id,
                    name=output.name,
                    kind=output.kind,
                    content_hash=content_hash,
                    byte_size=len(data),
                    available=available,
                )
            )
        return tuple(references)

    async def execute(self, request: AnalysisRequest) -> AnalysisSummary:
        """统一执行入口：校验上下文、幂等/熔断、创建 Step、Sandbox 执行和输出发布。"""
        task_id, run_id, project_id, snapshot_id = self._context()
        del task_id, run_id, project_id, snapshot_id
        self._validate_inputs(request.inputs)
        self._validate_staging_ref(request.staging_ref)
        if request.budget_units > self._max_budget_units:
            raise AnalysisBudgetError("请求预算超过 AnalysisRuntime 上限")
        request_hash = self._request_hash(request)
        cached = self._cache.get(str(request_hash))
        if cached is not None:
            return cached
        if self._failure_counts.get(str(request_hash), 0) >= self._max_consecutive_failures:
            raise AnalysisCircuitOpenError("相同规范化分析请求已连续失败并触发熔断")
        reservation = self._reserve_idempotency(request_hash)
        if reservation.result_ref is not None:
            durable = self._load_durable_summary(StepId(reservation.result_ref))
            if durable is None:
                raise AnalysisContextError("幂等结果引用存在但摘要无法恢复，拒绝重复执行")
            self._cache[str(request_hash)] = durable
            return durable
        step, version = self._new_step()
        sandbox_request = ExecutionRequest(
            step_id=step.id,
            kind=request.kind,
            code=request.code,
            timeout_seconds=request.timeout_seconds,
            expected_output_names=tuple(item.name for item in request.expected_outputs),
            input_refs=tuple(str(item.file_version_id) for item in request.inputs),
            staging_ref=f"{request.staging_ref}:{step.id}",
            budget_units=request.budget_units,
        )
        step_finalized = False
        try:
            result = await self._sandbox.execute(self._lease, sandbox_request)
            if result.status != ExecutionStatus.SUCCEEDED:
                error = SandboxError(f"Sandbox 返回 {result.status}")
                raise error
            data = result.stdout.encode("utf-8")
            code_hash = compute_content_hash(request.code.encode("utf-8"))
            outputs = self._publish_outputs(
                step.id, request.expected_outputs, data, request.inputs, code_hash
            )
            summary = AnalysisSummary(
                step_id=step.id,
                request_hash=request_hash,
                status=result.status,
                exit_code=result.exit_code,
                stdout=result.stdout[: self._max_summary_chars],
                stderr=result.stderr[: self._max_summary_chars],
                duration_ms=result.duration_ms,
                code_hash=code_hash,
                input_refs=request.inputs,
                outputs=outputs,
                schema=result.output_schema,
                statistics=result.statistics,
                resource_stats={
                    "stdout_bytes": len(result.stdout.encode("utf-8")),
                    "stderr_bytes": len(result.stderr.encode("utf-8")),
                    **result.resource_stats,
                },
            )
            self._write_staging(
                step.id,
                "analysis-summary.json",
                summary.model_dump_json().encode("utf-8"),
            )
            finished = step.succeed(self._clock())
            self._save_step(finished, version, "STEP_SUCCEEDED")
            step_finalized = True
            with self._store.unit_of_work() as uow:
                uow.repo.complete_idempotency(
                    "analysis",
                    self._idempotency_key(request_hash),
                    request_hash,
                    str(step.id),
                )
            self._failure_counts.pop(str(request_hash), None)
            self._cache[str(request_hash)] = summary
            return summary
        except Exception as error:
            self._failure_counts[str(request_hash)] = (
                self._failure_counts.get(str(request_hash), 0) + 1
            )
            if step_finalized:
                raise
            if isinstance(error, SandboxTimeoutError):
                failed = step.timeout(self._clock())
                self._save_step(failed, version, "STEP_TIMED_OUT")
            elif isinstance(error, SandboxCancelledError):
                failed = step.cancel(self._clock())
                self._save_step(failed, version, "STEP_CANCELLED")
            else:
                failed = step.fail(self._failure_kind(error), self._clock())
                self._save_step(failed, version, "STEP_FAILED")
            raise

    async def execute_python(
        self,
        code: str,
        *,
        inputs: tuple[InputReference, ...] = (),
        expected_outputs: tuple[OutputSpec, ...] = (),
        timeout_seconds: int = 300,
        budget_units: int = 1,
        staging_ref: str | None = None,
    ) -> AnalysisSummary:
        """仅在 Sandbox 执行 Python；不提供 Host fallback 或动态安装。"""
        return await self.execute(
            AnalysisRequest(
                kind=ExecutionKind.PYTHON,
                code=code,
                inputs=inputs,
                expected_outputs=expected_outputs,
                timeout_seconds=timeout_seconds,
                budget_units=budget_units,
                staging_ref=staging_ref or self._default_staging_ref(),
            )
        )

    async def execute_sql(
        self,
        query: str,
        *,
        inputs: tuple[InputReference, ...] = (),
        expected_outputs: tuple[OutputSpec, ...] = (),
        timeout_seconds: int = 300,
        budget_units: int = 1,
        staging_ref: str | None = None,
    ) -> AnalysisSummary:
        """仅在 Sandbox 的 DuckDB/SQLite runner 执行 SQL。"""
        return await self.execute(
            AnalysisRequest(
                kind=ExecutionKind.SQL,
                code=query,
                inputs=inputs,
                expected_outputs=expected_outputs,
                timeout_seconds=timeout_seconds,
                budget_units=budget_units,
                staging_ref=staging_ref or self._default_staging_ref(),
            )
        )

    def list_project_files(self) -> tuple[ProjectFileView, ...]:
        """列出当前 Run Snapshot 的文件版本元数据。"""
        snapshot = self._snapshot()
        with self._store.unit_of_work() as uow:
            result: list[ProjectFileView] = []
            for entry in snapshot.entries:
                version = uow.repo.get_file_version(entry.file_version_id).value
                file = uow.repo.get_file(entry.file_id)
                result.append(
                    ProjectFileView(
                        project_id=snapshot.project_id,
                        file_id=file.id,
                        file_version_id=version.id,
                        name=file.name,
                        status=version.status,
                        content_hash=version.content_hash,
                        media_type=version.media_type,
                        byte_size=version.byte_size,
                    )
                )
            return tuple(result)

    def search_project(
        self, query: str, *, limit: int = 20, media_types: tuple[str, ...] | None = None
    ):
        """在固定 Snapshot 内执行元数据过滤 + FTS5/BM25 RELEVANT 检索。"""
        return self._corpus.search(
            self._lease.project_snapshot_id, query, limit=limit, media_types=media_types
        )

    def inspect_project_file(
        self, file_version_id: str, *, max_bytes: int = 5 * 1024 * 1024, max_chars: int = 4096
    ) -> ProjectFileInspection:
        """读取固定 Snapshot 内一个版本的有界片段，不暴露 Workspace 路径。"""
        opened = self._corpus.open_resource(
            self._lease.project_snapshot_id, FileVersionId(file_version_id), max_bytes=max_bytes
        )
        text = opened.data.decode("utf-8", errors="replace")
        return ProjectFileInspection(
            file_version_id=opened.file_version_id,
            name=opened.name,
            media_type=opened.media_type,
            content_hash=opened.content_hash,
            byte_size=len(opened.data),
            excerpt=text[:max_chars],
            truncated=len(text) > max_chars,
        )

    async def preview_project_table(
        self,
        table_name: str,
        *,
        rows: int = 20,
        inputs: tuple[InputReference, ...] = (),
        timeout_seconds: int = 300,
    ) -> AnalysisSummary:
        """通过 Sandbox SQL runner 返回有界表预览；表名只允许安全标识符。"""
        if not _IDENTIFIER.fullmatch(table_name):
            raise AnalysisContextError("表名必须是受控 SQL 标识符")
        if not 1 <= rows <= 1000:
            raise AnalysisBudgetError("表预览行数必须在 1..1000 内")
        return await self.execute_sql(
            f'SELECT * FROM "{table_name}" LIMIT {rows}',
            inputs=inputs,
            timeout_seconds=timeout_seconds,
        )

    async def query_project_tables(
        self,
        query: str,
        *,
        inputs: tuple[InputReference, ...] = (),
        timeout_seconds: int = 300,
        expected_outputs: tuple[OutputSpec, ...] = (),
    ) -> AnalysisSummary:
        """通过 Sandbox DuckDB/SQLite runner 查询项目表；不在 Host 执行查询。"""
        return await self.execute_sql(
            query,
            inputs=inputs,
            timeout_seconds=timeout_seconds,
            expected_outputs=expected_outputs,
        )

    def get_project_coverage(self) -> ProjectCoverageReport:
        """生成并持久化当前 Snapshot 的 FULL_PROJECT CoverageReport。"""
        return self._corpus.full_project_coverage(self._lease.project_snapshot_id)

    def inspect_output(
        self,
        step_id: str,
        output_name: str,
        *,
        max_bytes: int = 5 * 1024 * 1024,
        max_chars: int = 4096,
    ) -> OutputInspection:
        """只检查当前 Task 当前/历史 Step 的 staging 输出，不接受任意 Host 路径。"""
        if (
            not StepId(step_id)
            or not output_name
            or output_name in {".", ".."}
            or "/" in output_name
            or "\\" in output_name
        ):
            raise AnalysisContextError("输出引用必须是受控 Step 与单个文件名")
        path = self._workspace.staging_path(
            self._lease.project_id, self._lease.task_id, StepId(step_id), output_name
        )
        if not path.is_file():
            raise AnalysisContextError("staging 输出不存在")
        size = path.stat().st_size
        if size > max_bytes:
            raise AnalysisBudgetError("输出超过有界检查上限")
        data = path.read_bytes()
        content_hash = compute_content_hash(data)
        text = data.decode("utf-8", errors="replace")
        available = False
        if self._bridge is not None:
            available = any(
                record.step_id == StepId(step_id)
                and record.output_name == output_name
                and record.status == PublicationStatus.AVAILABLE
                for record in self._bridge.available(self._lease.project_id)
            )
        return OutputInspection(
            step_id=StepId(step_id),
            name=output_name,
            content_hash=content_hash,
            byte_size=len(data),
            excerpt=text[:max_chars],
            truncated=len(text) > max_chars,
            available=available,
        )

    async def execute_full_project(
        self,
        code: str,
        *,
        kind: ExecutionKind = ExecutionKind.PYTHON,
        batch_size: int = 20,
        timeout_seconds: int = 300,
        budget_units: int = 1,
    ) -> FullProjectResult:
        """按 Snapshot READY 条目分批执行并返回覆盖缺口，不隐藏 FAILED/UNSUPPORTED。"""
        if batch_size <= 0:
            raise AnalysisBudgetError("FULL_PROJECT batch_size 必须为正")
        coverage = self.get_project_coverage()
        snapshot = self._snapshot()
        ready = [entry for entry in snapshot.ready_entries() if entry.content_hash is not None]
        batches: list[AnalysisSummary] = []
        for offset in range(0, len(ready), batch_size):
            entries = ready[offset : offset + batch_size]
            refs_list: list[InputReference] = []
            for entry in entries:
                content_hash = entry.content_hash
                if content_hash is None:
                    raise AnalysisContextError("READY 输入缺少内容 hash")
                refs_list.append(
                    InputReference(
                        file_version_id=entry.file_version_id,
                        file_id=entry.file_id,
                        content_hash=content_hash,
                    )
                )
            refs = tuple(refs_list)
            request = AnalysisRequest(
                kind=kind,
                code=code,
                inputs=refs,
                timeout_seconds=timeout_seconds,
                budget_units=budget_units,
                staging_ref=self._default_staging_ref(),
                mode=AnalysisMode.FULL_PROJECT,
            )
            batches.append(await self.execute(request))
        return FullProjectResult(
            coverage_report_id=str(coverage.id),
            total_files=coverage.total,
            processed_files=coverage.processed,
            uncovered_files=coverage.total - coverage.processed,
            batches=tuple(batches),
        )

    def submit_finding(self, summary: str, evidence: tuple[EvidenceRef, ...]) -> Finding:
        """提交 DRAFT FindingCandidate；正式验证由后续 Host Gate 负责。"""
        return self.submit_finding_with_coverage(summary, evidence)

    def submit_finding_with_coverage(
        self,
        summary: str,
        evidence: tuple[EvidenceRef, ...],
        *,
        coverage_report_id: str | None = None,
    ) -> Finding:
        """提交带可选覆盖报告引用的 Finding 草稿。

        Agent 仍只能得到 ``DRAFT``；CoverageReport 只是 FULL_PROJECT 的事实引用，
        是否允许晋级为正式 Finding 仍必须经过 Host VerificationService 的三道 Gate。
        """
        _, run_id, _, snapshot_id = self._context()
        if not summary.strip() or len(summary) > self._max_summary_chars:
            raise AnalysisContextError("Finding 摘要必须是有界非空文本")
        snapshot = self._snapshot()
        try:
            with self._store.unit_of_work() as uow:
                for item in evidence:
                    if item.kind == EvidenceKind.FILE:
                        entry = snapshot.entry_for(FileVersionId(item.target_id))
                        if entry is None or entry.content_hash != item.content_hash:
                            raise AnalysisContextError(
                                "Finding 文件证据不属于当前 Snapshot 或 hash 不匹配"
                            )
                    elif item.kind == EvidenceKind.STEP:
                        step = uow.repo.get_step(StepId(item.target_id)).value
                        if step.run_id != self._lease.run_id:
                            raise AnalysisContextError("Finding Step 证据不属于当前 Run")
                    elif item.kind == EvidenceKind.DATASET:
                        resource = uow.repo.get_dataset(DatasetId(item.target_id))
                        if (
                            resource.run_id != self._lease.run_id
                            or resource.content_hash != item.content_hash
                        ):
                            raise AnalysisContextError(
                                "Finding Dataset 证据不属于当前 Run 或 hash 不匹配"
                            )
                    elif item.kind == EvidenceKind.ARTIFACT:
                        resource = uow.repo.get_artifact(ArtifactId(item.target_id))
                        if (
                            resource.run_id != self._lease.run_id
                            or resource.content_hash != item.content_hash
                        ):
                            raise AnalysisContextError(
                                "Finding Artifact 证据不属于当前 Run 或 hash 不匹配"
                            )
        except RecordNotFoundError as error:
            raise AnalysisContextError("Finding 证据引用不存在") from error
        candidate = FindingCandidate(
            task_id=self._lease.task_id,
            run_id=run_id,
            project_snapshot_id=snapshot_id,
            summary=summary,
            evidence=evidence,
            coverage_report_id=(
                CoverageReportId(coverage_report_id) if coverage_report_id else None
            ),
            created_at=self._clock(),
        )
        finding = Finding(id=FindingId(self._ids.new("finding")), candidate=candidate)
        with self._store.unit_of_work() as uow:
            uow.repo.add_finding(finding)
        return finding
