"""CircuitBreaker pattern for preventing cascade failures in distributed systems."""

import asyncio
import contextvars
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Self


class CircuitState(Enum):
    """Circuit breaker state enumeration."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerError(Exception):
    """Raised when a request is rejected due to open circuit."""

    pass


@dataclass
class CircuitBreakerMetrics:
    """Metrics for tracking circuit breaker state and events.

    Timestamps are stored in monotonic seconds for safe duration calculations.
    """

    state: CircuitState
    failure_count: int
    success_count: int
    request_count: int
    last_failure_timestamp: float | None
    last_state_change: float
    total_rejected: int = field(default=0)


class CircuitBreaker:
    """Async context manager implementing the Circuit Breaker pattern.

    Provides protection against cascade failures in distributed systems by
    monitoring request failures and opening the circuit when thresholds are
    exceeded. The circuit transitions through three states:

    - CLOSED: Normal operation, requests pass through
    - OPEN: Circuit tripped, requests are rejected immediately
    - HALF_OPEN: Testing recovery, limited requests allowed

    Args:
        name: Unique identifier for this circuit breaker instance.
        failure_threshold: Number of failures before opening circuit. Default 10.
        error_rate_threshold: Error rate percentage to trigger open. Default 10%.
        latency_threshold_multiplier: P95 latency multiplier vs baseline. Default 3x.
        half_open_max_calls: Max calls allowed in HALF_OPEN state. Default 3.
        open_state_duration: Seconds to stay open before HALF_OPEN. Default 60.
        probe_count: Max concurrent probes allowed in HALF_OPEN. Default 1.
        baseline_latency_ms: Baseline P95 latency in milliseconds. Default 100.
        window_duration: Sliding window duration in seconds. Default 30.

    Example:
        ```python
        breaker = CircuitBreaker(name="api", failure_threshold=5)

        async def make_request():
            async with breaker:
                response = await http_client.get(url)
                return response
        ```
    """

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int | None = None,
        error_rate_threshold: float | None = None,
        latency_threshold_multiplier: float | None = None,
        half_open_max_calls: int | None = None,
        open_state_duration: float | None = None,
        probe_count: int = 1,
        baseline_latency_ms: float | None = None,
        window_duration: float | None = None,
    ) -> None:
        # Load from environment with defaults
        self._name = name
        self._failure_threshold = failure_threshold or int(
            os.getenv(f"CIRCUIT_BREAKER_FAILURE_THRESHOLD_{name.upper()}", "10")
        )
        self._error_rate_threshold = error_rate_threshold or float(
            os.getenv(f"CIRCUIT_BREAKER_ERROR_RATE_{name.upper()}", "0.10")
        )
        self._latency_threshold_multiplier = latency_threshold_multiplier or float(
            os.getenv(f"CIRCUIT_BREAKER_LATENCY_MULTIPLIER_{name.upper()}", "3.0")
        )
        self._half_open_max_calls = half_open_max_calls or int(
            os.getenv(f"CIRCUIT_BREAKER_HALF_OPEN_MAX_{name.upper()}", "3")
        )
        self._open_state_duration = open_state_duration or float(
            os.getenv(f"CIRCUIT_BREAKER_OPEN_DURATION_{name.upper()}", "60.0")
        )
        self._probe_count = probe_count
        self._baseline_latency_ms = baseline_latency_ms or float(
            os.getenv(f"CIRCUIT_BREAKER_BASELINE_LATENCY_{name.upper()}", "100.0")
        )
        self._window_duration = window_duration or float(
            os.getenv(f"CIRCUIT_BREAKER_WINDOW_{name.upper()}", "30.0")
        )

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._total_request_count = 0
        self._rejected_count = 0
        self._last_failure_timestamp: float | None = None
        self._last_state_change = time.monotonic()
        self._half_open_success_count = 0
        self._half_open_semaphore: asyncio.Semaphore | None = None
        self._half_open_acquired: contextvars.ContextVar[asyncio.Semaphore | None] = (
            contextvars.ContextVar(f"{name}_half_open_acquired", default=None)
        )
        self._lock = asyncio.Lock()
        self._request_window: deque[tuple[float, bool]] = deque()
        self._logger = logging.getLogger(f"circuit_breaker.{name}")

    @property
    def name(self) -> str:
        """Unique identifier for this circuit breaker."""
        return self._name

    @property
    def state(self) -> CircuitState:
        """Current state of the circuit breaker."""
        return self._state

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self._state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (rejecting requests)."""
        return self._state == CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)."""
        return self._state == CircuitState.HALF_OPEN

    def _prune_window(self) -> None:
        """Remove requests outside the sliding window."""
        now = time.monotonic()
        cutoff = now - self._window_duration
        while self._request_window and self._request_window[0][0] < cutoff:
            self._request_window.popleft()

    def _should_open(self) -> bool:
        """Determine if circuit should open based on sliding window."""
        self._prune_window()

        if not self._request_window:
            return False

        failures = sum(1 for _, is_fail in self._request_window if is_fail)
        total = len(self._request_window)

        if failures >= self._failure_threshold:
            return True

        if total >= 10:
            error_rate = failures / total
            if error_rate >= self._error_rate_threshold:
                return True

        return False

    def _should_close_from_half_open(self) -> bool:
        """Determine if circuit should close after successful half-open calls."""
        return self._half_open_success_count >= self._half_open_max_calls

    def _log_state_change(
        self,
        from_state: CircuitState,
        to_state: CircuitState,
        trigger: str,
    ) -> None:
        """Log state transition with structured JSON."""
        log_entry = {
            "event": "circuit_breaker_state_change",
            "name": self._name,
            "from_state": from_state.value,
            "to_state": to_state.value,
            "trigger": trigger,
            "timestamp": datetime.now(UTC).isoformat(),
            "metrics": {
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "request_count": self._total_request_count,
            },
        }
        self._logger.info(json.dumps(log_entry))

    async def _try_open(self) -> None:
        """Attempt to transition to OPEN state if thresholds exceeded."""
        async with self._lock:
            if self._should_open():
                old_state = self._state
                self._state = CircuitState.OPEN
                self._last_state_change = time.monotonic()
                self._log_state_change(old_state, CircuitState.OPEN, "failure_threshold_exceeded")

    async def _check_and_transition_from_open(self) -> None:
        """Check if enough time has passed to transition from OPEN to HALF_OPEN."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                time_in_open = time.monotonic() - self._last_state_change
                if time_in_open >= self._open_state_duration:
                    old_state = self._state
                    self._half_open_semaphore = asyncio.Semaphore(self._probe_count)
                    self._state = CircuitState.HALF_OPEN
                    self._last_state_change = time.monotonic()
                    self._half_open_success_count = 0
                    self._log_state_change(old_state, CircuitState.HALF_OPEN, "timeout_elapsed")

    async def _check_and_transition_from_half_open(self) -> None:
        """Check if circuit should close or reopen from HALF_OPEN state."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                if self._should_close_from_half_open():
                    old_state = self._state
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._last_state_change = time.monotonic()
                    self._log_state_change(old_state, CircuitState.CLOSED, "half_open_success")
                elif self._half_open_success_count >= self._half_open_max_calls:
                    # If all allowed calls succeed, close; otherwise stay half-open
                    pass

    async def __aenter__(self) -> Self:
        """Check circuit state before allowing request."""
        await self._check_and_transition_from_open()

        if self._state == CircuitState.OPEN:
            async with self._lock:
                self._rejected_count += 1
            raise CircuitBreakerError(
                f"Circuit breaker '{self._name}' is open. "
                f"Requests are rejected for {self._open_state_duration}s after failure threshold."
            )

        if self._state == CircuitState.HALF_OPEN:
            if self._half_open_semaphore is None:
                self._half_open_semaphore = asyncio.Semaphore(self._probe_count)
            try:
                await asyncio.wait_for(self._half_open_semaphore.acquire(), timeout=0.001)
            except TimeoutError as exc:
                async with self._lock:
                    self._rejected_count += 1
                raise CircuitBreakerError(
                    f"Circuit breaker '{self._name}' is half-open and at probe capacity."
                ) from exc
            else:
                self._half_open_acquired.set(self._half_open_semaphore)

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        """Record success or failure and manage state transitions."""
        half_open_semaphore = self._half_open_acquired.get()
        if half_open_semaphore is not None:
            half_open_semaphore.release()
            self._half_open_acquired.set(None)

        async with self._lock:
            self._total_request_count += 1

        timestamp = time.monotonic()

        if exc_type is not None:
            reopened_from_half_open = False
            async with self._lock:
                self._failure_count += 1
                self._last_failure_timestamp = timestamp
                self._request_window.append((timestamp, True))
                if self._state == CircuitState.HALF_OPEN:
                    old_state = self._state
                    self._state = CircuitState.OPEN
                    self._last_state_change = timestamp
                    self._half_open_success_count = 0
                    reopened_from_half_open = True

            if reopened_from_half_open:
                log_entry = {
                    "event": "circuit_breaker_state_change",
                    "name": self._name,
                    "from_state": old_state.value,
                    "to_state": CircuitState.OPEN.value,
                    "trigger": "half_open_failure",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "metrics": {
                        "failure_count": self._failure_count,
                        "success_count": self._success_count,
                        "request_count": self._total_request_count,
                    },
                }
                self._logger.warning(json.dumps(log_entry))
            else:
                await self._try_open()
        else:
            async with self._lock:
                self._success_count += 1
                self._request_window.append((timestamp, False))

            if self._state == CircuitState.HALF_OPEN:
                async with self._lock:
                    self._half_open_success_count += 1
                await self._check_and_transition_from_half_open()

    async def record_latency(self, latency_ms: float) -> None:
        """Record request latency; high latency counts as failure and may open circuit.

        Args:
            latency_ms: Request latency in milliseconds.
        """
        if self._state != CircuitState.CLOSED:
            return

        threshold = self._baseline_latency_ms * self._latency_threshold_multiplier
        if latency_ms > threshold:
            timestamp = time.monotonic()
            async with self._lock:
                self._failure_count += 1
                self._last_failure_timestamp = timestamp
                self._request_window.append((timestamp, True))

            await self._try_open()

    async def wait_pending(self) -> None:
        """Await any in-flight latency updates (no-op when none are pending)."""
        return None

    def get_metrics(self) -> CircuitBreakerMetrics:
        """Get current metrics for this circuit breaker.

        Returns:
            CircuitBreakerMetrics: Current metrics including state and counts.
        """
        return CircuitBreakerMetrics(
            state=self._state,
            failure_count=self._failure_count,
            success_count=self._success_count,
            request_count=self._total_request_count,
            last_failure_timestamp=self._last_failure_timestamp,
            last_state_change=self._last_state_change,
            total_rejected=self._rejected_count,
        )

    def reset(self) -> None:
        """Reset circuit breaker to initial CLOSED state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._total_request_count = 0
        self._rejected_count = 0
        self._last_failure_timestamp = None
        self._last_state_change = time.monotonic()
        self._half_open_success_count = 0
        self._half_open_semaphore = None
        self._request_window.clear()
