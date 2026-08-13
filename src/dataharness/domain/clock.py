"""时间工具：统一使用带时区的 UTC，避免本地时区污染领域对象。"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """返回当前 UTC 时间（携带 ``timezone.utc`` 时区信息）。"""
    return datetime.now(UTC)
