"""ProjectCorpus deep module：项目、导入、提取、索引、快照与覆盖。"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from dataharness.domain import (
    CoverageItem,
    CoverageItemStatus,
    CoverageReportId,
    FileId,
    FileVersionId,
    FileVersionStatus,
    Project,
    ProjectCoverageReport,
    ProjectFile,
    ProjectFileVersion,
    ProjectId,
    ProjectSnapshot,
    ProjectStatus,
    SnapshotEntry,
    SnapshotId,
    compute_content_hash,
    utcnow,
)
from dataharness.idgen import IdFactory, UuidIdFactory
from dataharness.providers.workspace import normalize_filename
from dataharness.storage import SqliteRuntimeStore
from dataharness.workspace import ResourceIntegrityError, VirtualWorkspace, WorkspaceResource

from .extractors import UnsupportedFormatError, extract_document, sniff_media_type
from .index import INDEX_VERSION, CorpusIndex
from .models import ExtractedDocument, OpenedResource, SearchHit


class ProjectCorpus:
    """长期项目语料的唯一应用入口。

    Runtime SQLite 保存领域元数据；Workspace 保存不可变原件和可重建派生物。调用方
    无需理解目录、解析器、FTS 表或版本号分配细节。
    """

    def __init__(
        self,
        store: SqliteRuntimeStore,
        workspace: VirtualWorkspace,
        *,
        id_factory: IdFactory | None = None,
        clock: Callable[[], datetime] = utcnow,
        max_snippet_chars: int = 2000,
    ) -> None:
        self._store = store
        self._workspace = workspace
        self._ids = id_factory or UuidIdFactory()
        self._clock = clock
        self._max_snippet_chars = max_snippet_chars

    def create_project(self, name: str) -> Project:
        """创建 ACTIVE Project，并幂等建立 Workspace 布局。"""
        if not name.strip():
            raise ValueError("项目名不能为空")
        project = Project(
            id=ProjectId(self._ids.new("project")), name=name.strip(), created_at=self._clock()
        )
        self._workspace.create_project(project.id)
        with self._store.unit_of_work() as uow:
            uow.repo.add_project(project)
        return project

    def archive_project(self, project_id: ProjectId) -> Project:
        """归档项目；历史版本、快照和文件均保留。"""
        with self._store.unit_of_work() as uow:
            stored = uow.repo.get_project(project_id)
            archived = stored.value.archive(at=self._clock())
            if archived == stored.value:
                return archived
            return uow.repo.save_project(archived, stored.version).value

    def _find_file(self, project_id: ProjectId, name: str) -> ProjectFile | None:
        with self._store.unit_of_work() as uow:
            return next(
                (item for item in uow.repo.list_project_files(project_id) if item.name == name),
                None,
            )

    def import_file(
        self, project_id: ProjectId, source: Path, *, logical_name: str | None = None
    ) -> ProjectFileVersion:
        """导入文件；同名更新创建新版本，失败/不支持也明确定稿。

        原件先以不可变版本保存，再执行提取与索引。这样解析器崩溃不会丢失输入，且
        恢复逻辑可以根据状态决定重建派生物或明确报告失败。
        """
        normalized_name = normalize_filename(logical_name or source.name)
        size, content_hash = self._workspace.inspect_import(source)
        # 同名导入需要在读取当前版本与分配下一个版本号之间串行化。否则两个浏览器请求
        # 都可能观察到相同版本号，随后触发唯一约束或留下难以解释的导入失败。
        with self._store.unit_of_work(immediate=True) as uow:
            project = uow.repo.get_project(project_id).value
            if project.status != ProjectStatus.ACTIVE:
                raise ValueError("归档项目不能导入新文件")
            current = next(
                (
                    item
                    for item in uow.repo.list_project_files(project_id)
                    if item.name == normalized_name
                ),
                None,
            )
            if current is None:
                current = ProjectFile(
                    id=FileId(self._ids.new("file")),
                    project_id=project_id,
                    name=normalized_name,
                    created_at=self._clock(),
                )
                uow.repo.add_file(current)
                version_number = 1
            else:
                version_number = len(uow.repo.list_file_versions(current.id)) + 1
            version = ProjectFileVersion(
                id=FileVersionId(self._ids.new("file_version")),
                file_id=current.id,
                project_id=project_id,
                version_number=version_number,
                created_at=self._clock(),
            )
            uow.repo.add_file_version(version)

        try:
            self._workspace.import_source(
                project_id,
                current.id,
                version.id,
                source,
                normalized_name,
                content_hash,
            )
            stored_path = self._workspace.source_path(
                project_id, current.id, version.id, normalized_name
            )
            media_type = sniff_media_type(stored_path)
            document = extract_document(stored_path, content_hash, media_type)
            self._write_extracted(project_id, version.id, document)
            CorpusIndex(self._workspace.index_path(project_id), self._max_snippet_chars).replace(
                file_id=current.id,
                file_version_id=version.id,
                file_name=normalized_name,
                document=document,
            )
            final = version.mark_ready(content_hash, size, media_type, at=self._clock())
        except UnsupportedFormatError:
            final = version.mark_unsupported(at=self._clock())
        except Exception:
            final = version.mark_failed(at=self._clock())

        with self._store.unit_of_work() as uow:
            return uow.repo.finalize_file_version(final, expected_version=0).value

    def _write_extracted(
        self, project_id: ProjectId, version_id: FileVersionId, document: ExtractedDocument
    ) -> WorkspaceResource:
        """原子写入可重建提取物，内容内嵌源哈希和 extractor version。"""
        target = self._workspace.extracted_path(project_id, version_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = document.model_dump_json().encode("utf-8")
        temporary = target.with_name(f".{target.name}.writing")
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        target.chmod(stat.S_IREAD)
        return WorkspaceResource(
            project_id=project_id,
            namespace="extracted",
            resource_id=str(version_id),
            name=target.name,
            content_hash=compute_content_hash(data),
            byte_size=len(data),
        )

    def create_snapshot(self, project_id: ProjectId) -> ProjectSnapshot:
        """固定全部逻辑文件的当前版本、处理状态、索引与 Dataset 版本。"""
        with self._store.unit_of_work() as uow:
            project = uow.repo.get_project(project_id).value
            # Snapshot 会形成新的可执行输入边界。归档项目仅供读取既有历史，不允许再
            # 通过绕过 WebUI 的 API 产生新的执行前提。
            if project.status != ProjectStatus.ACTIVE:
                raise ValueError("归档项目不能创建新的 Snapshot")
            versions = uow.repo.list_current_file_versions(project_id)
            file_names = {item.id: uow.repo.get_file(item.file_id).name for item in versions}
            datasets = uow.repo.list_project_datasets(project_id)
            snapshot = ProjectSnapshot(
                id=SnapshotId(self._ids.new("snapshot")),
                project_id=project.id,
                created_at=self._clock(),
                entries=tuple(
                    SnapshotEntry(
                        file_version_id=item.id,
                        file_id=item.file_id,
                        version_number=item.version_number,
                        status=item.status,
                        content_hash=item.content_hash,
                    )
                    for item in versions
                ),
                index_version=INDEX_VERSION
                if any(v.status == FileVersionStatus.READY for v in versions)
                else None,
                dataset_version_ids=tuple(item.id for item in datasets),
            )
            uow.repo.add_snapshot(snapshot)
        snapshot_files = tuple(
            (entry.file_id, entry.file_version_id, file_names[entry.file_version_id])
            for entry in snapshot.entries
            if entry.status == FileVersionStatus.READY
        )
        self._workspace.materialize_snapshot(project_id, snapshot.id, snapshot_files)
        manifest = {
            "project_id": str(snapshot.project_id),
            "snapshot_id": str(snapshot.id),
            "index_version": snapshot.index_version,
            "file_versions": [entry.model_dump(mode="json") for entry in snapshot.entries],
            "dataset_version_ids": list(snapshot.dataset_version_ids),
        }
        self._workspace.write_manifest(
            project_id,
            f"snapshot-{snapshot.id}.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            ),
        )
        return snapshot

    def search(
        self,
        snapshot_id: SnapshotId,
        query: str,
        *,
        limit: int = 20,
        media_types: tuple[str, ...] | None = None,
    ) -> tuple[SearchHit, ...]:
        """在固定 Snapshot 内执行 RELEVANT FTS5/BM25 检索。"""
        with self._store.unit_of_work() as uow:
            snapshot = uow.repo.get_snapshot(snapshot_id)
        return CorpusIndex(
            self._workspace.index_path(snapshot.project_id), self._max_snippet_chars
        ).search(snapshot, query, limit=limit, media_types=media_types)

    def rebuild_derived(self, file_version_id: FileVersionId) -> ProjectFileVersion:
        """从不可变原件重建被删除的提取物与索引，不改写领域版本。

        仅 READY 版本允许重建；重建前再次校验原件哈希，防止用漂移内容污染索引。
        """
        with self._store.unit_of_work() as uow:
            version = uow.repo.get_file_version(file_version_id).value
            if (
                version.status != FileVersionStatus.READY
                or version.content_hash is None
                or version.media_type is None
            ):
                raise ValueError("只有 READY ProjectFileVersion 可以重建派生物")
            file = uow.repo.get_file(version.file_id)
        source = self._workspace.source_path(
            version.project_id, version.file_id, version.id, file.name
        )
        if compute_content_hash(source.read_bytes()) != version.content_hash:
            raise ResourceIntegrityError("原件哈希漂移，拒绝重建派生物")
        document = extract_document(source, version.content_hash, version.media_type)
        self._write_extracted(version.project_id, version.id, document)
        CorpusIndex(
            self._workspace.index_path(version.project_id), self._max_snippet_chars
        ).replace(
            file_id=version.file_id,
            file_version_id=version.id,
            file_name=file.name,
            document=document,
        )
        return version

    def open_resource(
        self,
        snapshot_id: SnapshotId,
        file_version_id: FileVersionId,
        *,
        max_bytes: int = 5 * 1024 * 1024,
    ) -> OpenedResource:
        """有界读取 Snapshot 内 READY 原件，并再次验证内容哈希。"""
        with self._store.unit_of_work() as uow:
            snapshot = uow.repo.get_snapshot(snapshot_id)
            entry = snapshot.entry_for(file_version_id)
            if (
                entry is None
                or entry.status != FileVersionStatus.READY
                or entry.content_hash is None
            ):
                raise ValueError("文件版本不属于 Snapshot 的 READY 输入")
            version = uow.repo.get_file_version(file_version_id).value
            file = uow.repo.get_file(version.file_id)
        if version.byte_size is None or version.byte_size > max_bytes:
            raise ResourceIntegrityError("资源超过有界读取上限")
        path = self._workspace.source_path(
            snapshot.project_id, version.file_id, version.id, file.name
        )
        data = path.read_bytes()
        if compute_content_hash(data) != entry.content_hash:
            raise ResourceIntegrityError("ProjectFileVersion 原件哈希已变化")
        assert version.media_type is not None
        return OpenedResource(
            file_version_id=version.id,
            name=file.name,
            media_type=version.media_type,
            content_hash=entry.content_hash,
            data=data,
        )

    def full_project_coverage(self, snapshot_id: SnapshotId) -> ProjectCoverageReport:
        """枚举 Snapshot 全部条目并持久化 FULL_PROJECT 覆盖事实。"""
        with self._store.unit_of_work() as uow:
            snapshot = uow.repo.get_snapshot(snapshot_id)
        items: list[CoverageItem] = []
        for entry in snapshot.entries:
            if entry.status == FileVersionStatus.READY:
                extracted = self._workspace.extracted_path(
                    snapshot.project_id, entry.file_version_id
                )
                status = (
                    CoverageItemStatus.PROCESSED
                    if extracted.is_file()
                    else CoverageItemStatus.FAILED
                )
                detail = None if extracted.is_file() else "提取物缺失，需重建"
            elif entry.status == FileVersionStatus.UNSUPPORTED:
                status, detail = CoverageItemStatus.UNSUPPORTED, "真实格式不受支持"
            elif entry.status == FileVersionStatus.FAILED:
                status, detail = CoverageItemStatus.FAILED, "导入或提取失败"
            else:
                status, detail = CoverageItemStatus.SKIPPED, "版本尚未完成导入"
            items.append(
                CoverageItem(
                    file_version_id=entry.file_version_id,
                    file_id=entry.file_id,
                    status=status,
                    detail=detail,
                )
            )
        report = ProjectCoverageReport(
            id=CoverageReportId(self._ids.new("coverage")),
            snapshot_id=snapshot_id,
            created_at=self._clock(),
            items=tuple(items),
        )
        with self._store.unit_of_work() as uow:
            uow.repo.add_coverage_report(report)
        return report
