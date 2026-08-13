"""Runtime SQLite 对外暴露的稳定错误。"""

from __future__ import annotations


class StorageError(Exception):
    """所有存储错误的基类，避免上层依赖 ``sqlite3`` 异常类型。"""


class RecordNotFoundError(StorageError):
    """请求的领域记录不存在。"""


class ConcurrencyConflictError(StorageError):
    """CAS 版本或预期状态已经过期，调用方必须重新读取后决策。"""


class LeaseLostError(ConcurrencyConflictError):
    """Worker 的 lease owner/epoch 已失效，旧 Worker 不得继续提交。"""


class IdempotencyConflictError(StorageError):
    """同一幂等键被不同请求内容复用。"""


class MigrationError(StorageError):
    """迁移序列非法或某次迁移失败。"""


class InvalidMetadataError(StorageError):
    """事件或 checkpoint 元数据超出 Runtime DB 的安全边界。"""
