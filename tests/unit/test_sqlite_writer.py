"""Tests for SqliteWriter."""

import asyncio
import dataclasses
import os
import tempfile
from pathlib import Path

import aiosqlite
import pytest

from async_patterns.persistence.sqlite import SqliteWriter, SqliteWriterConfig


def create_temp_db_path() -> Path:
    """Create a temporary database file path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return Path(path)


def cleanup_db_path(path: Path) -> None:
    """Clean up a database file path."""
    if path.exists():
        os.unlink(path)
    wal_path = Path(str(path) + "-wal")
    if wal_path.exists():
        os.unlink(wal_path)
    shm_path = Path(str(path) + "-shm")
    if shm_path.exists():
        os.unlink(shm_path)


@dataclasses.dataclass
class SampleRecord:
    """Sample dataclass for testing."""

    url: str
    status_code: int
    latency_ms: float
    timestamp: float
    attempt: int
    error: str | None = None


@pytest.mark.asyncio
async def test_schema_creation() -> None:
    """Test that table and indexes are created on first write."""
    db_path = create_temp_db_path()
    try:
        config = SqliteWriterConfig(
            db_path=db_path,
            table_name="request_results",
            buffer_size=5,
        )
        assert not db_path.exists()
        async with SqliteWriter(config) as writer:
            await writer.write({"url": "https://example.com", "status_code": 200})
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='request_results'"
            )
            row: aiosqlite.Row | None = await cursor.fetchone()
            assert row is not None
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_url'"
            )
            row = await cursor.fetchone()
            assert row is not None
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_timestamp'"
            )
            row = await cursor.fetchone()
            assert row is not None
        finally:
            await db.close()
    finally:
        cleanup_db_path(db_path)


@pytest.mark.asyncio
async def test_write_single_record() -> None:
    """Test writing a single record."""
    db_path = create_temp_db_path()
    try:
        config = SqliteWriterConfig(db_path=db_path, buffer_size=10)
        async with SqliteWriter(config) as writer:
            record = {"url": "https://example.com", "status_code": 200, "latency_ms": 150.5}
            await writer.write(record)
            await writer.flush()
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute("SELECT * FROM request_results")
            rows = list(await cursor.fetchall())
            assert len(rows) == 1
            assert rows[0]["url"] == "https://example.com"
        finally:
            await db.close()
    finally:
        cleanup_db_path(db_path)


@pytest.mark.asyncio
async def test_write_batch_transaction() -> None:
    """Test batch insert in a single transaction."""
    db_path = create_temp_db_path()
    try:
        config = SqliteWriterConfig(db_path=db_path, buffer_size=10)
        async with SqliteWriter(config) as writer:
            records = [
                {"url": "https://example1.com", "status_code": 200},
                {"url": "https://example2.com", "status_code": 404},
                {"url": "https://example3.com", "status_code": 500},
            ]
            count = await writer.write_batch(records)
            assert count == 3
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute("SELECT COUNT(*) as count FROM request_results")
            row: aiosqlite.Row | None = await cursor.fetchone()
            assert row is not None
            assert row["count"] == 3
        finally:
            await db.close()
    finally:
        cleanup_db_path(db_path)


@pytest.mark.asyncio
async def test_buffer_flush_on_size() -> None:
    """Test that auto-flush triggers when buffer reaches limit."""
    db_path = create_temp_db_path()
    try:
        config = SqliteWriterConfig(db_path=db_path, buffer_size=3)
        async with SqliteWriter(config) as writer:
            for i in range(3):
                await writer.write({"url": f"https://example{i}.com"})
            assert len(writer._buffer) == 0
            for i in range(3, 5):
                await writer.write({"url": f"https://example{i}.com"})
            assert len(writer._buffer) == 2
    finally:
        cleanup_db_path(db_path)


@pytest.mark.asyncio
async def test_concurrent_writes() -> None:
    """Test thread-safe concurrent access to the writer."""
    db_path = create_temp_db_path()
    try:
        config = SqliteWriterConfig(db_path=db_path, buffer_size=100)
        async with SqliteWriter(config) as writer:

            async def write_records(start_id: int, count: int) -> None:
                for i in range(count):
                    await writer.write({"url": f"https://example{start_id + i}.com"})

            await asyncio.gather(write_records(0, 20), write_records(20, 20), write_records(40, 20))
            await writer.flush()
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute("SELECT COUNT(*) as count FROM request_results")
            row: aiosqlite.Row | None = await cursor.fetchone()
            assert row is not None
            assert row["count"] == 60
        finally:
            await db.close()
    finally:
        cleanup_db_path(db_path)


@pytest.mark.asyncio
async def test_read_all_records() -> None:
    """Test data integrity by reading all records."""
    db_path = create_temp_db_path()
    try:
        config = SqliteWriterConfig(db_path=db_path, buffer_size=5)
        records = [
            {"url": "https://site1.com", "status_code": 200, "latency_ms": 100.0},
            {"url": "https://site2.com", "status_code": 201, "latency_ms": 200.0},
            {"url": "https://site3.com", "status_code": 204, "latency_ms": 300.0},
            {"url": "https://site4.com", "status_code": 400, "latency_ms": 400.0},
            {"url": "https://site5.com", "status_code": 500, "latency_ms": 500.0},
        ]
        async with SqliteWriter(config) as writer:
            await writer.write_batch(records)
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute("SELECT * FROM request_results ORDER BY id")
            rows = list(await cursor.fetchall())
            assert len(rows) == 5
            for i, row in enumerate(rows):
                assert row["url"] == records[i]["url"]
        finally:
            await db.close()
    finally:
        cleanup_db_path(db_path)


@pytest.mark.asyncio
async def test_write_dataclass_record() -> None:
    """Test writing a dataclass record."""
    db_path = create_temp_db_path()
    try:
        config = SqliteWriterConfig(db_path=db_path, buffer_size=10)
        async with SqliteWriter(config) as writer:
            record = SampleRecord(
                url="https://dataclass.example.com",
                status_code=201,
                latency_ms=250.0,
                timestamp=1234567890.5,
                attempt=1,
                error=None,
            )
            await writer.write(dataclasses.asdict(record))
            await writer.flush()
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute("SELECT * FROM request_results")
            rows = list(await cursor.fetchall())
            assert len(rows) == 1
            assert rows[0]["url"] == "https://dataclass.example.com"
        finally:
            await db.close()
    finally:
        cleanup_db_path(db_path)


@pytest.mark.asyncio
async def test_write_to_closed_writer() -> None:
    """Test that writing to a closed writer raises an error."""
    db_path = create_temp_db_path()
    try:
        config = SqliteWriterConfig(db_path=db_path, buffer_size=10)
        writer = SqliteWriter(config)
        await writer._open()
        await writer.close()
        with pytest.raises(RuntimeError, match="Cannot write to closed writer"):
            await writer.write({"url": "https://example.com", "status_code": 200})
    finally:
        cleanup_db_path(db_path)


@pytest.mark.asyncio
async def test_write_batch_to_closed_writer() -> None:
    """Test that write_batch to a closed writer raises an error."""
    db_path = create_temp_db_path()
    try:
        config = SqliteWriterConfig(db_path=db_path, buffer_size=10)
        writer = SqliteWriter(config)
        await writer._open()
        await writer.close()
        with pytest.raises(RuntimeError, match="Cannot write to closed writer"):
            await writer.write_batch([{"url": "https://example.com", "status_code": 200}])
    finally:
        cleanup_db_path(db_path)


@pytest.mark.asyncio
async def test_close_idempotent() -> None:
    """Test that closing a writer multiple times is safe."""
    db_path = create_temp_db_path()
    try:
        config = SqliteWriterConfig(db_path=db_path, buffer_size=10)
        writer = SqliteWriter(config)
        await writer._open()
        await writer.write({"url": "https://example.com", "status_code": 200})
        await writer.close()
        await writer.close()
    finally:
        cleanup_db_path(db_path)


@pytest.mark.asyncio
async def test_flush_empty_buffer() -> None:
    """Test that flushing an empty buffer is safe."""
    db_path = create_temp_db_path()
    try:
        config = SqliteWriterConfig(db_path=db_path, buffer_size=10)
        async with SqliteWriter(config) as writer:
            await writer.flush()
    finally:
        cleanup_db_path(db_path)


@pytest.mark.asyncio
async def test_context_manager_without_enter() -> None:
    """Test that write opens connection if not already open."""
    db_path = create_temp_db_path()
    try:
        config = SqliteWriterConfig(db_path=db_path, buffer_size=10)
        writer = SqliteWriter(config)
        await writer.write({"url": "https://example.com", "status_code": 200})
        await writer.flush()
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute("SELECT COUNT(*) as count FROM request_results")
            row: aiosqlite.Row | None = await cursor.fetchone()
            assert row is not None
            assert row["count"] == 1
        finally:
            await db.close()
        await writer.close()
    finally:
        cleanup_db_path(db_path)
