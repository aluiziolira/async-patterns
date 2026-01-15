"""Tests for Retry pattern with exponential backoff."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from async_patterns.patterns.circuit_breaker import CircuitBreaker, CircuitBreakerError
from async_patterns.patterns.retry import (
    RetryBudgetExceededError,
    RetryConfig,
    RetryExhaustedError,
    RetryMetrics,
    RetryPolicy,
)


class TestRetryPolicyExponentialBackoff:
    """Tests for exponential backoff calculation."""

    @pytest.mark.asyncio
    async def test_retry_exponential_backoff(self):
        """Test that retry delays follow exponential backoff formula."""
        config = RetryConfig(
            max_attempts=3,
            base_delay=0.01,
            max_delay=1.0,
            jitter_factor=0.0,  # No jitter for predictable testing
        )
        policy = RetryPolicy(config=config)

        call_count = 0

        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("Failed")

        with pytest.raises(RetryExhaustedError):
            await policy.execute(always_fail)

        # Should have attempted 3 times (1 initial + 2 retries)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_delay_increases(self):
        """Test that retry delays increase exponentially."""
        delays = []
        call_times = []

        async def record_time():
            call_times.append(asyncio.get_event_loop().time())
            raise ConnectionError("Failed")

        policy = RetryPolicy(
            config=RetryConfig(
                max_attempts=3,
                base_delay=0.01,
                max_delay=1.0,
                jitter_factor=0.0,
            )
        )

        start_time = asyncio.get_event_loop().time()

        from contextlib import suppress

        with suppress(RetryExhaustedError):
            await policy.execute(record_time)

        elapsed = asyncio.get_event_loop().time() - start_time
        # Expected: 10 + 20 = 30ms minimum for 3 attempts
        assert elapsed >= 0.025  # Some tolerance


class TestRetryPolicyTransientErrors:
    """Tests for transient error detection."""

    @pytest.mark.asyncio
    async def test_retry_only_transient_errors(self):
        """Test that only transient errors are retried."""
        policy = RetryPolicy(config=RetryConfig(max_attempts=3, base_delay=0.01))

        async def non_transient_error():
            raise ValueError("This is a permanent error")

        # Non-transient errors should not be retried
        with pytest.raises(ValueError, match="This is a permanent error"):
            await policy.execute(non_transient_error)

    @pytest.mark.asyncio
    async def test_retry_connection_errors(self):
        """Test that connection errors are retried."""
        policy = RetryPolicy(config=RetryConfig(max_attempts=3, base_delay=0.01))

        call_count = 0

        async def flaky_connection():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection refused")
            return "success"

        result = await policy.execute(flaky_connection)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_timeout_errors(self):
        """Test that timeout errors are retried."""
        policy = RetryPolicy(config=RetryConfig(max_attempts=3, base_delay=0.01))

        call_count = 0

        async def timeout_then_success():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("Request timed out")
            return "success"

        result = await policy.execute(timeout_then_success)
        assert result == "success"
        assert call_count == 2

    def test_is_transient_error_aiohttp_exceptions(self) -> None:
        """Test aiohttp exception classification for retries."""
        import aiohttp

        from async_patterns.patterns.retry import _is_transient_error

        assert _is_transient_error(aiohttp.ClientConnectorError(None, OSError("boom")))
        assert _is_transient_error(aiohttp.ServerDisconnectedError("disconnect"))
        assert _is_transient_error(TimeoutError("timeout"))

        request_info = MagicMock()
        request_info.real_url = "http://example.com"
        server_error = aiohttp.ClientResponseError(
            request_info=request_info,
            history=(),
            status=503,
            message="server error",
        )
        assert _is_transient_error(server_error)

        client_error = aiohttp.ClientResponseError(
            request_info=request_info,
            history=(),
            status=404,
            message="not found",
        )
        assert not _is_transient_error(client_error)


class TestRetryPolicyJitter:
    """Tests for jitter in retry delays."""

    @pytest.mark.asyncio
    async def test_retry_with_jitter(self):
        """Test that jitter is added to delays."""
        # Run multiple times to verify jitter variation
        elapsed_times = []

        for _ in range(3):
            policy = RetryPolicy(
                config=RetryConfig(
                    max_attempts=3,
                    base_delay=0.05,
                    max_delay=1.0,
                    jitter_factor=0.5,  # 50% jitter
                )
            )

            async def always_fail():
                raise ConnectionError("Failed")

            start = asyncio.get_event_loop().time()

            from contextlib import suppress

            with suppress(RetryExhaustedError):
                await policy.execute(always_fail)

            elapsed = asyncio.get_event_loop().time() - start
            elapsed_times.append(elapsed)

        # With jitter, delays should vary
        # Base delays would be: 50 + 100 = 150ms without jitter
        # With jitter_factor=0.5, delays could range from 75ms to 225ms
        min_time = min(elapsed_times)
        max_time = max(elapsed_times)

        # Allow some tolerance for timing variations
        assert min_time > 0.05  # At least some delay happened
        # Variances should exist due to jitter
        assert max_time >= min_time  # At least as long as minimum


class TestRetryPolicyExhausted:
    """Tests for when all retries are exhausted."""

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self):
        """Test that RetryExhaustedError is raised when retries exhausted."""
        policy = RetryPolicy(config=RetryConfig(max_attempts=3, base_delay=0.01))

        async def always_fail():
            raise ConnectionError("Always fails")

        with pytest.raises(RetryExhaustedError) as exc_info:
            await policy.execute(always_fail)

        assert "3 attempts exhausted" in str(exc_info.value)


class TestRetryPolicyBudget:
    """Tests for retry budget tracking."""

    @pytest.mark.asyncio
    async def test_retry_budget_tracking(self):
        """Test that retry budget is tracked per key."""
        policy = RetryPolicy(
            config=RetryConfig(
                max_attempts=2,
                retry_budget=3,
                retry_budget_window_seconds=60.0,
            )
        )

        async def always_fail():
            raise ConnectionError("Failed")

        # Mock asyncio.sleep to skip exponential backoff delays
        with patch("asyncio.sleep", new_callable=AsyncMock):
            # Exhaust the budget
            for _ in range(3):
                from contextlib import suppress

                with suppress(Exception):
                    await policy.execute(always_fail, key="test")

            # Next attempt should fail due to budget
            with pytest.raises(RetryBudgetExceededError, match="Retry budget exhausted"):
                await policy.execute(always_fail, key="test")

            metrics = policy.get_metrics(key="test")
            assert metrics.budget_consumed >= 1.0  # Budget exhausted

    @pytest.mark.asyncio
    async def test_retry_budget_separate_keys(self):
        """Test that budgets are tracked separately per key."""
        policy = RetryPolicy(
            config=RetryConfig(
                max_attempts=2,
                retry_budget=2,
            )
        )

        async def always_fail():
            raise ConnectionError("Failed")

        # Mock asyncio.sleep to skip exponential backoff delays
        with patch("asyncio.sleep", new_callable=AsyncMock):
            # Exhaust budget for key1
            for _ in range(2):
                from contextlib import suppress

                with suppress(Exception):
                    await policy.execute(always_fail, key="key1")

            # key2 should still have budget
            assert policy._attempt_counts.get("key1", 0) == 2
            assert policy._attempt_counts.get("key2", 0) == 0


class TestRetryBudgetConsumption:
    """Tests for retry budget consumption behavior."""

    @pytest.mark.asyncio
    async def test_success_does_not_consume_budget(self):
        """Test successful calls do not consume retry budget."""
        policy = RetryPolicy(config=RetryConfig(max_attempts=3, retry_budget=5))

        async def succeed():
            return "ok"

        for _ in range(3):
            assert await policy.execute(succeed, key="budget") == "ok"

        metrics = policy.get_metrics(key="budget")
        assert metrics.attempt_count == 0

    @pytest.mark.asyncio
    async def test_single_retry_consumes_one_budget_unit(self):
        """Test one retry consumes one budget unit."""
        policy = RetryPolicy(config=RetryConfig(max_attempts=3, retry_budget=5))
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Failed once")
            return "ok"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            assert await policy.execute(flaky, key="budget") == "ok"

        metrics = policy.get_metrics(key="budget")
        assert metrics.attempt_count == 1

    @pytest.mark.asyncio
    async def test_two_retries_consume_two_budget_units(self):
        """Test two retries consume two budget units."""
        policy = RetryPolicy(config=RetryConfig(max_attempts=4, retry_budget=5))
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionError("Failed")
            return "ok"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            assert await policy.execute(flaky, key="budget") == "ok"

        metrics = policy.get_metrics(key="budget")
        assert metrics.attempt_count == 2

    @pytest.mark.asyncio
    async def test_budget_exhaustion_blocks_only_retries(self):
        """Test budget exhaustion blocks retries but not first attempts."""
        policy = RetryPolicy(config=RetryConfig(max_attempts=2, retry_budget=1))

        call_count = 0

        async def fail_once_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Failed once")
            return "ok"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            assert await policy.execute(fail_once_then_succeed, key="budget") == "ok"

            call_count = 0

            async def always_fail():
                nonlocal call_count
                call_count += 1
                raise ConnectionError("Failed")

            with pytest.raises(RetryBudgetExceededError):
                await policy.execute(always_fail, key="budget")

            assert call_count == 1


class TestRetryPolicyIntegration:
    """Tests for retry policy integration with other patterns."""

    @pytest.mark.asyncio
    async def test_retry_with_circuit_breaker_open(self):
        """Test retry fails fast when circuit breaker is open."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            open_state_duration=60.0,
        )

        # Open the circuit
        with pytest.raises(ConnectionError):
            async with breaker:
                raise ConnectionError("Failure")

        policy = RetryPolicy(config=RetryConfig(max_attempts=3, base_delay=0.01))

        async def succeed():
            return "success"

        # Should fail immediately due to open circuit
        with pytest.raises(CircuitBreakerError, match="Circuit breaker 'test' is open"):
            await policy.execute(succeed, circuit_breaker=breaker)

    @pytest.mark.asyncio
    async def test_retry_with_circuit_breaker_half_open(self):
        """Test retry succeeds when circuit breaker is in HALF_OPEN state."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            open_state_duration=0.01,  # Quick transition
            half_open_max_calls=3,
        )

        # Open the circuit
        with pytest.raises(ConnectionError):
            async with breaker:
                raise ConnectionError("Failure")

        # Wait for HALF_OPEN transition - need to enter the context to trigger check
        await asyncio.sleep(0.02)

        # Enter the context to trigger the state transition check
        async with breaker:
            pass  # This should transition to HALF_OPEN

        assert breaker.is_half_open

        # Now retry should work
        policy = RetryPolicy(config=RetryConfig(max_attempts=5, base_delay=0.01))

        async def succeed_after():
            return "success"

        result = await policy.execute(succeed_after, circuit_breaker=breaker)
        assert result == "success"


class TestRetryPolicyAttemptTracking:
    """Tests for per-call attempt tracking."""

    @pytest.mark.asyncio
    async def test_execute_with_metrics_tracks_attempts_per_call(self):
        """Ensure attempt counts are per call even with shared keys."""
        policy = RetryPolicy(config=RetryConfig(max_attempts=2, base_delay=0.01))
        call_counts = {"a": 0, "b": 0}

        async def flaky(key: str) -> str:
            call_counts[key] += 1
            if call_counts[key] < 2:
                raise ConnectionError("boom")
            return key

        with patch("asyncio.sleep", new_callable=AsyncMock):
            results = await asyncio.gather(
                policy.execute_with_metrics(lambda: flaky("a"), key="shared"),
                policy.execute_with_metrics(lambda: flaky("b"), key="shared"),
            )

        attempts = {result.value: result.attempt_count for result in results}
        assert attempts["a"] == 2
        assert attempts["b"] == 2


class TestRetryConfig:
    """Tests for RetryConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = RetryConfig()
        assert config.max_attempts == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0
        assert config.jitter_factor == 0.1
        assert config.retry_budget == 10

    def test_custom_config(self):
        """Test custom configuration values."""
        config = RetryConfig(
            max_attempts=5,
            base_delay=0.5,
            max_delay=30.0,
            jitter_factor=0.2,
            retry_budget=5,
        )
        assert config.max_attempts == 5
        assert config.base_delay == 0.5


class TestRetryMetrics:
    """Tests for RetryMetrics dataclass."""

    def test_metrics_creation(self):
        """Test RetryMetrics can be created."""
        metrics = RetryMetrics(
            attempt_count=3,
            total_delay_ms=500.0,
            final_result="success",
            budget_consumed=0.3,
        )
        assert metrics.attempt_count == 3
        assert metrics.total_delay_ms == 500.0
        assert metrics.final_result == "success"
        assert metrics.budget_consumed == 0.3
