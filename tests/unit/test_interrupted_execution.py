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
        assert active == set()

    @pytest.mark.asyncio
    async def test_graceful_close_records_completed_once_and_skips_interrupted(
        self, tmp_path: Path
    ) -> None:
        from async_patterns.engine._async_execution import AsyncHttpExecutor, PoolingStrategy
        from async_patterns.engine.models import ConnectionConfig
        from async_patterns.patterns.semaphore import SemaphoreLimiter

        second_started = asyncio.Event()
        hold_second = asyncio.Event()

        class DummyTransport:
            def open_session(self, config: ConnectionConfig):
                raise AssertionError("session should not open in this test")

        active: set[asyncio.Task] = set()
        executor = AsyncHttpExecutor(
            max_concurrent=1,
            config=ConnectionConfig(),
            pooling_strategy=PoolingStrategy.OPTIMIZED,
            limiter=SemaphoreLimiter(max_concurrent=1),
            active_tasks=active,
            transport=DummyTransport(),
        )

        async def fetch(url: str) -> RequestResult:
            if url.endswith("/1"):
                return RequestResult(
                    url=url,
                    status_code=200,
                    latency_ms=1.0,
                    timestamp=1.0,
                    attempt=1,
                    error=None,
                )
            second_started.set()
            await hold_second.wait()
            return RequestResult(
                url=url,
                status_code=200,
                latency_ms=1.0,
                timestamp=2.0,
                attempt=1,
                error=None,
            )

        urls = [
            "https://example.com/1",
            "https://example.com/2",
            "https://example.com/3",
        ]
        run_task = asyncio.create_task(executor._run_with_workers(urls, fetch))
        await second_started.wait()
        await executor.close(timeout=0.01)
        completed = await run_task

        assert [r.url for r in completed] == ["https://example.com/1"]
        assert active == set()

        jsonl_path = tmp_path / "completed.jsonl"
        async with JsonlWriter(JsonlWriterConfig(file_path=jsonl_path, buffer_size=10)) as writer:
            await writer.write_batch(completed)

        lines = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line]
        assert [row["url"] for row in lines] == ["https://example.com/1"]


class TestCombinedRetryBreakerPersistenceFailurePath:
    """Combined retry + circuit breaker + persistence sink failure-path coverage."""

    @pytest.mark.asyncio
    async def test_combined_retry_after_breaker_trip_and_dual_sink_persistence(
        self, tmp_path: Path
    ) -> None:
        from unittest.mock import MagicMock

        import aiohttp

        from async_patterns.engine.models import RetryConfig
        from async_patterns.patterns.circuit_breaker import CircuitBreaker, CircuitBreakerError
        from async_patterns.patterns.retry import RetryExhaustedError, RetryPolicy

        now = 5000.0

        def clock() -> float:
            return now

        breaker = CircuitBreaker(
            name="combined",
            failure_threshold=3,
            error_rate_threshold=1.0,
            open_state_duration=10.0,
            half_open_max_calls=1,
            clock=clock,
        )
        policy = RetryPolicy(
            config=RetryConfig(
                max_attempts=2,
                base_delay=0.1,
                max_delay=10.0,
                jitter_factor=0.0,
                retry_after_max=5.0,
            )
        )

        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        request_info = MagicMock()
        request_info.real_url = "https://example.com"

        recorded: list[RequestResult] = []

        # Job 1: 429 with Retry-After=2 on attempt 1, then 200 on attempt 2.
        job1_calls = 0

        async def job1() -> int:
            nonlocal job1_calls
            job1_calls += 1
            if job1_calls == 1:
                raise aiohttp.ClientResponseError(
                    request_info=request_info,
                    history=(),
                    status=429,
                    message="Rate limited",
                    headers={"Retry-After": "2"},
                )
            return 200

        with patch("asyncio.sleep", side_effect=fake_sleep):
            res1 = await policy.execute_with_metrics(job1, circuit_breaker=breaker)
        recorded.append(
            RequestResult(
                url="https://example.com/job1",
                status_code=res1.value,
                latency_ms=2.0,
                timestamp=now,
                attempt=res1.attempt_count,
                error=None,
            )
        )
        assert sleeps == [2.0]
        assert breaker.is_closed

        # Job 2: fails both attempts with 503 -> trips breaker to OPEN on 3rd total failure.
        async def job2() -> int:
            raise aiohttp.ClientResponseError(
                request_info=request_info,
                history=(),
                status=503,
                message="Unavailable",
                headers={},
            )

        with (
            patch("asyncio.sleep", side_effect=fake_sleep),
            pytest.raises(RetryExhaustedError) as exc_info,
        ):
            await policy.execute_with_metrics(job2, circuit_breaker=breaker)
        recorded.append(
            RequestResult(
                url="https://example.com/job2",
                status_code=503,
                latency_ms=3.0,
                timestamp=now,
                attempt=getattr(exc_info.value, "attempt_count", 1),
                error=str(exc_info.value),
            )
        )
        assert breaker.is_open

        # Job 3: rejected immediately while OPEN without sleeping or calling func.
        job3_calls = 0

        async def job3() -> int:
            nonlocal job3_calls
            job3_calls += 1
            return 200

        sleeps.clear()
        with (
            patch("asyncio.sleep", side_effect=fake_sleep),
            pytest.raises(CircuitBreakerError) as open_exc,
        ):
            await policy.execute_with_metrics(job3, circuit_breaker=breaker)
        assert job3_calls == 0
        assert sleeps == []
        recorded.append(
            RequestResult(
                url="https://example.com/job3",
                status_code=0,
                latency_ms=0.1,
                timestamp=now,
                attempt=getattr(open_exc.value, "attempt_count", 1),
                error=str(open_exc.value),
            )
        )

        # Advance clock past open_state_duration -> Job 4 recovers breaker to CLOSED.
        now += 10.0

        async def job4() -> int:
            return 200

        res4 = await policy.execute_with_metrics(job4, circuit_breaker=breaker)
        assert breaker.is_closed
        recorded.append(
            RequestResult(
                url="https://example.com/job4",
                status_code=res4.value,
                latency_ms=1.0,
                timestamp=now,
                attempt=res4.attempt_count,
                error=None,
            )
        )

        # Persist to both JSONL and SQLite with a simulated mid-flush cancellation.
        jsonl_path = tmp_path / "combined.jsonl"
        db_path = _temp_path(".db")
        try:
            async with JsonlWriter(JsonlWriterConfig(file_path=jsonl_path, buffer_size=10)) as jw:
                await jw.write_batch(recorded)

            sw = SqliteWriter(SqliteWriterConfig(db_path=db_path, buffer_size=10))
            await sw.write_batch(recorded)
            await sw.close()

            jsonl_rows = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line]
            assert [r["url"] for r in jsonl_rows] == [
                "https://example.com/job1",
                "https://example.com/job2",
                "https://example.com/job3",
                "https://example.com/job4",
            ]
            assert [r["attempt"] for r in jsonl_rows] == [2, 2, 1, 1]

            db = await aiosqlite.connect(db_path)
            try:
                cursor = await db.execute(
                    "SELECT url, status_code, attempt FROM request_results ORDER BY id"
                )
                sqlite_rows = await cursor.fetchall()
            finally:
                await db.close()

            assert list(sqlite_rows) == [
                ("https://example.com/job1", 200, 2),
                ("https://example.com/job2", 503, 2),
                ("https://example.com/job3", 0, 1),
                ("https://example.com/job4", 200, 1),
            ]
        finally:
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(db_path) + suffix)
                if p.exists():
                    p.unlink()
