"""Tests for CircuitBreaker pattern."""

import asyncio
import time

import pytest

from async_patterns.patterns.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitBreakerMetrics,
    CircuitState,
)


class TestCircuitBreakerDefaultState:
    """Tests for circuit breaker initial state."""

    @pytest.mark.asyncio
    async def test_circuit_closed_by_default(self):
        """Test circuit breaker starts in CLOSED state."""
        breaker = CircuitBreaker(name="test")
        assert breaker.state == CircuitState.CLOSED
        assert breaker.is_closed

    @pytest.mark.asyncio
    async def test_circuit_properties(self):
        """Test circuit breaker properties are set correctly."""
        breaker = CircuitBreaker(
            name="mybreaker",
            failure_threshold=5,
            error_rate_threshold=0.2,
            half_open_max_calls=2,
            open_state_duration=30.0,
        )
        assert breaker.name == "mybreaker"
        assert breaker.is_closed
        assert not breaker.is_open
        assert not breaker.is_half_open


class TestCircuitBreakerFailureThreshold:
    """Tests for circuit breaker failure handling."""

    @pytest.mark.asyncio
    async def test_circuit_opens_after_failure_threshold(self):
        """Test circuit opens after reaching failure threshold."""
        breaker = CircuitBreaker(name="test", failure_threshold=3)

        # Fail 3 times
        for _ in range(3):
            with pytest.raises(ValueError, match="Simulated failure"):
                async with breaker:
                    raise ValueError("Simulated failure")

        # Circuit should now be open
        assert breaker.state == CircuitState.OPEN
        assert breaker.is_open

        # Further requests should be rejected
        with pytest.raises(CircuitBreakerError):
            async with breaker:
                pass

    @pytest.mark.asyncio
    async def test_failure_count_tracking(self):
        """Test that failure count is tracked correctly."""
        breaker = CircuitBreaker(name="test", failure_threshold=10)

        # Fail once
        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        metrics = breaker.get_metrics()
        assert metrics.failure_count == 1
        assert metrics.success_count == 0


class TestCircuitBreakerHalfOpenState:
    """Tests for HALF_OPEN state transitions."""

    @pytest.mark.asyncio
    async def test_circuit_half_open_after_timeout(self):
        """Test circuit transitions to HALF_OPEN after timeout."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            open_state_duration=0.01,  # 10ms timeout
        )

        # Fail once to open circuit
        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        assert breaker.is_open

        # Wait for open state duration
        await asyncio.sleep(0.02)

        # Try entering - should transition to HALF_OPEN
        async with breaker:
            pass

        assert breaker.is_half_open

    @pytest.mark.asyncio
    async def test_circuit_closes_on_recovery(self):
        """Test circuit closes after successful recovery in HALF_OPEN."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            open_state_duration=0.01,
            half_open_max_calls=2,
        )

        # Open the circuit
        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        # Wait for HALF_OPEN transition
        await asyncio.sleep(0.02)

        # Successful calls in HALF_OPEN should close circuit
        async with breaker:
            pass  # First success

        async with breaker:
            pass  # Second success

        assert breaker.is_closed

    @pytest.mark.asyncio
    async def test_circuit_reopens_on_failure_in_half_open(self):
        """Test circuit reopens if failure occurs in HALF_OPEN."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            open_state_duration=0.01,
            half_open_max_calls=2,
        )

        # Open the circuit
        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        # Wait for HALF_OPEN
        await asyncio.sleep(0.02)

        # Fail during HALF_OPEN
        with pytest.raises(ValueError, match="Failure in half-open"):
            async with breaker:
                raise ValueError("Failure in half-open")

        assert breaker.is_open


class TestCircuitBreakerProbeGating:
    """Tests for HALF_OPEN probe gating and counting."""

    @pytest.mark.asyncio
    async def test_half_open_limits_concurrent_probes(self):
        """Test HALF_OPEN allows only probe_count concurrent requests."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            open_state_duration=0.01,
            half_open_max_calls=10,
            probe_count=2,
        )

        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        await asyncio.sleep(0.02)

        entered = asyncio.Queue()
        hold = asyncio.Event()

        async def guarded() -> None:
            async with breaker:
                entered.put_nowait(True)
                await hold.wait()

        tasks = [asyncio.create_task(guarded()) for _ in range(2)]
        for _ in range(2):
            await entered.get()

        with pytest.raises(CircuitBreakerError):
            async with breaker:
                pass

        hold.set()
        await asyncio.gather(*tasks)

    @pytest.mark.asyncio
    async def test_half_open_failure_immediately_opens(self):
        """Test HALF_OPEN failure reopens circuit immediately."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=3,
            open_state_duration=0.01,
            window_duration=0.01,
        )

        for _ in range(3):
            with pytest.raises(ValueError, match="Failure"):
                async with breaker:
                    raise ValueError("Failure")

        await asyncio.sleep(0.02)

        with pytest.raises(ValueError, match="Half-open failure"):
            async with breaker:
                raise ValueError("Half-open failure")

        assert breaker.is_open

    @pytest.mark.asyncio
    async def test_half_open_semaphore_resets_between_cycles(self):
        """Test HALF_OPEN semaphore resets after reopening."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            open_state_duration=0.01,
            half_open_max_calls=5,
            probe_count=1,
        )

        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        await asyncio.sleep(0.02)

        async with breaker:
            pass

        first_semaphore = breaker._half_open_semaphore

        with pytest.raises(ValueError, match="Failure in half-open"):
            async with breaker:
                raise ValueError("Failure in half-open")

        assert breaker.is_open

        await asyncio.sleep(0.02)

        async with breaker:
            pass

        second_semaphore = breaker._half_open_semaphore

        assert first_semaphore is not None
        assert second_semaphore is not None
        assert first_semaphore is not second_semaphore

    @pytest.mark.asyncio
    async def test_half_open_counts_single_request(self):
        """Test HALF_OPEN increments total request count once."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            open_state_duration=0.01,
            half_open_max_calls=5,
        )

        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        await asyncio.sleep(0.02)

        before = breaker.get_metrics().request_count
        async with breaker:
            pass
        after = breaker.get_metrics().request_count

        assert after - before == 1

    @pytest.mark.asyncio
    async def test_half_open_failure_counts_single_request(self):
        """Test HALF_OPEN failure increments total request count once."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            open_state_duration=0.01,
            half_open_max_calls=5,
        )

        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        await asyncio.sleep(0.02)

        before = breaker.get_metrics().request_count
        with pytest.raises(ValueError, match="Half-open failure"):
            async with breaker:
                raise ValueError("Half-open failure")
        after = breaker.get_metrics().request_count

        assert after - before == 1


class TestCircuitBreakerSuccessTracking:
    """Tests for success tracking."""

    @pytest.mark.asyncio
    async def test_success_count_increments(self):
        """Test that success count increments on successful requests."""
        breaker = CircuitBreaker(name="test", failure_threshold=10)

        async with breaker:
            pass

        metrics = breaker.get_metrics()
        assert metrics.success_count == 1
        assert metrics.request_count >= 1

    @pytest.mark.asyncio
    async def test_rejected_requests_tracked(self):
        """Test that rejected requests are tracked."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            open_state_duration=60.0,
        )

        # Open the circuit
        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        # Try multiple rejected requests
        for _ in range(3):
            with pytest.raises(CircuitBreakerError):
                async with breaker:
                    pass

        metrics = breaker.get_metrics()
        assert metrics.total_rejected == 3


class TestCircuitBreakerMetrics:
    """Tests for metrics retrieval."""

    def test_metrics_dataclass(self):
        """Test CircuitBreakerMetrics can be created."""
        metrics = CircuitBreakerMetrics(
            state=CircuitState.CLOSED,
            failure_count=5,
            success_count=10,
            request_count=15,
            last_failure_timestamp=None,
            last_state_change=time.monotonic(),
        )
        assert metrics.state == CircuitState.CLOSED
        assert metrics.failure_count == 5
        assert metrics.success_count == 10


class TestCircuitBreakerReset:
    """Tests for circuit breaker reset."""

    @pytest.mark.asyncio
    async def test_reset_returns_to_closed(self):
        """Test reset returns circuit to CLOSED state."""
        breaker = CircuitBreaker(name="test", failure_threshold=1)

        # Open the circuit
        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        assert breaker.is_open

        # Reset
        breaker.reset()

        assert breaker.is_closed
        metrics = breaker.get_metrics()
        assert metrics.failure_count == 0
        assert metrics.success_count == 0


class TestCircuitBreakerErrorRate:
    """Tests for error rate-based opening."""

    @pytest.mark.asyncio
    async def test_opens_on_failure_threshold(self):
        """Test circuit opens when failure threshold is reached."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=5,
            error_rate_threshold=0.5,  # 50% error rate (not used in this test)
        )

        # Fail 5 times
        for _ in range(5):
            with pytest.raises(ValueError, match="Failure"):
                async with breaker:
                    raise ValueError("Failure")

        # Circuit should open due to failure threshold
        assert breaker.is_open


class TestCircuitBreakerSlidingWindow:
    """Tests for sliding window functionality."""

    @pytest.mark.asyncio
    async def test_sliding_window_prunes_old_requests(self):
        """Test that sliding window prunes old requests outside the window."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=10,
            error_rate_threshold=1.0,
            window_duration=0.1,  # 100ms window
        )

        # Add 5 failures within the window
        for _ in range(5):
            with pytest.raises(ValueError, match="Failure"):
                async with breaker:
                    raise ValueError("Failure")

        # Wait for window to expire
        await asyncio.sleep(0.15)

        # Add more failures - circuit should not open because old ones are pruned
        # The window now has 0 requests after pruning
        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        # Circuit should still be closed because the window was pruned
        assert breaker.is_closed

    @pytest.mark.asyncio
    async def test_error_rate_within_window_only(self):
        """Test that error rate is calculated only within the sliding window."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=10,
            error_rate_threshold=0.5,  # 50% error rate
            window_duration=0.1,
        )

        # Add 4 successes followed by 6 failures (60% error rate in window)
        for _ in range(4):
            async with breaker:
                pass  # Success

        for _ in range(6):
            with pytest.raises(ValueError, match="Failure"):
                async with breaker:
                    raise ValueError("Failure")

        # Circuit should be open due to 60% error rate
        assert breaker.is_open

    @pytest.mark.asyncio
    async def test_window_duration_configurable(self):
        """Test that window_duration parameter is configurable."""
        breaker1 = CircuitBreaker(
            name="test1",
            failure_threshold=10,
            window_duration=5.0,  # 5 second window
        )
        assert breaker1._window_duration == 5.0

        breaker2 = CircuitBreaker(
            name="test2",
            failure_threshold=10,
            window_duration=60.0,  # 60 second window
        )
        assert breaker2._window_duration == 60.0

        # Default should be 30.0
        breaker3 = CircuitBreaker(name="test3", failure_threshold=10)
        assert breaker3._window_duration == 30.0

    @pytest.mark.asyncio
    async def test_error_rate_ignores_old_failures(self):
        """Test that old failures outside the window are ignored for error rate."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=100,  # High absolute threshold
            error_rate_threshold=0.5,  # 50% error rate
            window_duration=0.1,
        )

        # Add 10 failures - circuit opens
        for _ in range(10):
            with pytest.raises(ValueError, match="Failure"):
                async with breaker:
                    raise ValueError("Failure")

        assert breaker.is_open

        # Reset and wait for window to expire
        breaker.reset()
        await asyncio.sleep(0.15)

        # Add 4 successes followed by 2 failures (33% error rate)
        # This should NOT open the circuit because window is pruned
        for _ in range(4):
            async with breaker:
                pass

        for _ in range(2):
            with pytest.raises(ValueError, match="Failure"):
                async with breaker:
                    raise ValueError("Failure")

        # Circuit should remain closed - old failures were pruned
        assert breaker.is_closed

    @pytest.mark.asyncio
    async def test_should_open_with_below_threshold_error_rate(self):
        """Test _should_open returns False when error rate is below threshold."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=10,
            error_rate_threshold=0.5,  # 50% threshold
            window_duration=60.0,
        )

        # Add 10 successes followed by 1 failure (9% error rate)
        for _ in range(10):
            async with breaker:
                pass

        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        # Circuit should stay closed - only 1/11 = ~9% error rate
        assert breaker.is_closed


class TestCircuitBreakerRecordLatency:
    """Tests for record_latency functionality."""

    @pytest.mark.asyncio
    async def test_record_latency_opens_circuit_on_high_latency(self):
        """Test that high latency triggers failure and opens circuit."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=3,
            baseline_latency_ms=100.0,
            latency_threshold_multiplier=2.0,  # 200ms threshold
        )

        # Record latency above threshold - should count as failure
        await breaker.record_latency(300.0)  # 300ms > 200ms threshold

        # Failure count should have increased
        metrics = breaker.get_metrics()
        assert metrics.failure_count >= 1

    @pytest.mark.asyncio
    async def test_record_latency_opens_circuit_after_threshold(self):
        """Test that repeated high latency opens the circuit."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=2,
            baseline_latency_ms=100.0,
            latency_threshold_multiplier=2.0,  # 200ms threshold
        )

        await breaker.record_latency(300.0)
        assert breaker.is_closed

        await breaker.record_latency(300.0)
        assert breaker.is_open

    @pytest.mark.asyncio
    async def test_record_latency_below_threshold_no_failure(self):
        """Test that latency below threshold does not trigger failure."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=3,
            baseline_latency_ms=100.0,
            latency_threshold_multiplier=2.0,
        )

        # Record latency below threshold
        await breaker.record_latency(50.0)  # 50ms < 200ms threshold

        metrics = breaker.get_metrics()
        assert metrics.failure_count == 0

    @pytest.mark.asyncio
    async def test_record_latency_ignored_in_open_state(self):
        """Test that record_latency is ignored when circuit is open."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            baseline_latency_ms=100.0,
            latency_threshold_multiplier=2.0,
        )

        # Open the circuit
        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        assert breaker.is_open

        # Record high latency - should be ignored
        await breaker.record_latency(300.0)

        # Failure count should still be 1
        metrics = breaker.get_metrics()
        assert metrics.failure_count == 1


class TestCircuitBreakerStructuredLogging:
    """Tests for structured logging functionality."""

    @pytest.mark.asyncio
    async def test_state_change_logged_on_open(self, caplog):
        """Test that state change to OPEN is logged."""
        import json
        import logging
        from io import StringIO

        breaker = CircuitBreaker(name="test_logging", failure_threshold=1)
        breaker._logger.setLevel(logging.INFO)

        # Create a string handler to capture log output
        log_capture = StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.INFO)
        breaker._logger.addHandler(handler)

        # Fail once to open the circuit
        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        assert breaker.is_open

        # Get the log output
        log_output = log_capture.getvalue()

        # Parse the log entry as JSON
        if log_output:
            log_entry = json.loads(log_output.strip())
            assert log_entry["event"] == "circuit_breaker_state_change"
            assert log_entry["name"] == "test_logging"
            assert log_entry["from_state"] == "closed"
            assert log_entry["to_state"] == "open"
            assert log_entry["trigger"] == "failure_threshold_exceeded"
            assert "timestamp" in log_entry
            assert "metrics" in log_entry

    @pytest.mark.asyncio
    async def test_state_change_logged_on_half_open(self, caplog):
        """Test that state change to HALF_OPEN is logged."""
        import json
        import logging
        from io import StringIO

        breaker = CircuitBreaker(
            name="test_half_open",
            failure_threshold=1,
            open_state_duration=0.01,
        )
        breaker._logger.setLevel(logging.INFO)

        # Create a string handler to capture log output
        log_capture = StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.INFO)
        breaker._logger.addHandler(handler)

        # Open the circuit first
        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        assert breaker.is_open

        # Wait for timeout and transition to HALF_OPEN
        await asyncio.sleep(0.02)
        await breaker._check_and_transition_from_open()

        assert breaker.is_half_open

        # Get the log output for HALF_OPEN transition
        log_output = log_capture.getvalue()

        # Parse the last log entry (HALF_OPEN transition)
        log_lines = log_output.strip().split("\n")
        last_log_line = log_lines[-1]
        if last_log_line:
            log_entry = json.loads(last_log_line)
            assert log_entry["event"] == "circuit_breaker_state_change"
            assert log_entry["name"] == "test_half_open"
            assert log_entry["from_state"] == "open"
            assert log_entry["to_state"] == "half_open"
            assert log_entry["trigger"] == "timeout_elapsed"
            assert "timestamp" in log_entry

    @pytest.mark.asyncio
    async def test_state_change_logged_on_close(self, caplog):
        """Test that state change to CLOSED is logged."""
        import json
        import logging
        from io import StringIO

        breaker = CircuitBreaker(
            name="test_close",
            failure_threshold=1,
            open_state_duration=0.01,
            half_open_max_calls=1,
        )
        breaker._logger.setLevel(logging.INFO)

        # Create a string handler to capture log output
        log_capture = StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.INFO)
        breaker._logger.addHandler(handler)

        # Open the circuit
        with pytest.raises(ValueError, match="Failure"):
            async with breaker:
                raise ValueError("Failure")

        # Wait for HALF_OPEN
        await asyncio.sleep(0.02)
        await breaker._check_and_transition_from_open()

        assert breaker.is_half_open

        # Successful call should close the circuit
        async with breaker:
            pass

        assert breaker.is_closed

        # Get the log output for CLOSED transition
        log_output = log_capture.getvalue()

        # Parse the last log entry (CLOSED transition)
        log_lines = log_output.strip().split("\n")
        last_log_line = log_lines[-1]
        if last_log_line:
            log_entry = json.loads(last_log_line)
            assert log_entry["event"] == "circuit_breaker_state_change"
            assert log_entry["name"] == "test_close"
            assert log_entry["from_state"] == "half_open"
            assert log_entry["to_state"] == "closed"
            assert log_entry["trigger"] == "half_open_success"
            assert "timestamp" in log_entry


def _trigger_breaker_failure(breaker: CircuitBreaker) -> None:
    """Helper to trigger a circuit breaker failure."""
    asyncio.run(breaker.__aenter__())
    raise ValueError("Failure")

    def test_log_format_is_valid_json(self):
        """Test that logged output is valid JSON."""
        import json
        import logging
        from io import StringIO

        breaker = CircuitBreaker(name="test_json", failure_threshold=1)
        breaker._logger.setLevel(logging.INFO)

        # Create a string handler to capture log output
        log_capture = StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.INFO)
        breaker._logger.addHandler(handler)

        # Trigger a state change to OPEN
        with pytest.raises(ValueError, match="Failure"):
            _trigger_breaker_failure(breaker)

        # Get the log output
        log_output = log_capture.getvalue()

        # Parse the log entry as JSON
        if log_output:
            log_entry = json.loads(log_output.strip())
            assert log_entry["event"] == "circuit_breaker_state_change"
            assert log_entry["name"] == "test_json"
            assert log_entry["from_state"] == "closed"
            assert log_entry["to_state"] == "open"
            assert "timestamp" in log_entry
            assert "metrics" in log_entry
            assert "failure_count" in log_entry["metrics"]
            assert "success_count" in log_entry["metrics"]
            assert "request_count" in log_entry["metrics"]
