"""Runtime SQLite 事实源、事务与耐久队列原语。"""

from __future__ import annotations

from .database import PrivacyConnectionFactory, RuntimeConnectionFactory
from .errors import (
    ConcurrencyConflictError,
    IdempotencyConflictError,
    InvalidMetadataError,
    LeaseLostError,
    MigrationError,
    RecordNotFoundError,
    StorageError,
)
from .migrate import Migration, current_schema_version, discover_migrations, migrate
from .publication import SqlitePublicationJournal
from .records import (
    CheckpointMetadata,
    ClaimedRun,
    EventRecord,
    IdempotencyRecord,
    RunLease,
    StoredRecord,
)
from .repository import RuntimeRepository
from .store import SqliteRuntimeStore
from .uow import UnitOfWork

__all__ = [
    "CheckpointMetadata",
    "ClaimedRun",
    "ConcurrencyConflictError",
    "EventRecord",
    "IdempotencyConflictError",
    "IdempotencyRecord",
    "InvalidMetadataError",
    "LeaseLostError",
    "Migration",
    "MigrationError",
    "PrivacyConnectionFactory",
    "RecordNotFoundError",
    "RunLease",
    "RuntimeConnectionFactory",
    "RuntimeRepository",
    "SqliteRuntimeStore",
    "SqlitePublicationJournal",
    "StorageError",
    "StoredRecord",
    "UnitOfWork",
    "current_schema_version",
    "discover_migrations",
    "migrate",
]
