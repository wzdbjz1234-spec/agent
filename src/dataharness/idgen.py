"""稳定 ID 生成。

领域对象接收显式 ID，ID 的实际生成属于应用层。这里提供可注入的 ID 工厂：
生产用 UUID，测试与可复现场景用确定性递增序列，二者满足同一 :class:`IdFactory` 协议。
"""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4


class IdFactory(Protocol):
    """ID 工厂协议：根据前缀生成稳定、唯一、可读的 ID。"""

    def new(self, prefix: str) -> str:
        """生成一个带前缀的 ID。"""
        ...


class UuidIdFactory:
    """生产用 ID 工厂：UUID4 十六进制，前缀用于定位对象类型。"""

    def new(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"


class DeterministicIdFactory:
    """确定性 ID 工厂：按调用顺序递增，保证测试与复现运行可重复。"""

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._counter = 0

    def new(self, prefix: str | None = None) -> str:
        self._counter += 1
        return f"{prefix or self._prefix}_{self._counter:06d}"
