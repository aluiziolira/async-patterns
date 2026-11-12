"""Circuit breaker transition tests using a fake monotonic clock."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from async_patterns.engine.models import RetryConfig
from async_patterns.patterns.circuit_breaker import CircuitBreaker, CircuitBreakerError
from async_patterns.patterns.retry import RetryPolicy


class FakeClock:
    """Controllable monotonic clock for deterministic timing tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


async def _open_breaker(breaker: CircuitBreaker, failures: int = 1) -> None:
    for _ in range(failures):
        with pytest.raises(ValueError, match="boom"):
            async with breaker:
                raise ValueError("boom")
    assert breaker.is_open


class TestHalfOpenAdmission:
    """HALF_OPEN admits only the configured number of trial requests."""

    @pytest.mark.asyncio
    async def test_half_open_admits_only_configured_trials(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(
            name="admit-cap",
            failure_threshold=1,
            open_state_duration=10.0,
            half_open_max_calls=2,
            probe_count=2,
            clock=clock,
        )
        await _open_breaker(breaker)
        clock.advance(10.0)

        hold = asyncio.Event()
        entered = 0

        async def trial() -> None:
            nonlocal entered
            async with breaker:
                entered += 1
                await hold.wait()

        tasks = [asyncio.create_task(trial()) for _ in range(2)]
        for _ in range(100):
            if entered == 2:
                break
            await asyncio.sleep(0)

        assert entered == 2
        assert breaker.is_half_open

        with pytest.raises(CircuitBreakerError, match="already admitted"):
            async with breaker:
                pass

        hold.set()
        await asyncio.gather(*tasks)
        assert breaker.is_closed


class TestHalfOpenRecoveryAndFailure:
    """HALF_OPEN recovers to CLOSED on success and re-opens on trial failure."""

    @pytest.mark.asyncio
    async def test_half_open_recovers_to_closed_on_successes(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(
            name="recover",
            failure_threshold=1,
            open_state_duration=5.0,
            half_open_max_calls=2,
            probe_count=1,
            clock=clock,
        )
        await _open_breaker(breaker)
        clock.advance(5.0)

        async with breaker:
            pass
        assert breaker.is_half_open

        async with breaker:
            pass
        assert breaker.is_closed

    @pytest.mark.asyncio
    async def test_half_open_reopens_on_trial_failure(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(
            name="reopen",
            failure_threshold=1,
            open_state_duration=5.0,
            half_open_max_calls=3,
            probe_count=1,
            clock=clock,
        )
        await _open_breaker(breaker)
        clock.advance(5.0)

        with pytest.raises(ValueError, match="trial failed"):
            async with breaker:
                raise ValueError("trial failed")

        assert breaker.is_open

    @pytest.mark.asyncio
    async def test_open_to_half_open_uses_fake_clock_without_sleep(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(
            name="clock",
            failure_threshold=1,
            open_state_duration=30.0,
            half_open_max_calls=1,
            clock=clock,
        )
        await _open_breaker(breaker)

        with pytest.raises(CircuitBreakerError):
            async with breaker:
                pass

        clock.advance(30.0)
        async with breaker:
            pass
        assert breaker.is_closed


class TestOpenRejectionDoesNotConsumeRetries:
    """OPEN rejections fail fast and do not burn retry attempts."""

    @pytest.mark.asyncio
    async def test_open_rejection_does_not_consume_retries(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(
            name="reject",
            failure_threshold=1,
            open_state_duration=60.0,
            clock=clock,
        )
        await _open_breaker(breaker)

        policy = RetryPolicy(config=RetryConfig(max_attempts=5, base_delay=0.01, jitter_factor=0.0))
        calls = 0

        async def should_not_run() -> str:
            nonlocal calls
            calls += 1
            return "ok"

        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            with pytest.raises(CircuitBreakerError):
                await policy.execute(should_not_run, circuit_breaker=breaker)

        assert calls == 0
        assert sleep_mock.await_count == 0
        assert breaker.get_metrics().total_rejected == 1

    @pytest.mark.asyncio
    async def test_failure_that_opens_breaker_fails_fast_without_backoff_sleep(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(
            name="trip-fast",
            failure_threshold=1,
            open_state_duration=60.0,
            clock=clock,
        )
        policy = RetryPolicy(config=RetryConfig(max_attempts=4, base_delay=5.0, jitter_factor=0.0))
        calls = 0

        async def flaky() -> str:
            nonlocal calls
            calls += 1
            raise ConnectionError("transient boom")

        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            with pytest.raises(CircuitBreakerError):
                await policy.execute(flaky, circuit_breaker=breaker)

        assert calls == 1
        assert sleep_mock.await_count == 0
        assert breaker.is_open

    @pytest.mark.asyncio
    async def test_retry_policy_transitions_open_breaker_to_half_open_and_closes(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(
            name="retry-recover",
            failure_threshold=1,
            open_state_duration=10.0,
            half_open_max_calls=1,
            clock=clock,
        )
        await _open_breaker(breaker)

        policy = RetryPolicy(config=RetryConfig(max_attempts=3, base_delay=0.01, jitter_factor=0.0))

        clock.advance(10.0)

        async def healthy() -> str:
            return "recovered"

        assert await policy.execute(healthy, circuit_breaker=breaker) == "recovered"
        assert breaker.is_closed

    @pytest.mark.asyncio
    async def test_recovery_clears_window_so_single_failure_does_not_reopen(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker(
            name="window-reset",
            failure_threshold=2,
            error_rate_threshold=1.0,
            open_state_duration=5.0,
            window_duration=60.0,
            half_open_max_calls=1,
            clock=clock,
        )
        await _open_breaker(breaker, failures=2)
        clock.advance(5.0)

        async with breaker:
            pass
        assert breaker.is_closed

        with pytest.raises(ValueError, match="post-recovery"):
            async with breaker:
                raise ValueError("post-recovery")

        assert breaker.is_closed
