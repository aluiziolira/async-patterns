"""SQLite writer with buffered async writes using aiosqlite."""

import asyncio
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

import aiosqlite

from async_patterns.persistence.base import StorageWriter


@dataclass
class SqliteWriterConfig:
    """Configuration for SqliteWriter.

    Attributes:
        db_path: Path to the SQLite database file.
        table_name: Name of the table to write to.
        buffer_size: Number of records to buffer before auto-flush.
        flush_interval: Seconds between automatic flushes.
    """

    db_path: Path
    table_name: str = "request_results"
    buffer_size: int = 100
    flush_interval: float = 5.0


class SqliteWriter(StorageWriter):
    """SQLite writer with buffered async writes using aiosqlite.

    Batches records in memory and inserts them in a single transaction when
    the buffer reaches `buffer_size` or when explicitly flushed.

    Example:
        ```python
        async with SqliteWriter(SqliteWriterConfig("results.db")) as writer:
            await writer.write({"url": "https://example.com", "status_code": 200})
            await writer.write_batch([...])
        ```
    """

    def __init__(self, config: SqliteWriterConfig) -> None:
        """Initialize the SQLite writer.

        Args:
            config: Writer configuration.
        """
        self._config = config
        self._buffer: list[dict[str, Any]] = []
        self._db: aiosqlite.Connection | None = None
        self._closed = False
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        """Enter async context manager and open the database.

        Returns:
            Self for context manager protocol.
        """
        await self._open()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit async context manager and close the database.

        Args:
            exc_type: Exception type if an error occurred.
            exc_val: Exception value if an error occurred.
            exc_tb: Exception traceback if an error occurred.
        """
        await self.close()

    async def _open(self) -> None:
        """Open the SQLite database connection."""
        self._db = await aiosqlite.connect(
            self._config.db_path,
            isolation_level=None,
        )
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA synchronous = NORMAL")
        await self._ensure_schema()

    async def _ensure_schema(self) -> None:
        """Create the table and indexes if they don't exist."""
        if self._db is None:
            raise RuntimeError("Database connection not open")

        table_name = self._config.table_name
        await self._db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                status_code INTEGER,
                latency_ms REAL,
                timestamp REAL,
                attempt INTEGER,
                error TEXT,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
            """
        )
        await self._db.execute(f"CREATE INDEX IF NOT EXISTS idx_url ON {table_name}(url)")
        await self._db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_timestamp ON {table_name}(timestamp)"
        )
        await self._db.commit()

    async def write(self, record: dict[str, Any]) -> None:
        """Write a single record to the SQLite database.

        Adds record to internal buffer. Auto-flushes when buffer reaches
        `buffer_size`.

        Args:
            record: Dictionary to write to the database.
        """
        if self._closed:
            raise RuntimeError("Cannot write to closed writer")

        if self._db is None:
            await self._open()

        if dataclasses.is_dataclass(record) and not isinstance(record, type):
            record_dict = dataclasses.asdict(record)
        elif hasattr(record, "__dataclass_fields__"):
            record_dict = record.__dict__ if hasattr(record, "__dict__") else dict(record)
        else:
            record_dict = record

        async with self._lock:
            self._buffer.append(record_dict)

            if len(self._buffer) >= self._config.buffer_size:
                await self._flush_locked()

    async def write_batch(self, records: list[dict[str, Any]]) -> int:
        """Write multiple records to the SQLite database.

        Adds all records to the buffer and then flushes.

        Args:
            records: List of dictionaries to write.

        Returns:
            Number of records written.
        """
        if self._closed:
            raise RuntimeError("Cannot write to closed writer")

        for record in records:
            await self.write(record)

        return len(records)

    async def flush(self) -> None:
        """Flush all buffered records to the database."""
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        """Internal flush method called with lock held."""
        if not self._db or not self._buffer:
            return

        table_name = self._config.table_name
        columns = list(self._buffer[0].keys())

        placeholders = ", ".join(f":{col}" for col in columns)
        column_names = ", ".join(columns)

        await self._db.execute("BEGIN TRANSACTION")
        try:
            for record in self._buffer:
                await self._db.execute(
                    f"""
                    INSERT INTO {table_name} ({column_names})
                    VALUES ({placeholders})
                    """,
                    record,
                )
            await self._db.execute("COMMIT")
            self._buffer.clear()
        except Exception:
            await self._db.execute("ROLLBACK")
            raise

    async def close(self) -> None:
        """Close the writer and release resources.

        Flushes any remaining buffered data and closes the database connection.
        """
        if self._closed:
            return

        async with self._lock:
            await self._flush_locked()

        self._closed = True

        if self._db:
            await self._db.close()
            self._db = None
