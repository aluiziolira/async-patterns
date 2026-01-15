"""Integration tests for CircuitBreaker latency-based threshold with AsyncEngine.

Tests verify that:
- CircuitBreaker can be injected into AsyncEngine
- record_latency is called after each request
- Engine works correctly with and without circuit breaker
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from async_patterns.engine.async_engine import AsyncEngine
from async_patterns.patterns.circuit_breaker import CircuitBreaker


class TestCircuitBreakerIntegration:
    """Integration tests for CircuitBreaker integration with AsyncEngine."""

    @pytest.fixture
    def slow_breaker(self) -> CircuitBreaker:
        """Create a circuit breaker with low latency threshold for testing."""
        return CircuitBreaker(
            name="slow_service",
            failure_threshold=3,
            baseline_latency_ms=50.0,  # Low baseline
            latency_threshold_multiplier=2.0,  # 2x baseline = 100ms threshold
            open_state_duration=1.0,  # Short open duration for testing
            window_duration=10.0,
        )

    @pytest.mark.asyncio
    async def test_circuit_breaker_injected_into_engine(self, slow_breaker: CircuitBreaker) -> None:
        """Test that circuit breaker is properly injected into engine."""
        engine = AsyncEngine(circuit_breaker=slow_breaker)

        assert engine._circuit_breaker is slow_breaker

    @pytest.mark.asyncio
    async def test_record_latency_called_after_request(self, slow_breaker: CircuitBreaker) -> None:
        """Test that record_latency is called on the circuit breaker after each request."""
        engine = AsyncEngine(circuit_breaker=slow_breaker)

        # Mock record_latency to track calls
        original_record = slow_breaker.record_latency
        call_count = [0]
        latencies = []

        async def mock_record_latency(latency_ms: float) -> None:
            call_count[0] += 1
            latencies.append(latency_ms)

        slow_breaker.record_latency = mock_record_latency

        async def mock_get(url: str) -> MagicMock:
            await asyncio.sleep(0.05)
            response = MagicMock()
            response.status = 200
            response.read = AsyncMock(return_value=b"")
            return response

        with patch("aiohttp.ClientSession") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            # Make multiple requests
            result = await engine.run(["https://example1.com", "https://example2.com"])

            # Verify record_latency was called for each request
            assert call_count[0] == 2
            assert len(latencies) == 2
            # All latencies should be > 0
            assert all(latency > 0 for latency in latencies)

    @pytest.mark.asyncio
    async def test_engine_without_circuit_breaker(self, slow_breaker: CircuitBreaker) -> None:
        """Test that engine works correctly without circuit breaker."""
        # Create a breaker but don't inject it
        breaker = CircuitBreaker(name="orphan")
        original_record = breaker.record_latency
        breaker.record_latency = AsyncMock()

        engine = AsyncEngine()  # No circuit breaker

        async def mock_get(url: str) -> MagicMock:
            response = MagicMock()
            response.status = 200
            response.read = AsyncMock(return_value=b"")
            return response

        with patch("aiohttp.ClientSession") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            assert len(result.results) == 1
            assert result.results[0].status_code == 200
            # record_latency should NOT be called on the orphan breaker
            breaker.record_latency.assert_not_awaited()


class TestCircuitBreakerLatencyThreshold:
    """Tests for latency threshold behavior with mocked high latency."""

    @pytest.mark.asyncio
    async def test_high_latency_triggers_record_latency(self) -> None:
        """Test that high latency values are passed to record_latency."""
        from async_patterns.patterns.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker(
            name="high_latency_test",
            baseline_latency_ms=50.0,
            latency_threshold_multiplier=2.0,
        )

        # Mock record_latency to capture the latency value
        recorded_latencies = []
        original_record = breaker.record_latency

        async def mock_record(latency_ms: float) -> None:
            recorded_latencies.append(latency_ms)

        breaker.record_latency = mock_record

        engine = AsyncEngine(circuit_breaker=breaker)

        async def mock_slow_get(url: str) -> MagicMock:
            await asyncio.sleep(0.2)  # 200ms delay
            response = MagicMock()
            response.status = 200
            response.read = AsyncMock(return_value=b"")
            return response

        with patch("aiohttp.ClientSession") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_slow_get
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            # Verify latency was recorded and is high (> 100ms threshold)
            assert len(recorded_latencies) == 1
            assert recorded_latencies[0] >= 100  # Should exceed 100ms threshold


class TestCircuitBreakerWithPoolingStrategy:
    """Test circuit breaker integration with different pooling strategies."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_with_optimized_pooling(self) -> None:
        """Test circuit breaker works with OPTIMIZED pooling strategy (default)."""
        from async_patterns.engine.async_engine import PoolingStrategy

        breaker = CircuitBreaker(name="optimized_test", baseline_latency_ms=10.0)
        engine = AsyncEngine(
            circuit_breaker=breaker,
            pooling_strategy=PoolingStrategy.OPTIMIZED,
        )

        assert engine._pooling_strategy == PoolingStrategy.OPTIMIZED
        assert engine._circuit_breaker is breaker

        async def mock_get(url: str) -> MagicMock:
            response = MagicMock()
            response.status = 200
            response.read = AsyncMock(return_value=b"")
            return response

        with patch("aiohttp.ClientSession") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example.com"])

            assert len(result.results) == 1
            assert result.results[0].status_code == 200

    @pytest.mark.asyncio
    async def test_circuit_breaker_with_naive_pooling(self) -> None:
        """Test circuit breaker works with NAIVE pooling strategy."""
        from async_patterns.engine.async_engine import PoolingStrategy

        breaker = CircuitBreaker(name="naive_test", baseline_latency_ms=10.0)
        engine = AsyncEngine(
            circuit_breaker=breaker,
            pooling_strategy=PoolingStrategy.NAIVE,
        )

        assert engine._pooling_strategy == PoolingStrategy.NAIVE
        assert engine._circuit_breaker is breaker

        async def mock_get(url: str) -> MagicMock:
            response = MagicMock()
            response.status = 200
            response.read = AsyncMock(return_value=b"")
            return response

        with patch("aiohttp.ClientSession") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client_class.return_value.__aexit__.return_value = None

            result = await engine.run(["https://example1.com", "https://example2.com"])

            assert len(result.results) == 2
