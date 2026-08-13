"""受控本地 Workspace Provider。"""

from __future__ import annotations

import os
import re
import stat
import unicodedata
from pathlib import Path, PurePath

from dataharness.domain import (
    ContentHash,
    FileId,
    FileVersionId,
    ProjectId,
    StepId,
    TaskId,
    compute_content_hash,
)
from dataharness.workspace import (
    PublicationKind,
    PublicationRecord,
    ResourceIntegrityError,
    UnsafePathError,
    WorkspaceResource,
)

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_EXECUTABLE_SUFFIXES = frozenset(
    {".exe", ".com", ".bat", ".cmd", ".ps1", ".sh", ".dll", ".so", ".dylib"}
)
_PROJECT_DIRS = ("sources", "extracted", "indexes", "datasets", "artifacts", "manifests", "tasks")
_WINDOWS_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


def normalize_filename(name: str) -> str:
    """规范化逻辑文件名，同时拒绝绝对路径、分隔符和目录穿越。"""
    if not name or name in {".", ".."} or PurePath(name).name != name:
        raise UnsafePathError("文件名必须是单个相对路径组件")
    normalized = unicodedata.normalize("NFKC", name.strip())
    normalized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", normalized).rstrip(". ")
    if not normalized or len(normalized) > 255:
        raise UnsafePathError("文件名规范化后为空或过长")
    if normalized.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        raise UnsafePathError("文件名是 Windows 保留设备名")
    return normalized


class LocalWorkspace:
    """本地文件系统实现，所有内部路径均由稳定 ID 派生。

    每次访问都会验证组件、解析后的真实路径和父目录中的符号链接；因此调用方不能
    通过 ``..``、绝对路径、junction/symlink 或跨 Task ID 逃离受控根。
    """

    def __init__(self, root: Path, *, max_file_bytes: int = 100 * 1024 * 1024) -> None:
        unresolved = root.absolute()
        if unresolved.is_symlink():
            raise UnsafePathError("Workspace 根目录不能是符号链接")
        self.root = unresolved.resolve()
        self.max_file_bytes = max_file_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _component(value: object, label: str) -> str:
        text = str(value)
        if not _SAFE_COMPONENT.fullmatch(text) or text in {".", ".."}:
            raise UnsafePathError(f"{label} 包含非法路径字符")
        return text

    def _within(self, *parts: object, must_exist: bool = False) -> Path:
        safe = [self._component(part, "路径组件") for part in parts]
        candidate = self.root.joinpath(*safe)
        # strict=False 仍会解析已有父目录中的链接，足以在创建叶子前发现逃逸。
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise UnsafePathError("路径解析后越出 Workspace 根目录")
        cursor = self.root
        for part in safe:
            cursor = cursor / part
            if cursor.exists() and cursor.is_symlink():
                raise UnsafePathError("Workspace 路径中禁止符号链接")
        if must_exist and not candidate.exists():
            raise FileNotFoundError(candidate.name)
        return candidate

    def create_project(self, project_id: ProjectId) -> None:
        """幂等、逐目录创建项目布局。"""
        project = self._within(project_id)
        project.mkdir(exist_ok=True)
        for name in _PROJECT_DIRS:
            self._within(project_id, name).mkdir(exist_ok=True)

    def create_task(self, project_id: ProjectId, task_id: TaskId) -> None:
        """原子可重放地创建 Task 独立写域。"""
        self.create_project(project_id)
        for parts in (
            (project_id, "tasks", task_id),
            (project_id, "tasks", task_id, "working"),
            (project_id, "tasks", task_id, "staging"),
            (project_id, "tasks", task_id, "state"),
        ):
            self._within(*parts).mkdir(exist_ok=True)

    @staticmethod
    def validate_import_source(source: Path, max_file_bytes: int) -> tuple[int, ContentHash]:
        """在复制前拒绝链接、设备、可执行文件及超限输入。"""
        if source.is_symlink():
            raise UnsafePathError("导入源不能是符号链接")
        info = source.stat()
        if not stat.S_ISREG(info.st_mode):
            raise UnsafePathError("只允许导入普通文件")
        if info.st_size > max_file_bytes:
            raise ResourceIntegrityError(f"文件超过 {max_file_bytes} 字节上限")
        if source.suffix.casefold() in _EXECUTABLE_SUFFIXES or info.st_mode & 0o111:
            raise UnsafePathError("禁止导入可执行文件")
        return info.st_size, compute_content_hash(source.read_bytes())

    def inspect_import(self, source: Path) -> tuple[int, ContentHash]:
        """按 Provider 配额检查待导入文件并返回大小与内容哈希。"""
        return self.validate_import_source(source, self.max_file_bytes)

    def import_source(
        self,
        project_id: ProjectId,
        file_id: FileId,
        version_id: FileVersionId,
        source: Path,
        normalized_name: str,
        expected_hash: ContentHash,
    ) -> WorkspaceResource:
        """通过同目录临时文件和原子替换创建不可变输入版本。"""
        normalized_name = normalize_filename(normalized_name)
        size, actual_hash = self.validate_import_source(source, self.max_file_bytes)
        if actual_hash != expected_hash:
            raise ResourceIntegrityError("导入期间源文件内容发生变化")
        self.create_project(project_id)
        directory = self._within(project_id, "sources", file_id, version_id)
        directory.mkdir(parents=True, exist_ok=False)
        target = self._within(project_id, "sources", file_id, version_id, normalized_name)
        temporary = directory / f".{normalized_name}.importing"
        try:
            with source.open("rb") as reader, temporary.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            copied_hash = compute_content_hash(temporary.read_bytes())
            if copied_hash != expected_hash:
                raise ResourceIntegrityError("导入副本哈希与声明不一致")
            os.replace(temporary, target)
            target.chmod(stat.S_IREAD)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return WorkspaceResource(
            project_id=project_id,
            namespace="sources",
            resource_id=str(version_id),
            name=normalized_name,
            content_hash=expected_hash,
            byte_size=size,
        )

    def source_path(
        self, project_id: ProjectId, file_id: FileId, version_id: FileVersionId, name: str
    ) -> Path:
        return self._within(
            project_id, "sources", file_id, version_id, normalize_filename(name), must_exist=True
        )

    def extracted_path(self, project_id: ProjectId, version_id: FileVersionId) -> Path:
        return self._within(project_id, "extracted", f"{version_id}.json")

    def index_path(self, project_id: ProjectId) -> Path:
        return self._within(project_id, "indexes", "corpus.sqlite3")

    def write_manifest(self, project_id: ProjectId, name: str, data: bytes) -> WorkspaceResource:
        """只创建一次不可变清单；相同内容重放幂等，不同内容拒绝覆盖。"""
        self.create_project(project_id)
        safe_name = normalize_filename(name)
        target = self._within(project_id, "manifests", safe_name)
        content_hash = compute_content_hash(data)
        if target.exists():
            if compute_content_hash(target.read_bytes()) != content_hash:
                raise ResourceIntegrityError("不可变 manifest 已存在且内容不同")
        else:
            temporary = target.with_name(f".{safe_name}.writing")
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            target.chmod(stat.S_IREAD)
        return WorkspaceResource(
            project_id=project_id,
            namespace="manifests",
            resource_id=safe_name,
            name=safe_name,
            content_hash=content_hash,
            byte_size=len(data),
        )

    @staticmethod
    def _atomic_write(target: Path, data: bytes) -> None:
        """同目录写入、fsync 后原子替换，避免 Host 崩溃留下半文件。"""
        temporary = target.with_name(f".{target.name}.writing")
        temporary.unlink(missing_ok=True)
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    def write_working(
        self, project_id: ProjectId, task_id: TaskId, name: str, data: bytes
    ) -> WorkspaceResource:
        """仅写指定 Task 的 working；名称不接受路径或其他 Task 引用。"""
        self.create_task(project_id, task_id)
        safe_name = normalize_filename(name)
        target = self._within(project_id, "tasks", task_id, "working", safe_name)
        self._atomic_write(target, data)
        return WorkspaceResource(
            project_id=project_id,
            namespace="working",
            resource_id=str(task_id),
            name=safe_name,
            content_hash=compute_content_hash(data),
            byte_size=len(data),
        )

    def write_task_state(
        self, project_id: ProjectId, task_id: TaskId, name: str, data: bytes
    ) -> WorkspaceResource:
        """原子写 Task 状态；``RUN.json`` 创建后不可覆盖。"""
        allowed = {"PLAN.md", "PROGRESS.md", "CONTEXT.md", "RUN.json"}
        if name not in allowed:
            raise UnsafePathError("Task state 只允许固定清单文件")
        self.create_task(project_id, task_id)
        target = self._within(project_id, "tasks", task_id, "state", name)
        if name == "RUN.json" and target.exists():
            if compute_content_hash(target.read_bytes()) != compute_content_hash(data):
                raise ResourceIntegrityError("RUN.json 是不可变复现清单")
        else:
            self._atomic_write(target, data)
            if name == "RUN.json":
                target.chmod(stat.S_IREAD)
        return WorkspaceResource(
            project_id=project_id,
            namespace="state",
            resource_id=str(task_id),
            name=name,
            content_hash=compute_content_hash(data),
            byte_size=len(data),
        )

    def staging_path(
        self, project_id: ProjectId, task_id: TaskId, step_id: StepId, output_name: str
    ) -> Path:
        self.create_task(project_id, task_id)
        directory = self._within(project_id, "tasks", task_id, "staging", step_id)
        directory.mkdir(exist_ok=True)
        return self._within(
            project_id, "tasks", task_id, "staging", step_id, normalize_filename(output_name)
        )

    def cleanup_staging(self, project_id: ProjectId, task_id: TaskId) -> None:
        """清理当前 Task 尚未登记为正式资源的 staging 文件。

        发布流程会先把文件移动到 datasets/artifacts，再标记 AVAILABLE；因此只删除
        ``tasks/<task>/staging`` 不会触碰已发布资源。目标由受控组件拼接并再次验证，
        不接受调用方传入宿主路径。
        """
        staging = self._within(project_id, "tasks", task_id, "staging")
        if not staging.exists():
            return
        for child in staging.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                for nested in sorted(child.rglob("*"), reverse=True):
                    if nested.is_symlink() or nested.is_file():
                        nested.unlink(missing_ok=True)
                    elif nested.is_dir():
                        nested.rmdir()
                child.rmdir()

    def _published_path(self, record: PublicationRecord) -> Path:
        namespace = "datasets" if record.kind == PublicationKind.DATASET else "artifacts"
        return self._within(
            record.project_id,
            namespace,
            self._component(record.resource_id, "resource_id"),
            normalize_filename(record.output_name),
        )

    @staticmethod
    def _verify(path: Path, record: PublicationRecord) -> None:
        if path.is_symlink() or not path.is_file():
            raise ResourceIntegrityError("发布资源不是普通文件")
        if path.stat().st_size != record.byte_size:
            raise ResourceIntegrityError("发布资源大小与 STAGED 记录不一致")
        if compute_content_hash(path.read_bytes()) != record.content_hash:
            raise ResourceIntegrityError("发布资源哈希与 STAGED 记录不一致")

    def published_resource(self, record: PublicationRecord) -> WorkspaceResource:
        target = self._published_path(record)
        self._verify(target, record)
        namespace = "datasets" if record.kind == PublicationKind.DATASET else "artifacts"
        return WorkspaceResource(
            project_id=record.project_id,
            namespace=namespace,
            resource_id=record.resource_id,
            name=record.output_name,
            content_hash=record.content_hash,
            byte_size=record.byte_size,
        )

    def publish_staged(self, record: PublicationRecord) -> WorkspaceResource:
        source = self.staging_path(
            record.project_id, record.task_id, record.step_id, record.output_name
        )
        target = self._published_path(record)
        if target.exists():
            return self.published_resource(record)
        self._verify(source, record)
        target.parent.mkdir(exist_ok=True)
        os.replace(source, target)
        target.chmod(stat.S_IREAD)
        return self.published_resource(record)

    def resource_exists(self, record: PublicationRecord) -> bool:
        try:
            self.published_resource(record)
        except (FileNotFoundError, ResourceIntegrityError):
            return False
        return True
