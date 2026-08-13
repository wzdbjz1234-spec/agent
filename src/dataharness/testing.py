"""测试支持工具：假时钟与合成数据。

供单元/集成/E2E 测试使用，避免测试依赖真实时间、随机性或外部数据。这些工具
不参与生产运行路径，仅作为可复用的确定性输入源。
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta


class FakeClock:
    """确定性假时钟：返回固定或手动推进的 UTC 时间。"""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        """返回当前（假）时间。"""
        return self._now

    def set(self, at: datetime) -> None:
        """把时钟设到指定时间。"""
        self._now = at

    def advance(self, delta: timedelta) -> datetime:
        """推进一段时间并返回新的当前时间。"""
        self._now += delta
        return self._now


def synthetic_csv_bytes(rows: list[list[object]]) -> bytes:
    """把二维行数据序列化为 CSV 字节，用于文件导入测试。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def synthetic_text_bytes(text: str) -> bytes:
    """把纯文本编码为 UTF-8 字节。"""
    return text.encode("utf-8")
