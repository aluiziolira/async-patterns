"""Tests for cancellation / shutdown correctness in sinks and workers."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from async_patterns.engine.models import RequestResult
from async_patterns.persistence.jsonl import JsonlWriter, JsonlWriterConfig
from async_patterns.persistence.sqlite import SqliteWriter, SqliteWriterConfig


def _temp_path(suffix: str) -> Path:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    os.unlink(path)
    return Path(path)


class TestJsonlInterruptedFlush:
    """JSONL sink must not double-record when a flush is cancelled."""

    @pytest.mark.asyncio
    async def test_cancelled_flush_then_close_writes_each_record_once(self, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        config = JsonlWriterConfig(file_path=path, buffer_size=100)
        records = [
            {"url": "https://example.com/1", "status_code": 200},
            {"url": "https://example.com/2", "status_code": 200},
        ]

        async with JsonlWriter(config) as writer:
            for record in records:
                await writer.write(record)

            original_write_records = writer._write_records

            async def cancel_once(batch: list[dict]) -> None:
                raise asyncio.CancelledError()

            writer._write_records = cancel_once  # type: ignore[method-assign]
            with pytest.raises(asyncio.CancelledError):
                await writer.flush()

            # Buffer retained; a later successful flush/close records once.
            writer._write_records = original_write_records  # type: ignore[method-assign]
            await writer.close()

        lines = [line for line in path.read_text().splitlines() if line]
        assert len(lines) == 2
        urls = [json.loads(line)["url"] for line in lines]
        assert urls == ["https://example.com/1", "https://example.com/2"]


class TestSqliteInterruptedFlush:
    """SQLite sink rolls back cancelled transactions and records once on close."""

    @pytest.mark.asyncio
    async def test_cancelled_transaction_rolls_back_and_close_writes_once(self) -> None:
        db_path = _temp_path(".db")
        try:
            config = SqliteWriterConfig(db_path=db_path, buffer_size=100)
            writer = SqliteWriter(config)
            await writer.write({"url": "https://example.com/a", "status_code": 200})
            await writer.write({"url": "https://example.com/b", "status_code": 201})

            assert writer._db is not None
            real_execute = writer._db.execute

            async def flaky_execute(sql: str, parameters: object | None = None):
                if sql.strip().upper().startswith("INSERT"):
                    raise asyncio.CancelledError()
                if parameters is None:
                    return await real_execute(sql)
                return await real_execute(sql, parameters)

            with patch.object(writer._db, "execute", side_effect=flaky_execute):
                with pytest.raises(asyncio.CancelledError):
                    await writer.flush()

            await writer.close()

            db = await aiosqlite.connect(db_path)
            try:
                cursor = await db.execute("SELECT url FROM request_results ORDER BY id")
                rows = [row[0] for row in await cursor.fetchall()]
            finally:
                await db.close()

            assert rows == ["https://example.com/a", "https://example.com/b"]
        finally:
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(db_path) + suffix)
                if p.exists():
                    p.unlink()


class TestCircuitBreakerIgnoresCancellation:
    """Cancellation must not trip the breaker or consume half-open slots."""

    @pytest.mark.asyncio
    async def test_cancelled_half_open_trial_frees_admission_slot(self) -> None:
        from async_patterns.patterns.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker(
            name="cancel-slot",
            failure_threshold=1,
            open_state_duration=0.01,
            half_open_max_calls=1,
            probe_count=1,
        )
        with pytest.raises(ValueError, match="boom"):
            async with breaker:
                raise ValueError("boom")
        await asyncio.sleep(0.02)

        with pytest.raises(asyncio.CancelledError):
            async with breaker:
                raise asyncio.CancelledError()

        # Slot freed: another trial can be admitted.
        async with breaker:
            pass
        assert breaker.is_closed


class TestEngineCancelledInFlight:
    """Cancelled in-flight engine work is not double-recorded."""

    @pytest.mark.asyncio
    async def test_cancelled_worker_does_not_append_result(self) -> None:
        from async_patterns.engine._async_execution import AsyncHttpExecutor, PoolingStrategy
        from async_patterns.engine.models import ConnectionConfig
        from async_patterns.patterns.semaphore import SemaphoreLimiter

        hold = asyncio.Event()
        started = asyncio.Event()

        class SlowTransport:
            def open_session(self, config: ConnectionConfig):
                raise AssertionError("session should not open in this test")

        limiter = SemaphoreLimiter(max_concurrent=1)
        active: set[asyncio.Task] = set()
        executor = AsyncHttpExecutor(
            max_concurrent=1,
            config=ConnectionConfig(),
            pooling_strategy=PoolingStrategy.OPTIMIZED,
            limiter=limiter,
            active_tasks=active,
            transport=SlowTransport(),
        )

        async def blocked_fetch(url: str) -> RequestResult:
            started.set()
            await hold.wait()
            return RequestResult(
                url=url,
                status_code=200,
                latency_ms=1.0,
                timestamp=1.0,
                attempt=1,
                error=None,
            )

        with patch.object(executor, "_run_optimized", AsyncMock()) as run_opt:
            # Exercise the worker path directly.
            run_opt.side_effect = None

        results_holder: list[list[RequestResult]] = []

        async def run_workers() -> None:
            results = await executor._run_with_workers(
                ["https://example.com/1"],
                blocked_fetch,
            )
            results_holder.append(results)

        task = asyncio.create_task(run_workers())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert results_holder == []
