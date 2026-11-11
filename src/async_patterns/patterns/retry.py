"""Retry pattern with exponential backoff for transient error recovery."""

import asyncio
import random
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import TypeVar

from async_patterns.engine.models import RetryConfig
from async_patterns.patterns.circuit_breaker import CircuitBreaker

T = TypeVar("T")


def parse_retry_after(
    value: str,
    *,
    now: float | None = None,
) -> float | None:
    """Parse a Retry-After header into a non-negative delay in seconds.

    Accepts delta-seconds or an HTTP-date. Past dates resolve to 0.0.
    Malformed values return None so callers can fall back to backoff.

    Args:
        value: Raw Retry-After header value.
        now: Unix timestamp used when interpreting HTTP-dates. When omitted,
            ``time.time()`` is used.

    Returns:
        Delay in seconds, or None when the value cannot be parsed.
    """
    stripped = value.strip()
    if not stripped:
        return None

    if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
        try:
            seconds = int(stripped)
        except ValueError:
            return None
        return float(max(0, seconds))

    try:
        retry_at = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None

    if retry_at is None:
        return None

    if retry_at.tzinfo is None:
        # HTTP-date without zone is treated as GMT per RFC 9110.
        from datetime import UTC

        retry_at = retry_at.replace(tzinfo=UTC)

    reference = time.time() if now is None else now
    return max(0.0, retry_at.timestamp() - reference)


def _extract_retry_after_seconds(exc: BaseException, *, now: float | None = None) -> float | None:
    """Extract a parsed Retry-After delay from a raised HTTP error, if present."""
    headers = getattr(exc, "headers", None)
    if headers is None:
        return None

    raw: str | None
    if isinstance(headers, Mapping):
        raw_value = headers.get("Retry-After")
        if raw_value is None:
            raw_value = headers.get("retry-after")
        raw = None if raw_value is None else str(raw_value)
    else:
        getter = getattr(headers, "get", None)
        if getter is None:
            return None
        raw_value = getter("Retry-After")
        if raw_value is None:
            raw_value = getter("retry-after")
        raw = None if raw_value is None else str(raw_value)

    if raw is None:
        return None
    return parse_retry_after(raw, now=now)


@dataclass
class RetryMetrics:
    """Metrics for tracking retry operations."""

    attempt_count: int
    total_delay_ms: float
    final_result: str
    budget_consumed: float = field(default=0.0)


@dataclass(frozen=True, slots=True)
class RetryExecutionResult[T]:
    """Result of a retry execution with per-call attempt count."""

    value: T
    attempt_count: int


class RetryExhaustedError(Exception):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.attempt_count: int = 0


class RetryBudgetExceededError(Exception):
    """Raised when retry budget is consumed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.attempt_count: int = 0


def _is_transient_error(exc: BaseException) -> bool:
    """Check if an exception represents a transient error.

    Transient errors are those that may succeed on retry, such as:
    - HTTP 5xx errors (server errors)
    - Connection errors
    - Timeout errors
    - Network unreachable

    Args:
        exc: The exception to check.

    Returns:
        bool: True if the error is transient and worth retrying.
    """
    import aiohttp

    # Connection errors (Python built-in)
    if isinstance(exc, ConnectionError):
        return True

    # aiohttp connection errors
    if isinstance(exc, aiohttp.ClientConnectorError):
        return True

    if isinstance(exc, asyncio.TimeoutError):
        return True

    if isinstance(exc, aiohttp.ServerDisconnectedError):
        return True

    # HTTP 5xx errors
    if isinstance(exc, aiohttp.ClientResponseError):
        if exc.status is not None and 500 <= exc.status < 600:
            return True
        # 429 Too Many Requests is also retryable
        if exc.status == 429:
            return True

    # OS-level network errors
    error_str = str(exc).lower()
    transient_patterns = [
        "connection refused",
        "connection reset",
        "network unreachable",
        "host unreachable",
        "temporary failure",
        "no route to host",
        "econnreset",
        "etimedout",
        "socket closed",
    ]
    return any(pattern in error_str for pattern in transient_patterns)


class RetryPolicy:
    """
    Retry policy with exponential backoff and jitter.

    Provides configurable retry logic that can be composed with other
    patterns like CircuitBreaker.

    Formula:
        delay = min(base_delay × 2^attempt + jitter, max_delay)

    Args:
        config: RetryConfig instance with retry parameters.
        is_transient: Callable to determine if an error is transient.

    Example:
        ```python
        policy = RetryPolicy(max_attempts=3, base_delay=1.0)

        async def fetch_with_retry():
            return await policy.execute(fetch_data)
        ```
    """

    def __init__(
        self,
        config: RetryConfig | None = None,
        is_transient: Callable[[BaseException], bool] | None = None,
        *,
        max_attempts: int | None = None,
        base_delay: float | None = None,
        max_delay: float | None = None,
        jitter_factor: float | None = None,
        retry_budget: int | None = None,
        retry_budget_window_seconds: float | None = None,
        retry_after_max: float | None = None,
        base_delay_ms: float | None = None,
        max_delay_ms: float | None = None,
    ) -> None:
        self._config = config or RetryConfig()
        self._is_transient = is_transient or _is_transient_error

        effective_max_attempts = (
            max_attempts if max_attempts is not None else self._config.max_attempts
        )
        effective_base_delay_ms = (
            base_delay_ms
            if base_delay_ms is not None
            else (
                base_delay * 1000.0 if base_delay is not None else self._config.base_delay * 1000.0
            )
        )
        effective_max_delay_ms = (
            max_delay_ms
            if max_delay_ms is not None
            else (max_delay * 1000.0 if max_delay is not None else self._config.max_delay * 1000.0)
        )

        effective_jitter = (
            jitter_factor if jitter_factor is not None else self._config.jitter_factor
        )
        effective_retry_budget = (
            retry_budget if retry_budget is not None else self._config.retry_budget
        )
        effective_retry_budget_window = (
            retry_budget_window_seconds
            if retry_budget_window_seconds is not None
            else self._config.retry_budget_window_seconds
        )
        effective_retry_after_max = (
            retry_after_max if retry_after_max is not None else self._config.retry_after_max
        )

        self._max_attempts = int(effective_max_attempts)
        self._base_delay_ms = float(effective_base_delay_ms)
        self._max_delay_ms = float(effective_max_delay_ms)
        self._jitter_factor = float(effective_jitter)
        self._retry_budget = int(effective_retry_budget)
        self._retry_budget_window = float(effective_retry_budget_window)
        self._retry_after_max = float(effective_retry_after_max)

        self._attempt_counts: dict[str, int] = {}
        self._budget_window_start = time.monotonic()
        self._budget_lock = threading.Lock()

    @property
    def max_attempts(self) -> int:
        """Total number of attempts including the initial request."""
        return self._max_attempts

    @property
    def max_delay_ms(self) -> float:
        """Maximum delay between retries in milliseconds."""
        return self._max_delay_ms

    @property
    def retry_after_max(self) -> float:
        """Maximum Retry-After-influenced delay in seconds."""
        return self._retry_after_max

    def _calculate_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay for a given attempt number.

        Formula: delay = min(base_delay × 2^attempt + jitter, max_delay)

        Args:
            attempt: Current attempt number (0-indexed).

        Returns:
            float: Delay in seconds.
        """
        # Exponential backoff
        base_delay = self._base_delay_ms * (2**attempt)

        # Add jitter
        jitter_range = base_delay * self._jitter_factor
        jitter = random.uniform(-jitter_range, jitter_range)
        delay_ms = base_delay + jitter

        # Clamp to max delay
        clamped_delay_ms: float = min(delay_ms, self._max_delay_ms)
        return clamped_delay_ms / 1000.0  # Convert to seconds

    def _calculate_delay(self, attempt: int, exc: BaseException | None = None) -> float:
        """Calculate the sleep delay for the next retry attempt.

        When ``exc`` carries a valid Retry-After header, the delay is
        ``min(max(backoff, retry_after), retry_after_max)``. Malformed or
        missing Retry-After values fall back to normal backoff.

        Args:
            attempt: Current attempt number (0-indexed).
            exc: Exception from the failed attempt, used to read Retry-After.

        Returns:
            float: Delay in seconds.
        """
        backoff = self._calculate_backoff_delay(attempt)
        if exc is None:
            return backoff

        retry_after = _extract_retry_after_seconds(exc)
        if retry_after is None:
            return backoff

        return min(max(backoff, retry_after), self._retry_after_max)

    async def _check_budget(self, key: str = "default") -> None:
        """Check if retry budget is available for a retry attempt.

        Called only on retry attempts (attempt >= 1), not on the initial attempt.

        Args:
            key: Optional key for tracking different budgets.

        Raises:
            RetryBudgetExceededError: If budget is exhausted.
        """
        now = time.monotonic()

        with self._budget_lock:
            # Reset window if expired
            if now - self._budget_window_start >= self._retry_budget_window:
                self._attempt_counts.clear()
                self._budget_window_start = now

            # Check budget
            current_count = self._attempt_counts.get(key, 0)
            if current_count >= self._retry_budget:
                raise RetryBudgetExceededError(
                    f"Retry budget exhausted for '{key}': {current_count}/{self._retry_budget} retries in window"
                )

            self._attempt_counts[key] = current_count + 1

    async def execute(
        self,
        func: Callable[[], Awaitable[T]],
        key: str = "default",
        circuit_breaker: CircuitBreaker | None = None,
    ) -> T:
        """Execute a function with retry logic.

        Args:
            func: Callable returning an asyncio Future.
            key: Optional key for tracking retry budgets.
            circuit_breaker: Optional circuit breaker to check before each attempt.

        Returns:
            T: Result of the function call.

        Raises:
            RetryExhaustedError: If all retry attempts are exhausted.
            RetryBudgetExceededError: If retry budget is consumed.
            CircuitBreakerError: If circuit breaker is open.
        """
        result = await self.execute_with_metrics(
            func,
            key=key,
            circuit_breaker=circuit_breaker,
        )
        return result.value

    async def execute_with_metrics(
        self,
        func: Callable[[], Awaitable[T]],
        key: str = "default",
        circuit_breaker: CircuitBreaker | None = None,
    ) -> RetryExecutionResult[T]:
        """Execute a function with retry logic and return per-call attempt count.

        Args:
            func: Callable returning an asyncio Future.
            key: Optional key for tracking retry budgets.
            circuit_breaker: Optional circuit breaker to check before each attempt.

        Returns:
            RetryExecutionResult[T]: Result value and attempt count for this call.
        """
        last_exception: BaseException | None = None
        total_delay = 0.0

        if circuit_breaker is not None and circuit_breaker.is_open:
            from async_patterns.patterns.circuit_breaker import CircuitBreakerError

            raise CircuitBreakerError(f"Circuit breaker '{circuit_breaker.name}' is open")

        for attempt in range(self._max_attempts):
            attempt_count = attempt + 1
            try:
                if circuit_breaker is not None:
                    async with circuit_breaker:
                        if attempt > 0:
                            await self._check_budget(key)
                        result = await func()
                else:
                    if attempt > 0:
                        await self._check_budget(key)
                    result = await func()

                return RetryExecutionResult(value=result, attempt_count=attempt_count)

            except Exception as exc:
                last_exception = exc

                if isinstance(exc, RetryBudgetExceededError):
                    exc.attempt_count = attempt_count
                    raise

                # Open/half-open admission rejections fail fast and must not burn retries.
                from async_patterns.patterns.circuit_breaker import CircuitBreakerError

                if isinstance(exc, CircuitBreakerError):
                    raise

                # Check if error is transient
                if not self._is_transient(exc):
                    raise

                # Check if this was the last attempt
                if attempt >= self._max_attempts - 1:
                    error = RetryExhaustedError(
                        f"All {self._max_attempts} attempts exhausted. Last error: {exc}"
                    )
                    error.attempt_count = attempt_count
                    raise error from exc

                # Calculate and apply delay (honors Retry-After when present)
                delay = self._calculate_delay(attempt, last_exception)
                total_delay += delay * 1000  # Track in ms

                await asyncio.sleep(delay)

        # Should not reach here, but handle gracefully
        if last_exception is not None:
            error = RetryExhaustedError(f"All {self._max_attempts} attempts exhausted")
            error.attempt_count = self._max_attempts
            raise error from last_exception

        error = RetryExhaustedError("All retry attempts exhausted")
        error.attempt_count = self._max_attempts
        raise error

    def get_metrics(self, key: str = "default") -> RetryMetrics:
        """Get retry metrics for a given key.

        Args:
            key: The key to get metrics for.

        Returns:
            RetryMetrics: Current metrics.
        """
        budget_consumed = self._attempt_counts.get(key, 0) / self._retry_budget

        return RetryMetrics(
            attempt_count=self._attempt_counts.get(key, 0),
            total_delay_ms=0.0,
            final_result="active",
            budget_consumed=budget_consumed,
        )

    def reset_budget(self, key: str = "default") -> None:
        """Reset retry budget for a given key from sync or async contexts.

        Args:
            key: The key to reset.
        """
        with self._budget_lock:
            self._attempt_counts[key] = 0


__all__ = [
    "RetryConfig",
    "RetryExhaustedError",
    "RetryBudgetExceededError",
    "RetryMetrics",
    "RetryExecutionResult",
    "RetryPolicy",
    "parse_retry_after",
]
