"""Runtime SQLite UnitOfWork。"""

from __future__ import annotations

import sqlite3
from types import TracebackType

from .database import RuntimeConnectionFactory
from .repository import RuntimeRepository


class UnitOfWork:
    """把多个 Repository 操作与事件提交收拢到一个显式事务。

    默认使用延迟事务以减少只读阻塞；耐久队列领取由 ``immediate=True`` 获取写锁，
    保证“选择候选 + 写入 lease”之间没有竞态窗口。
    """

    def __init__(self, factory: RuntimeConnectionFactory, *, immediate: bool = False) -> None:
        self._factory = factory
        self._immediate = immediate
        self._connection: sqlite3.Connection | None = None
        self.repository: RuntimeRepository | None = None

    def __enter__(self) -> UnitOfWork:
        if self._connection is not None:
            raise RuntimeError("UnitOfWork 不可重复进入")
        connection = self._factory.connect()
        connection.execute("BEGIN IMMEDIATE" if self._immediate else "BEGIN")
        self._connection = connection
        self.repository = RuntimeRepository(connection)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        connection = self._require_connection()
        try:
            if exc_type is None:
                connection.commit()
            else:
                connection.rollback()
        finally:
            connection.close()
            self._connection = None
            self.repository = None

    @property
    def repo(self) -> RuntimeRepository:
        """返回当前事务 Repository；在上下文外访问属于编程错误。"""
        if self.repository is None:
            raise RuntimeError("UnitOfWork 尚未进入")
        return self.repository

    def rollback(self) -> None:
        """显式回滚后立即开始同类型新事务，主要用于可恢复的批处理边界。"""
        connection = self._require_connection()
        connection.rollback()
        connection.execute("BEGIN IMMEDIATE" if self._immediate else "BEGIN")

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("UnitOfWork 尚未进入")
        return self._connection
