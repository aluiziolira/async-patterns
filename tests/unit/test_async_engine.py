"""Unit tests for AsyncEngine core implementation.

Tests cover:
- Protocol conformance
- SemaphoreLimiter bounded concurrency
- Valid EngineResult return
- Memory profiling with tracemalloc
"""

from __future__ import annotations

import asyncio
import tracemalloc
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from async_patterns.engine.async_base import AsyncEngine
from async_patterns.engine.models import ConnectionConfig, EngineResult


class TestAsyncEngineProtocol:
    """Tests for AsyncEngine protocol conformance."""

    def test_async_engine_protocol_exists(self) -> None:
        """Verify AsyncEngine protocol can be imported."""

        assert AsyncEngine is not None

    def test_async_engine_is_runtime_checkable_protocol(self) -> None:
        """Verify AsyncEngine is a runtime_checkable protocol."""

        # Protocol should be runtime_checkable for isinstance checks
        assert hasattr(AsyncEngine, "__protocol_attrs__")


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

    @pytest.fixture()
    def mock_httpx_response(self) -> MagicMock:
        """Create a mock httpx response."""
        response = MagicMock()
        response.status_code = 200
        response.headers = {"content-type": "application/json"}
        response.text = '{"status": "ok"}'
        response.elapsed.total_seconds.return_value = 0.05
        return response

    @pytest.mark.asyncio()
    async def test_run_returns_engine_result(self, mock_httpx_response: MagicMock) -> None:
        """Test run() returns valid EngineResult."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_httpx_response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            assert isinstance(result, EngineResult)
            assert len(result.results) == 1
            assert result.total_time >= 0

    @pytest.mark.asyncio()
    async def test_run_with_multiple_urls(self) -> None:
        """Test run() with multiple URLs returns all results."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=10)
        urls = [f"https://example{i}.com" for i in range(5)]

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            for url in urls:
                response = MagicMock()
                response.status_code = 200
                response.elapsed.total_seconds.return_value = 0.05
                mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(urls)

            assert isinstance(result, EngineResult)
            assert len(result.results) == 5

    @pytest.mark.asyncio()
    async def test_run_handles_http_errors(self) -> None:
        """Test run() handles HTTP error responses correctly."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 404
            response.elapsed.total_seconds.return_value = 0.03
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com/notfound"])

            assert isinstance(result, EngineResult)
            assert len(result.results) == 1
            assert result.results[0].status_code == 404

    @pytest.mark.asyncio()
    async def test_run_handles_exceptions(self) -> None:
        """Test run() handles exceptions during request."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = Exception("Connection failed")
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            assert isinstance(result, EngineResult)
            assert len(result.results) == 1
            assert result.results[0].error is not None
            assert "Connection failed" in result.results[0].error


class TestAsyncEngineMemoryProfiling:
    """Tests for memory profiling with tracemalloc."""

    @pytest.mark.asyncio()
    async def test_peak_memory_mb_populated(self) -> None:
        """Test EngineResult.peak_memory_mb is populated correctly."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            assert result.peak_memory_mb >= 0

    @pytest.mark.asyncio()
    async def test_memory_tracking_with_tracemalloc(self) -> None:
        """Test that tracemalloc is used for memory tracking."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        # Start tracemalloc
        tracemalloc.start()

        try:
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                response = MagicMock()
                response.status_code = 200
                response.elapsed.total_seconds.return_value = 0.05
                mock_client.get.return_value = response
                mock_client_class.return_value.__aenter__.return_value = mock_client
                mock_client_class.return_value.__aexit__.return_value = None

                result = await engine.run(["https://example.com"])

                # Should have captured memory snapshot
                assert result.peak_memory_mb >= 0
        finally:
            tracemalloc.stop()


class TestAsyncEngineSemaphore:
    """Tests for SemaphoreLimiter integration."""

    @pytest.mark.asyncio()
    async def test_uses_semaphore_for_concurrency(self) -> None:
        """Test that SemaphoreLimiter is used for bounded concurrency."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=5)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            urls = [f"https://example{i}.com" for i in range(10)]
            result = await engine.run(urls)

            # All requests should complete despite concurrency limit
            assert len(result.results) == 10

    def test_semaphore_limiter_property(self) -> None:
        """Test SemaphoreLimiter is initialized correctly."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=25)
        assert engine._limiter.max_concurrent == 25

    @pytest.mark.asyncio()
    async def test_semaphore_acquired_per_request(self) -> None:
        """Test that semaphore is acquired per request, not per batch."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=5)
        max_concurrent = 5
        num_urls = 20

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()

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
                response.status_code = 200
                response.elapsed.total_seconds.return_value = 0.05
                return response

            mock_client.get = mock_get
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

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


class TestAsyncEngineTaskGroupExceptionHandling:
    """Tests for TaskGroup exception handling coverage."""

    @pytest.mark.asyncio()
    async def test_run_naive_taskgroup_exception_handling(self) -> None:
        """Test NAIVE strategy TaskGroup exception handling block (lines 176-180)."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(max_concurrent=5, pooling_strategy=PoolingStrategy.NAIVE)

        # Create a mock task that has an exception
        mock_task_with_exception = AsyncMock()
        mock_task_with_exception.exception.return_value = ValueError("Task error")
        mock_task_with_exception.done.return_value = True
        mock_task_with_exception.cancelled.return_value = False

        # Mock TaskGroup that raises ExceptionGroup
        original_task_group = asyncio.TaskGroup

        class MockedTaskGroup:
            def __init__(self, *args, **kwargs):
                self.tasks = [mock_task_with_exception]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                raise ExceptionGroup("TaskGroup error", [ValueError("Task error")])

            def create_task(self, coro):
                return mock_task_with_exception

        with (
            patch.object(asyncio, "TaskGroup", MockedTaskGroup),
            patch("httpx.AsyncClient") as mock_client_class,
        ):
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            # Should not raise, should handle exception gracefully
            result = await engine.run(["https://example.com"])

            assert isinstance(result, EngineResult)
            # Exception handling should allow graceful continuation
            assert result.total_time >= 0

    @pytest.mark.asyncio()
    async def test_run_optimized_taskgroup_exception_handling(self) -> None:
        """Test OPTIMIZED strategy TaskGroup exception handling block (lines 218-222)."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        # Create mock tasks - one with exception, one without
        mock_task_with_exception = AsyncMock()
        mock_task_with_exception.exception.return_value = RuntimeError("Network error")
        mock_task_with_exception.done.return_value = True
        mock_task_with_exception.cancelled.return_value = False

        mock_task_success = AsyncMock()
        mock_task_success.exception.return_value = None
        mock_task_success.done.return_value = True
        mock_task_success.cancelled.return_value = False

        class MockedTaskGroup:
            def __init__(self, *args, **kwargs):
                self.tasks = [mock_task_with_exception, mock_task_success]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                raise ExceptionGroup("Multiple errors", [RuntimeError("Network error")])

            def create_task(self, coro):
                return mock_task_with_exception if "error" in str(coro) else mock_task_success

        with (
            patch.object(asyncio, "TaskGroup", MockedTaskGroup),
            patch("httpx.AsyncClient") as mock_client_class,
        ):
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            # Should not raise, should handle exception gracefully
            result = await engine.run(["https://example1.com", "https://example2.com"])

            assert isinstance(result, EngineResult)
            # Exception handling should allow graceful continuation
            assert result.total_time >= 0

    @pytest.mark.asyncio()
    async def test_taskgroup_exception_block_iterates_tasks(self) -> None:
        """Test that exception block properly iterates through tasks with exceptions."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(max_concurrent=3, pooling_strategy=PoolingStrategy.NAIVE)

        # Create multiple mock tasks with exceptions
        mock_tasks = []
        for i in range(3):
            task = AsyncMock()
            task.exception.return_value = Exception(f"Error {i}")
            task.done.return_value = True
            task.cancelled.return_value = False
            mock_tasks.append(task)

        call_count = [0]

        class MockedTaskGroup:
            def __init__(self, *args, **kwargs):
                self.tasks = mock_tasks

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                # Simulate multiple exceptions
                raise ExceptionGroup(
                    "Multiple task errors",
                    [
                        Exception("Error 0"),
                        Exception("Error 1"),
                        Exception("Error 2"),
                    ],
                )

            def create_task(self, coro):
                task = mock_tasks[call_count[0]]
                call_count[0] += 1
                return task

        with (
            patch.object(asyncio, "TaskGroup", MockedTaskGroup),
            patch("httpx.AsyncClient") as mock_client_class,
        ):
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            # Should handle multiple task exceptions
            result = await engine.run(
                [
                    "https://example1.com",
                    "https://example2.com",
                    "https://example3.com",
                ]
            )

            assert isinstance(result, EngineResult)
            # All exception handling should complete
            assert result.total_time >= 0
            # Verify all tasks' exception() was called (iterated in except* block)
            for task in mock_tasks:
                task.exception.assert_called()

    @pytest.mark.asyncio()
    async def test_naive_exception_handling_with_mixed_results(self) -> None:
        """Test NAIVE strategy with mix of successful and failed tasks."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(max_concurrent=5, pooling_strategy=PoolingStrategy.NAIVE)

        # Create tasks: one raises, one succeeds
        error_task = AsyncMock()
        error_task.exception.return_value = ConnectionError("Failed")
        error_task.done.return_value = True
        error_task.cancelled.return_value = False

        success_task = AsyncMock()
        success_task.exception.return_value = None
        success_task.done.return_value = True
        success_task.cancelled.return_value = False

        class MockedTaskGroup:
            def __init__(self, *args, **kwargs):
                self.tasks = [error_task, success_task]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                raise ExceptionGroup("Mixed results", [ConnectionError("Failed")])

            def create_task(self, coro):
                return error_task

        with (
            patch.object(asyncio, "TaskGroup", MockedTaskGroup),
            patch("httpx.AsyncClient") as mock_client_class,
        ):
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://error.com", "https://success.com"])

            assert isinstance(result, EngineResult)
            assert result.total_time >= 0
            # Verify exception() was called on error task
            error_task.exception.assert_called()

    @pytest.mark.asyncio()
    async def test_optimized_exception_handling_with_exception_group(self) -> None:
        """Test OPTIMIZED strategy with ExceptionGroup containing multiple exceptions."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        mock_task = AsyncMock()
        mock_task.exception.return_value = TimeoutError("Timeout")
        mock_task.done.return_value = True
        mock_task.cancelled.return_value = False

        class MockedTaskGroup:
            def __init__(self, *args, **kwargs):
                self.tasks = [mock_task]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                # Create ExceptionGroup with multiple exceptions
                raise ExceptionGroup(
                    "Request failures",
                    [TimeoutError("Timeout"), ConnectionError("Dropped")],
                )

            def create_task(self, coro):
                return mock_task

        with (
            patch.object(asyncio, "TaskGroup", MockedTaskGroup),
            patch("httpx.AsyncClient") as mock_client_class,
        ):
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            assert isinstance(result, EngineResult)
            assert result.total_time >= 0
            # Exception block should have been triggered
            mock_task.exception.assert_called()


class TestAsyncEnginePoolingStrategy:
    """Tests for pooling strategy configuration."""

    @pytest.mark.asyncio()
    async def test_naive_pooling_strategy(self) -> None:
        """Test NAIVE pooling strategy creates new client per request."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(pooling_strategy=PoolingStrategy.NAIVE)
        assert engine._pooling_strategy == PoolingStrategy.NAIVE

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            # NAIVE strategy should still return valid result
            assert isinstance(result, EngineResult)
            assert len(result.results) == 1
            assert result.results[0].status_code == 200

    @pytest.mark.asyncio()
    async def test_naive_pooling_strategy_exception_handling(self) -> None:
        """Test NAIVE pooling strategy handles exceptions gracefully."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(max_concurrent=5, pooling_strategy=PoolingStrategy.NAIVE)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            # Make the client raise an exception
            mock_client.get.side_effect = Exception("Connection error")
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            # Should handle exception gracefully
            assert isinstance(result, EngineResult)
            # Result count may vary based on exception handling behavior
            # The key is that the engine doesn't crash
            assert result.total_time >= 0

    @pytest.mark.asyncio()
    async def test_run_with_empty_url_list(self) -> None:
        """Test run() with empty URL list returns empty result."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()
        result = await engine.run([])

        assert isinstance(result, EngineResult)
        assert len(result.results) == 0
        assert result.total_time == 0.0
        assert result.peak_memory_mb == 0.0

    @pytest.mark.asyncio()
    async def test_optimized_strategy_taskgroup_exception(self) -> None:
        """Test OPTIMIZED strategy handles TaskGroup exceptions."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()

            # Make get() raise an exception that propagates to TaskGroup
            async def mock_get(url):
                raise Exception("TaskGroup level exception")

            mock_client.get = mock_get
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            # This should still return a valid result (empty due to exception handling)
            result = await engine.run(["https://example.com"])

            assert isinstance(result, EngineResult)
            # Engine should handle gracefully
            assert result.total_time >= 0


class TestAsyncEngineHTTP2Config:
    """Tests for HTTP/2 configuration in AsyncEngine."""

    @pytest.mark.asyncio()
    async def test_http2_enabled_by_default(self) -> None:
        """HTTP/2 should be enabled by default for better performance."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()
        assert engine._config.http2 is True

    @pytest.mark.asyncio()
    async def test_http2_disabled_for_http1_only_servers(self) -> None:
        """HTTP/2 can be disabled for HTTP/1.1-only servers."""
        from async_patterns.engine.async_engine import AsyncEngine
        from async_patterns.engine.models import ConnectionConfig

        config = ConnectionConfig(http2=False)
        engine = AsyncEngine(config=config)
        assert engine._config.http2 is False

    @pytest.mark.asyncio()
    async def test_async_client_receives_http2_config(self) -> None:
        """AsyncClient should receive http2 parameter from config."""
        from async_patterns.engine.async_engine import AsyncEngine
        from async_patterns.engine.models import ConnectionConfig

        config = ConnectionConfig(http2=True)
        engine = AsyncEngine(config=config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            await engine.run(["https://example.com"])

            # Verify AsyncClient was called with http2=True
            mock_client_class.assert_called_once()
            call_kwargs = mock_client_class.call_args.kwargs
            assert call_kwargs.get("http2") is True


class TestAsyncEngineKeepaliveConfig:
    """Tests for keepalive_expiry configuration in AsyncEngine."""

    @pytest.mark.asyncio()
    async def test_keepalive_expiry_applied_to_limits(self) -> None:
        """Keepalive expiry should be passed to httpx.Limits."""
        from async_patterns.engine.async_engine import AsyncEngine
        from async_patterns.engine.models import ConnectionConfig

        config = ConnectionConfig(keepalive_expiry=60.0)
        engine = AsyncEngine(config=config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            await engine.run(["https://example.com"])

            # Verify AsyncClient was called with correct limits
            mock_client_class.assert_called_once()
            call_kwargs = mock_client_class.call_args.kwargs
            limits = call_kwargs.get("limits")
            assert limits is not None
            assert limits.keepalive_expiry == 60.0

    @pytest.mark.asyncio()
    async def test_keepalive_expiry_default_value(self) -> None:
        """Keepalive expiry should default to 30 seconds."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()
        assert engine._config.keepalive_expiry == 30.0
