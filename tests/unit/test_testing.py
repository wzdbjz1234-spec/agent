"""测试支持工具（假时钟与合成数据）测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dataharness.testing import FakeClock, synthetic_csv_bytes, synthetic_text_bytes


def test_fake_clock_default_is_utc() -> None:
    clock = FakeClock()
    assert clock.now().tzinfo is UTC


def test_fake_clock_advance() -> None:
    clock = FakeClock()
    start = clock.now()
    clock.advance(timedelta(hours=1))
    assert clock.now() - start == timedelta(hours=1)


def test_fake_clock_set() -> None:
    clock = FakeClock()
    at = datetime(2030, 5, 5, tzinfo=UTC)
    clock.set(at)
    assert clock.now() == at


def test_synthetic_csv_bytes() -> None:
    assert synthetic_csv_bytes([["a", "b"], ["1", "2"]]) == b"a,b\r\n1,2\r\n"


def test_synthetic_text_bytes() -> None:
    assert synthetic_text_bytes("hello") == b"hello"
