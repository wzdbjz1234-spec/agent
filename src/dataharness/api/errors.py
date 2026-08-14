"""HTTP 边界的安全错误映射。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApiError(Exception):
    """已知业务错误；message 必须是可公开的短说明。"""

    status_code: int
    code: str
    message: str
