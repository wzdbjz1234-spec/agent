"""本地 Workspace 生产 Adapter。"""

from .fake import FakeWorkspace
from .local import LocalWorkspace, normalize_filename

__all__ = ["FakeWorkspace", "LocalWorkspace", "normalize_filename"]
