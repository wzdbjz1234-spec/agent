"""测试用确定性 Workspace Adapter。"""

from __future__ import annotations

from pathlib import Path

from .local import LocalWorkspace


class FakeWorkspace(LocalWorkspace):
    """使用测试提供的隔离根复用完整契约，不隐藏真实文件系统语义。"""

    def __init__(self, root: Path, *, max_file_bytes: int = 1024 * 1024) -> None:
        super().__init__(root, max_file_bytes=max_file_bytes)
