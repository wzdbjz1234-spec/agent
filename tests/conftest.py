"""跨层共享测试 fixture。

提供假时钟、确定性 ID 工厂、临时 runtime-data 布局与合成数据，供 unit/contract/
integration/e2e 各层复用，避免测试依赖真实时间、随机性或外部数据。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dataharness.idgen import DeterministicIdFactory
from dataharness.testing import FakeClock, synthetic_csv_bytes


@pytest.fixture
def fake_clock() -> FakeClock:
    """确定性假时钟，初始为固定 UTC 时间。"""
    return FakeClock(datetime(2026, 1, 1, tzinfo=UTC))


@pytest.fixture
def id_factory() -> DeterministicIdFactory:
    """确定性 ID 工厂，每次调用生成可复现的递增 ID。"""
    return DeterministicIdFactory()


@pytest.fixture
def runtime_layout(tmp_path: Path) -> Path:
    """在临时目录建立最小 runtime-data 布局（projects/ 与 privacy/）。"""
    (tmp_path / "projects").mkdir()
    (tmp_path / "privacy").mkdir()
    return tmp_path


@pytest.fixture
def sample_csv_bytes() -> bytes:
    """合成 CSV 字节，用于文件导入测试。"""
    return synthetic_csv_bytes([["id", "name"], ["1", "alice"], ["2", "bob"]])
