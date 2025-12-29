"""SemaphoreLimiter pattern for bounded concurrency control using asyncio.Semaphore."""

import asyncio
from dataclasses import dataclass, field
from typing import Self


class AcquisitionTimeoutError(Exception):
    """Raised when semaphore acquisition times out."""

    pass


@dataclass
class SemaphoreMetrics:
    """Metrics for tracking semaphore acquisitions and active tasks."""

    current_active: int
    peak_active: int
    total_acquisitions: int
    timeout_count: int = field(default=0)


class SemaphoreLimiter:
    """
    Async context manager that limits concurrent execution using asyncio.Semaphore.

    Provides bounded concurrency control with metrics tracking for monitoring
    the number of active tasks and their acquisition patterns.

    Args:
        max_concurrent: Maximum number of concurrent tasks allowed.
                       Defaults to 100.

    Example:
        ```python
        limiter = SemaphoreLimiter(max_concurrent=5)

        async def process_items(items):
            async with limiter:
                # Only 5 of these will execute concurrently
                await process_item(item)
        ```
    """

    def __init__(
        self,
        max_concurrent: int = 100,
        acquire_timeout: float = 30.0,
    ) -> None:
        """Initialize the SemaphoreLimiter.

        Args:
            max_concurrent: Maximum number of concurrent operations allowed.
            acquire_timeout: Maximum time to wait for semaphore acquisition in seconds.

        Raises:
            ValueError: If max_concurrent is less than 1.
            ValueError: If acquire_timeout is negative.
        """
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        if acquire_timeout < 0:
            raise ValueError("acquire_timeout must be non-negative")

        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._acquire_timeout = acquire_timeout
        self._max_concurrent = max_concurrent
        self._current_active = 0
        self._peak_active = 0
        self._total_acquisitions = 0
        self._timeout_count = 0
        self._lock = asyncio.Lock()

    @property
    def max_concurrent(self) -> int:
        """Maximum number of concurrent operations allowed."""
        return self._max_concurrent

    async def __aenter__(self) -> Self:
        """Acquire the semaphore and enter the context manager.

        Returns:
            Self: The SemaphoreLimiter instance.

        Raises:
            AcquisitionTimeoutError: If semaphore acquisition times out.
        """
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self._acquire_timeout,
            )
        except TimeoutError:
            async with self._lock:
                self._timeout_count += 1
            raise AcquisitionTimeoutError(
                f"Failed to acquire semaphore within {self._acquire_timeout}s"
            ) from None

        async with self._lock:
            self._current_active += 1
            self._total_acquisitions += 1
            self._peak_active = max(self._peak_active, self._current_active)

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        """Release the semaphore when exiting the context manager."""
        async with self._lock:
            self._current_active -= 1

        self._semaphore.release()

    def get_metrics(self) -> SemaphoreMetrics:
        """Get current metrics for this semaphore limiter.

        Returns:
            SemaphoreMetrics: Current metrics including active count, peak, and total.
        """
        return SemaphoreMetrics(
            current_active=self._current_active,
            peak_active=self._peak_active,
            total_acquisitions=self._total_acquisitions,
            timeout_count=self._timeout_count,
        )
