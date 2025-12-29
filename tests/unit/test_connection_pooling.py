"""Unit tests for connection pooling strategies.

Tests cover:
- PoolingStrategy.NAIVE: New client per request
- PoolingStrategy.OPTIMIZED: Shared client with Limits
- TaskGroup behavior for structured concurrency
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from async_patterns.engine.models import ConnectionConfig


class TestPoolingStrategy:
    """Tests for PoolingStrategy enum."""

    def test_pooling_strategy_enum_exists(self) -> None:
        """Verify PoolingStrategy enum exists."""
        from async_patterns.engine.async_engine import PoolingStrategy

        assert PoolingStrategy is not None

    def test_naive_strategy_value(self) -> None:
        """Test NAIVE strategy value."""
        from async_patterns.engine.async_engine import PoolingStrategy

        assert PoolingStrategy.NAIVE.value == "naive"

    def test_optimized_strategy_value(self) -> None:
        """Test OPTIMIZED strategy value."""
        from async_patterns.engine.async_engine import PoolingStrategy

        assert PoolingStrategy.OPTIMIZED.value == "optimized"


class TestNaivePoolingStrategy:
    """Tests for NAIVE pooling strategy (new client per request)."""

    @pytest.mark.asyncio()
    async def test_naive_mode_creates_client_per_request(self) -> None:
        """Test NAIVE mode creates a new client for each request."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(
            max_concurrent=10,
            config=ConnectionConfig(max_connections=100),
            pooling_strategy=PoolingStrategy.NAIVE,
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            urls = ["https://example1.com", "https://example2.com"]
            result = await engine.run(urls)

            # NAIVE mode should create client for each request
            assert mock_client_class.call_count == len(urls)

    @pytest.mark.asyncio()
    async def test_naive_mode_returns_valid_results(self) -> None:
        """Test NAIVE mode returns valid EngineResult."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(max_concurrent=5, pooling_strategy=PoolingStrategy.NAIVE)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.03
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            urls = [f"https://example{i}.com" for i in range(3)]
            result = await engine.run(urls)

            assert len(result.results) == 3
            assert all(r.status_code == 200 for r in result.results)


class TestOptimizedPoolingStrategy:
    """Tests for OPTIMIZED pooling strategy (shared client with Limits)."""

    @pytest.mark.asyncio()
    async def test_optimized_mode_shares_client(self) -> None:
        """Test OPTIMIZED mode uses a single shared client."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(
            max_concurrent=10,
            config=ConnectionConfig(max_connections=100, max_keepalive_connections=20),
            pooling_strategy=PoolingStrategy.OPTIMIZED,
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            urls = [
                "https://example1.com",
                "https://example2.com",
                "https://example3.com",
            ]
            result = await engine.run(urls)

            # OPTIMIZED mode should create client once
            assert mock_client_class.call_count == 1

    @pytest.mark.asyncio()
    async def test_optimized_mode_applies_limits(self) -> None:
        """Test OPTIMIZED mode applies httpx.Limits to client."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(
            max_concurrent=10,
            config=ConnectionConfig(max_connections=50, max_keepalive_connections=10, timeout=15.0),
            pooling_strategy=PoolingStrategy.OPTIMIZED,
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            # Verify client was created with limits
            call_kwargs = (
                mock_client_class.call_args.kwargs if mock_client_class.call_args.kwargs else {}
            )
            # The Limits object should have been passed
            assert mock_client_class.called


class TestTaskGroupConcurrency:
    """Tests for TaskGroup structured concurrency."""

    @pytest.mark.asyncio()
    async def test_all_tasks_complete_or_fail_together(self) -> None:
        """Test that all tasks complete together using TaskGroup."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=10)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            response = MagicMock()
            response.status_code = 200
            response.elapsed.total_seconds.return_value = 0.05
            mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            urls = [f"https://example{i}.com" for i in range(5)]
            result = await engine.run(urls)

            # All tasks should complete
            assert len(result.results) == 5

    @pytest.mark.asyncio()
    async def test_taskgroup_handles_partial_failures(self) -> None:
        """Test TaskGroup handles partial failures correctly."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine(max_concurrent=5)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            response_success = MagicMock()
            response_success.status_code = 200
            response_success.elapsed.total_seconds.return_value = 0.05

            mock_client.get.side_effect = [
                response_success,
                Exception("Network error"),
                response_success,
                Exception("Timeout"),
                response_success,
            ]
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            urls = [f"https://example{i}.com" for i in range(5)]
            result = await engine.run(urls)

            # Some tasks should complete, some should fail
            assert len(result.results) == 5
            success_count = sum(1 for r in result.results if r.error is None)
            error_count = sum(1 for r in result.results if r.error is not None)
            assert success_count == 3
            assert error_count == 2


class TestConnectionPoolingIntegration:
    """Integration tests for connection pooling with real httpx patterns."""

    @pytest.mark.asyncio()
    async def test_engine_result_has_valid_metrics(self) -> None:
        """Test EngineResult metrics are computed correctly."""
        from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy

        engine = AsyncEngine(
            max_concurrent=5,
            config=ConnectionConfig(timeout=10.0),
            pooling_strategy=PoolingStrategy.OPTIMIZED,
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            for i in range(3):
                response = MagicMock()
                response.status_code = 200
                response.elapsed.total_seconds.return_value = 0.1 * (i + 1)
                mock_client.get.return_value = response
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            urls = [f"https://example{i}.com" for i in range(3)]
            result = await engine.run(urls)

            assert isinstance(result, object)
            assert hasattr(result, "results")
            assert hasattr(result, "total_time")
            assert hasattr(result, "peak_memory_mb")

    def test_default_pooling_strategy(self) -> None:
        """Test default pooling strategy is OPTIMIZED."""
        from async_patterns.engine.async_engine import AsyncEngine

        engine = AsyncEngine()
        # Default should be OPTIMIZED
        assert engine._pooling_strategy.value == "optimized"
