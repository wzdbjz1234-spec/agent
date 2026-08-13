"""单元测试共享 fixture 与常量。

使用固定时间戳保证测试确定性；领域对象均支持显式传入时间，
因此测试无需打补丁即可获得可复现结果。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.fixture
def t0() -> datetime:
    """固定的 UTC 基准时间，用于领域对象的确定性时间戳。"""
    return datetime(2026, 1, 1, tzinfo=UTC)
