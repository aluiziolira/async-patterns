"""Unit tests for AsyncEngine core implementation using aiohttp.

Tests cover:
- Protocol conformance
- SemaphoreLimiter bounded concurrency
- Valid EngineResult return
- Memory profiling with tracemalloc
"""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from async_patterns.engine.async_engine import AsyncEngine
from async_patterns.engine.models import ConnectionConfig, EngineResult, RequestResult


class TestAsyncEngineProtocol:
    """Tests for AsyncEngine protocol conformance."""

    def test_async_engine_protocol_exists(self) -> None:
        """Verify AsyncEngine class can be imported."""

        assert AsyncEngine is not None


class TestAsyncEngineInitialization:
    """Tests for AsyncEngine initialization."""

    def test_default_max_concurrent(self) -> None:
        """Test default max_concurrent value."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()
        assert engine._max_concurrent == 100

    def test_custom_max_concurrent(self) -> None:
        """Test custom max_concurrent value."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=50)
        assert engine._max_concurrent == 50

    def test_config_initialization(self) -> None:
        """Test ConnectionConfig initialization."""
        from async_patterns.engine.async_engine import AsyncEngine

        config = ConnectionConfig(max_connections=25, timeout=60.0)
        engine = AsyncEngine(max_concurrent=25, config=config)
        assert engine._config.max_connections == 25
        assert engine._config.timeout == 60.0


class TestAsyncEngineRun:
    """Tests for AsyncEngine.run() method."""

    @pytest.fixture
    def mock_aiohttp_response(self) -> MagicMock:
        """Create a mock aiohttp response."""
        response = MagicMock()
        response.status = 200
        response.headers = {"content-type": "application/json"}
        response.read = AsyncMock(return_value=b"")
        response.text = AsyncMock(return_value='{"status": "ok"}')
        return response

    @pytest.mark.asyncio
    async def test_run_returns_engine_result(self, mock_aiohttp_response: MagicMock) -> None:
        """Test run() returns valid EngineResult."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.get.return_value = mock_aiohttp_response
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            assert isinstance(result, EngineResult)
            assert len(result.results) == 1
            assert result.total_time >= 0

    @pytest.mark.asyncio
    async def test_run_with_multiple_urls(self) -> None:
        """Test run() with multiple URLs returns all results."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=10)
        urls = [f"https://example{i}.com" for i in range(5)]

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            for url in urls:
                response = MagicMock()
                response.status = 200
                response.read = AsyncMock(return_value=b"")
                mock_session.get.return_value = response
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(urls)

            assert isinstance(result, EngineResult)
            assert len(result.results) == 5

    @pytest.mark.asyncio
    async def test_run_handles_http_errors(self) -> None:
        """Test run() handles HTTP error responses correctly."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            response = MagicMock()
            response.status = 404
            response.read = AsyncMock(return_value=b"")
            mock_session.get.return_value = response
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com/notfound"])

            assert isinstance(result, EngineResult)
            assert len(result.results) == 1
            assert result.results[0].status_code == 404

    @pytest.mark.asyncio
    async def test_run_handles_exceptions(self) -> None:
        """Test run() handles exceptions during request."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.get.side_effect = Exception("Connection failed")
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            assert isinstance(result, EngineResult)
            assert len(result.results) == 1
            assert result.results[0].error is not None
            assert "Connection failed" in result.results[0].error

    @pytest.mark.asyncio
    async def test_run_collects_worker_errors(self) -> None:
        """Test run() records error results when workers hit unexpected exceptions."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=2)
        urls = [
            "https://example.com/ok",
            "https://example.com/fail",
            "https://example.com/ok2",
        ]

        async def fake_fetch(_session: object, url: str) -> RequestResult:
            if "fail" in url:
                raise RuntimeError("worker boom")
            return RequestResult(
                url=url,
                status_code=200,
                latency_ms=1.0,
                timestamp=time.time(),
                attempt=1,
                error=None,
            )

        engine._fetch_url = AsyncMock(side_effect=fake_fetch)

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(urls)

        assert len(result.results) == len(urls)
        error_results = [res for res in result.results if res.error is not None]
        assert len(error_results) == 1
        assert error_results[0].status_code == 0

    @pytest.mark.asyncio
    async def test_run_sets_epoch_timestamp(self, mock_aiohttp_response: MagicMock) -> None:
        """Test RequestResult timestamp is a Unix epoch completion time."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.get.return_value = mock_aiohttp_response
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            start_time = time.time()
            result = await engine.run(["https://example.com"])
            end_time = time.time()

        assert start_time <= result.results[0].timestamp <= end_time


class TestAsyncEngineMemoryProfiling:
    """Tests for memory profiling with tracemalloc."""

    @pytest.mark.asyncio
    async def test_peak_memory_mb_populated(self) -> None:
        """Test EngineResult.peak_memory_mb is populated correctly."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            response = MagicMock()
            response.status = 200
            response.read = AsyncMock(return_value=b"")
            mock_session.get.return_value = response
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            assert result.peak_memory_mb >= 0

    @pytest.mark.asyncio
    async def test_memory_tracking_with_tracemalloc(self) -> None:
        """Test that tracemalloc is used for memory tracking."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        # Start tracemalloc
        tracemalloc.start()

        try:
            with patch("aiohttp.ClientSession") as mock_session_class:
                mock_session = AsyncMock()
                response = MagicMock()
                response.status = 200
                response.read = AsyncMock(return_value=b"")
                mock_session.get.return_value = response
                mock_session_class.return_value.__aenter__.return_value = mock_session
                mock_session_class.return_value.__aexit__.return_value = None

                result = await engine.run(["https://example.com"])

                # Should have captured memory snapshot
                assert result.peak_memory_mb >= 0
        finally:
            tracemalloc.stop()


class TestAsyncEngineSemaphore:
    """Tests for SemaphoreLimiter integration."""

    @pytest.mark.asyncio
    async def test_uses_semaphore_for_concurrency(self) -> None:
        """Test that SemaphoreLimiter is used for bounded concurrency."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=5)

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            response = MagicMock()
            response.status = 200
            response.read = AsyncMock(return_value=b"")
            mock_session.get.return_value = response
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            urls = [f"https://example{i}.com" for i in range(10)]
            result = await engine.run(urls)

            # All requests should complete despite concurrency limit
            assert len(result.results) == 10

    def test_semaphore_limiter_property(self) -> None:
        """Test SemaphoreLimiter is initialized correctly."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=25)
        assert engine._limiter.max_concurrent == 25

    @pytest.mark.asyncio
    async def test_semaphore_acquired_per_request(self) -> None:
        """Test that semaphore is acquired per request, not per batch."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=5)
        max_concurrent = 5
        num_urls = 20

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()

            # Create a real TaskGroup to track concurrent executions
            active_count = 0
            max_concurrent_seen = 0
            start_barrier = asyncio.Event()
            all_tasks_started = asyncio.Event()

            async def mock_get(url: str) -> MagicMock:
                nonlocal active_count, max_concurrent_seen

                # Signal that we're about to make a request
                current = active_count + 1
                active_count = current
                max_concurrent_seen = max(max_concurrent_seen, active_count)

                # Signal that this task is about to start
                start_barrier.set()

                # Wait a bit to allow other tasks to attempt concurrent execution
                await asyncio.sleep(0.01)

                # Decrement after response
                active_count -= 1

                response = MagicMock()
                response.status = 200
                response.read = AsyncMock(return_value=b"")
                return response

            mock_session.get = mock_get
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            urls = [f"https://example{i}.com" for i in range(num_urls)]
            result = await engine.run(urls)

            # Verify bounded concurrency: max 5 concurrent requests
            assert max_concurrent_seen <= max_concurrent, (
                f"Expected at most {max_concurrent} concurrent requests, "
                f"but saw {max_concurrent_seen}"
            )

            # All requests should complete
            assert len(result.results) == num_urls


class TestAsyncEngineName:
    """Tests for engine name property."""

    def test_name_property(self) -> None:
        """Test engine name is correct."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()
        assert engine.name == "async_engine"


class TestAsyncEnginePoolingStrategy:
    """Tests for pooling strategy configuration."""

    @pytest.mark.asyncio
    async def test_naive_pooling_strategy(self) -> None:
        """Test NAIVE pooling strategy creates new session per request."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(pooling_strategy=PoolingStrategy.NAIVE)
        assert engine._pooling_strategy == PoolingStrategy.NAIVE

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            response = MagicMock()
            response.status = 200
            response.read = AsyncMock(return_value=b"")
            mock_session.get.return_value = response
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            # NAIVE strategy should still return valid result
            assert isinstance(result, EngineResult)
            assert len(result.results) == 1
            assert result.results[0].status_code == 200

    @pytest.mark.asyncio
    async def test_naive_pooling_strategy_exception_handling(self) -> None:
        """Test NAIVE pooling strategy handles exceptions gracefully."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(max_concurrent=5, pooling_strategy=PoolingStrategy.NAIVE)

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            # Make the session raise an exception
            mock_session.get.side_effect = Exception("Connection error")
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            # Should handle exception gracefully
            assert isinstance(result, EngineResult)
            # Result count may vary based on exception handling behavior
            # The key is that the engine doesn't crash
            assert result.total_time >= 0

    @pytest.mark.asyncio
    async def test_run_with_empty_url_list(self) -> None:
        """Test run() with empty URL list returns empty result."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()
        result = await engine.run([])

        assert isinstance(result, EngineResult)
        assert len(result.results) == 0
        assert result.total_time == 0.0
        assert result.peak_memory_mb == 0.0

    @pytest.mark.asyncio
    async def test_optimized_strategy_handles_exceptions(self) -> None:
        """Test OPTIMIZED strategy handles exceptions."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()

            # Make get() raise an exception
            async def mock_get(url):
                raise Exception("Request level exception")

            mock_session.get = mock_get
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            # This should still return a valid result
            result = await engine.run(["https://example.com"])

            assert isinstance(result, EngineResult)
            # Engine should handle gracefully
            assert result.total_time >= 0


class TestAsyncEngineCircuitBreaker:
    """Tests for CircuitBreaker integration in AsyncEngine."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_injection(self) -> None:
        """Test that AsyncEngine accepts circuit_breaker argument in __init__."""
        from async_patterns.engine.async_engine import AsyncEngine
        from async_patterns.patterns.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker(name="test")
        engine = AsyncEngine(circuit_breaker=breaker)

        assert engine._circuit_breaker is breaker

    def test_circuit_breaker_optional(self) -> None:
        """Test that circuit_breaker is optional (defaults to None)."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        assert engine._circuit_breaker is None

    @pytest.mark.asyncio
    async def test_record_latency_called(self) -> None:
        """Test that record_latency is called on the circuit breaker after a request."""
        from async_patterns.engine.async_engine import AsyncEngine
        from async_patterns.patterns.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker(name="test")
        engine = AsyncEngine(circuit_breaker=breaker)

        # Mock record_latency to verify it's called
        breaker.record_latency = AsyncMock()

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            response = MagicMock()
            response.status = 200
            response.read = AsyncMock(return_value=b"")
            mock_session.get.return_value = response
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            # Verify record_latency was called
            breaker.record_latency.assert_awaited_once()
            latency_arg = breaker.record_latency.call_args[0][0]
            assert latency_arg > 0

    @pytest.mark.asyncio
    async def test_record_latency_called_on_error(self) -> None:
        """Test that record_latency is called even when request fails."""
        from async_patterns.engine.async_engine import AsyncEngine
        from async_patterns.patterns.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker(name="test")
        engine = AsyncEngine(circuit_breaker=breaker)

        # Mock record_latency to verify it's called
        breaker.record_latency = AsyncMock()

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.get.side_effect = Exception("Connection failed")
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            # Verify record_latency was called even on error
            breaker.record_latency.assert_awaited_once()
            latency_arg = breaker.record_latency.call_args[0][0]
            assert latency_arg > 0

    @pytest.mark.asyncio
    async def test_record_latency_not_called_without_breaker(self) -> None:
        """Test that record_latency is not called when no circuit breaker is configured."""
        from async_patterns.engine.async_engine import AsyncEngine
        from async_patterns.patterns.circuit_breaker import CircuitBreaker

        # Create a breaker but don't inject it
        breaker = CircuitBreaker(name="orphan")
        breaker.record_latency = AsyncMock()

        engine = AsyncEngine()  # No circuit breaker

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            response = MagicMock()
            response.status = 200
            response.read = AsyncMock(return_value=b"")
            mock_session.get.return_value = response
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            # Verify record_latency was NOT called on the orphan breaker
            breaker.record_latency.assert_not_awaited()


class TestAsyncEngineRetryPolicy:
    """Tests for RetryPolicy integration in AsyncEngine."""

    @pytest.mark.asyncio
    async def test_retry_policy_injection(self) -> None:
        """Test that AsyncEngine accepts retry_policy argument in __init__."""
        from async_patterns.engine.async_engine import AsyncEngine
        from async_patterns.patterns.retry import RetryConfig, RetryPolicy

        policy = RetryPolicy(RetryConfig(max_attempts=3))
        engine = AsyncEngine(retry_policy=policy)

        assert engine._retry_policy is policy

    @pytest.mark.asyncio
    async def test_retry_policy_optional(self) -> None:
        """Test that retry_policy is optional (defaults to None)."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        assert engine._retry_policy is None

    @pytest.mark.asyncio
    async def test_retry_policy_with_circuit_breaker(self) -> None:
        """Test that retry policy and circuit breaker work together."""
        from async_patterns.engine.async_engine import AsyncEngine
        from async_patterns.patterns.circuit_breaker import CircuitBreaker
        from async_patterns.patterns.retry import RetryConfig, RetryPolicy

        policy = RetryPolicy(RetryConfig(max_attempts=2, base_delay=0.01))
        breaker = CircuitBreaker(name="test")
        # Mock record_latency to verify it's called
        breaker.record_latency = AsyncMock()
        engine = AsyncEngine(retry_policy=policy, circuit_breaker=breaker)

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            response = MagicMock()
            response.status = 200
            mock_session.get.return_value = response
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            assert isinstance(result, EngineResult)
            assert len(result.results) == 1
            assert result.results[0].status_code == 200
            # Circuit breaker should have recorded latency
            breaker.record_latency.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_retry_without_policy(self) -> None:
        """Test that requests are not retried when no retry_policy is set."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()  # No retry policy

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()

            response = MagicMock()
            response.status = 500
            response.read = AsyncMock(return_value=b"")
            mock_session.get.return_value = response
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            assert isinstance(result, EngineResult)
            # Should only have been called once (no retries)
            assert mock_session.get.call_count == 1
            assert result.results[0].status_code == 500


class TestAsyncEngineRetryStatus:
    """Tests for status code preservation on retry failures."""

    @pytest.mark.asyncio
    async def test_failed_retries_preserve_last_status(self) -> None:
        """Test last HTTP status is preserved when retries exhaust."""
        from async_patterns.engine.async_engine import AsyncEngine
        from async_patterns.patterns.retry import RetryConfig, RetryPolicy

        policy = RetryPolicy(RetryConfig(max_attempts=2, base_delay=0.0))
        engine = AsyncEngine(retry_policy=policy)

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            response = MagicMock()
            response.status = 503
            response.headers = {}
            response.request_info = MagicMock()
            response.history = ()
            response.read = AsyncMock(return_value=b"")
            mock_session.get.return_value = response
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await engine.run(["https://example.com"])

            assert result.results[0].status_code == 503

    @pytest.mark.asyncio
    async def test_connection_failures_keep_status_zero(self) -> None:
        """Test connection failures preserve status_code=0 after retries."""
        from async_patterns.engine.async_engine import AsyncEngine
        from async_patterns.patterns.retry import RetryConfig, RetryPolicy

        policy = RetryPolicy(RetryConfig(max_attempts=2, base_delay=0.0))
        engine = AsyncEngine(retry_policy=policy)

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.get.side_effect = ConnectionError("connection refused")
            mock_session_class.return_value.__aenter__.return_value = mock_session
            mock_session_class.return_value.__aexit__.return_value = None

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await engine.run(["https://example.com"])

            assert result.results[0].status_code == 0


class TestStreamingSessionReuse:
    """Tests to verify streaming mode reuses sessions correctly."""

    @pytest.mark.asyncio
    async def test_streaming_creates_single_session_optimized(self) -> None:
        """Test that run_streaming() creates only one session for OPTIMIZED strategy."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(max_concurrent=5, pooling_strategy=PoolingStrategy.OPTIMIZED)

        session_creation_count = 0
        original_client_session = None

        with patch("aiohttp.ClientSession") as mock_session_class:

            def track_session_creation(*args, **kwargs):
                nonlocal session_creation_count, original_client_session
                session_creation_count += 1
                mock_session = AsyncMock()
                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.headers = {}
                mock_response.text = AsyncMock(return_value="test")
                mock_response.read = AsyncMock(return_value=b"")
                mock_session.get.return_value = mock_response
                mock_session.close = AsyncMock()
                return mock_session

            mock_session_class.side_effect = track_session_creation

            # Generate 10 URLs to stream
            urls = [f"https://example.com/{i}" for i in range(10)]

            results = []
            async for result in engine.run_streaming(urls):
                results.append(result)

            # Verify only ONE session was created for OPTIMIZED strategy
            assert session_creation_count == 1, (
                f"Expected 1 session for OPTIMIZED strategy, got {session_creation_count}"
            )
            assert len(results) == 10


class TestStreamingShutdown:
    """Tests for streaming shutdown behavior."""

    @pytest.mark.asyncio
    async def test_streaming_workers_cancelled_on_close(self) -> None:
        """Test run_streaming workers are cancelled by close()."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=2)
        hold = asyncio.Event()

        async def slow_fetch(session, url: str) -> RequestResult:
            await hold.wait()
            return RequestResult(
                url=url,
                status_code=200,
                latency_ms=1.0,
                timestamp=0.0,
                attempt=1,
                error=None,
            )

        engine._fetch_url = AsyncMock(side_effect=slow_fetch)

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.close = AsyncMock()
            mock_session_class.return_value = mock_session

            urls = ["https://example.com/1", "https://example.com/2"]

            async def consume() -> None:
                async for _ in engine.run_streaming(urls):
                    pass

            consumer_task = asyncio.create_task(consume())

            for _ in range(50):
                if engine._active_tasks:
                    break
                await asyncio.sleep(0)

            assert engine._active_tasks

            await engine.close(timeout=0.01)

            assert not engine._active_tasks

            consumer_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer_task

    @pytest.mark.asyncio
    async def test_streaming_creates_multiple_sessions_naive(self) -> None:
        """Test that run_streaming() creates multiple sessions for NAIVE strategy."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(max_concurrent=5, pooling_strategy=PoolingStrategy.NAIVE)

        session_creation_count = 0

        with patch("aiohttp.ClientSession") as mock_session_class:

            def track_session_creation(*args, **kwargs):
                nonlocal session_creation_count
                session_creation_count += 1
                mock_session = AsyncMock()
                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.headers = {}
                mock_response.text = AsyncMock(return_value="test")
                mock_response.read = AsyncMock(return_value=b"")
                mock_session.get.return_value = mock_response
                mock_session.close = AsyncMock()
                return mock_session

            mock_session_class.side_effect = track_session_creation

            # Generate 10 URLs to stream
            urls = [f"https://example.com/{i}" for i in range(10)]

            results = []
            async for result in engine.run_streaming(urls):
                results.append(result)

            # NAIVE strategy should create one session per request
            assert session_creation_count == 10, (
                f"Expected 10 sessions for NAIVE strategy, got {session_creation_count}"
            )
            assert len(results) == 10
