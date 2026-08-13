"""Workspace 边界的稳定错误类型。"""

from __future__ import annotations


class WorkspaceError(RuntimeError):
    """Workspace 操作失败的基类。"""


class UnsafePathError(WorkspaceError):
    """路径越界、绝对路径、链接或特殊文件被拒绝。"""


class ResourceIntegrityError(WorkspaceError):
    """资源内容与声明的大小或哈希不一致。"""


class PublicationError(WorkspaceError):
    """发布状态冲突或无法安全收敛。"""
