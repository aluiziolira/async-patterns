"""Async HTTP Engine using aiohttp for high-performance concurrent requests.

This module implements the AsyncEngine protocol using aiohttp.ClientSession
for async HTTP requests with bounded concurrency control via SemaphoreLimiter.

Features:
- PoolingStrategy: NAIVE (new session per request) or OPTIMIZED (shared session)
- Memory profiling with peak memory tracking
- Streaming mode for memory-efficient result processing
"""

from __future__ import annotations

import asyncio
import logging
import time
import tracemalloc
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import Enum
from typing import TYPE_CHECKING, Any

import aiohttp

from async_patterns.engine.models import ConnectionConfig, EngineResult, RequestResult
from async_patterns.patterns.circuit_breaker import CircuitBreaker
from async_patterns.patterns.retry import RetryPolicy
from async_patterns.patterns.semaphore import SemaphoreLimiter

if TYPE_CHECKING:
    from async_patterns.patterns.retry import RetryPolicy


class PoolingStrategy(Enum):
    """Connection pooling strategy for async HTTP sessions.

    Attributes:
        NAIVE: Creates a new aiohttp.ClientSession for each request.
               Higher memory usage but cleaner isolation.
        OPTIMIZED: Shares a single aiohttp.ClientSession across all requests
                   with connection pooling limits. Lower memory usage.
    """

    NAIVE = "naive"
    OPTIMIZED = "optimized"


class AsyncEngine:
    """Async HTTP engine with bounded concurrency using aiohttp.ClientSession.

    Implements the AsyncEngine protocol for concurrent HTTP requests with:
    - asyncio for structured concurrency
    - Bounded concurrency control via SemaphoreLimiter
    - Configurable connection pooling strategy
    - High throughput using aiohttp's efficient async handling

    Args:
        max_concurrent: Maximum number of concurrent requests (default: 100).
        config: Connection configuration for pooling and timeouts.
        pooling_strategy: Strategy for connection pooling (default: OPTIMIZED).

    Example:
        ```python
        import asyncio
        from async_patterns.engine.async_engine import AsyncEngine

        async def main():
            engine = AsyncEngine(
                max_concurrent=50,
                config=ConnectionConfig(timeout=30.0),
            )
            result = await engine.run([
                "https://api.example.com/users",
                "https://api.example.com/posts",
            ])
            print(f"RPS: {result.rps}")

        asyncio.run(main())
        ```
    """

    def __init__(
        self,
        max_concurrent: int = 100,
        config: ConnectionConfig | None = None,
        pooling_strategy: PoolingStrategy = PoolingStrategy.OPTIMIZED,
        circuit_breaker: CircuitBreaker | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        """Initialize the AsyncEngine.

        Args:
            max_concurrent: Maximum concurrent requests allowed.
            config: Connection configuration for HTTP client.
            pooling_strategy: Strategy for connection pooling.
            circuit_breaker: Optional CircuitBreaker for latency-based circuit opening.
            retry_policy: Optional RetryPolicy for automatic retry handling.

        Raises:
            ValueError: If max_concurrent is less than 1.
        """
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")

        self._max_concurrent = max_concurrent
        self._config = config or ConnectionConfig()
        self._pooling_strategy = pooling_strategy
        self._circuit_breaker = circuit_breaker
        self._retry_policy = retry_policy
        self._limiter = SemaphoreLimiter(max_concurrent=max_concurrent)
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._shutdown = False

    @property
    def name(self) -> str:
        """Name of the async engine implementation."""
        return "async_engine"

    async def __aenter__(self) -> AsyncEngine:
        """Enter async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        """Exit async context manager, gracefully shutting down."""
        await self.close()

    async def close(self, timeout: float = 5.0) -> None:
        """Gracefully shutdown engine, cancelling in-flight requests.

        Args:
            timeout: Seconds to wait for tasks before force-cancelling.
        """
        self._shutdown = True
        if self._active_tasks:
            # Give tasks a chance to complete
            done, pending = await asyncio.wait(
                self._active_tasks, timeout=timeout, return_when=asyncio.ALL_COMPLETED
            )
            # Force cancel stragglers
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        # Cleanup circuit breaker pending tasks
        if self._circuit_breaker:
            await self._circuit_breaker.wait_pending()

    async def run(self, urls: list[str]) -> EngineResult:
        """Execute HTTP requests for the given URLs.

        Uses asyncio.gather for concurrent execution and SemaphoreLimiter
        for bounded concurrency control.

        Args:
            urls: List of URLs to request.

        Returns:
            EngineResult containing all request results and aggregate metrics.
        """
        if not urls:
            return EngineResult(results=[], total_time=0.0, peak_memory_mb=0.0)

        # Only start/stop tracemalloc if it's not already running
        was_tracing = tracemalloc.is_tracing()
        if not was_tracing:
            tracemalloc.start()

        start_time = time.perf_counter()

        try:
            if self._pooling_strategy == PoolingStrategy.NAIVE:
                results = await self._run_naive(urls)
            else:
                results = await self._run_optimized(urls)

            total_time = time.perf_counter() - start_time

            # Only get memory if we started tracing, otherwise set to 0
            if not was_tracing:
                _, peak = tracemalloc.get_traced_memory()
                peak_memory_mb = peak / (1024 * 1024)
            else:
                peak_memory_mb = 0.0

            return EngineResult(
                results=results,
                total_time=total_time,
                peak_memory_mb=peak_memory_mb,
            )
        finally:
            # Only stop if we started it
            if not was_tracing:
                tracemalloc.stop()

    async def run_streaming(self, urls: list[str]) -> AsyncIterator[RequestResult]:
        """Execute HTTP requests for the given URLs, yielding results as they complete.

        This method is memory-efficient as it yields results as they are completed
        rather than accumulating them in a list. It uses an asyncio queue to
        coordinate between worker tasks and the consumer.

        Args:
            urls: List of URLs to request.

        Yields:
            RequestResult objects as each request completes.
        """
        if not urls:
            return

        # Queues for coordinating workers and results
        result_queue: asyncio.Queue[RequestResult] = asyncio.Queue(maxsize=self._max_concurrent)
        url_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=len(urls))
        for url in urls:
            url_queue.put_nowait(url)

        # Create shared session for OPTIMIZED strategy
        if self._pooling_strategy == PoolingStrategy.OPTIMIZED:
            connector = aiohttp.TCPConnector(
                limit=self._config.max_connections,
                limit_per_host=self._config.max_keepalive_connections,
                keepalive_timeout=self._config.keepalive_expiry,
            )
            timeout = aiohttp.ClientTimeout(total=self._config.timeout)
            shared_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        else:
            shared_session = None

        try:

            async def worker() -> None:
                while True:
                    try:
                        url = url_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        start_time = time.perf_counter()
                        if self._pooling_strategy == PoolingStrategy.NAIVE:
                            result = await self._fetch_url_naive(url)
                        else:
                            # Use the shared session for OPTIMIZED strategy
                            result = await self._fetch_url(shared_session, url)  # type: ignore
                        await result_queue.put(result)
                    except Exception as exc:
                        logging.warning(f"Worker failed for {url}: {exc}")
                        latency_ms = (time.perf_counter() - start_time) * 1000
                        error_result = RequestResult(
                            url=url,
                            status_code=0,
                            latency_ms=latency_ms,
                            timestamp=time.time(),
                            attempt=1,
                            error=str(exc),
                        )
                        await result_queue.put(error_result)
                    finally:
                        url_queue.task_done()

            worker_count = min(self._max_concurrent, len(urls))
            tasks = [
                asyncio.create_task(self._worker_wrapper(worker, task_id))
                for task_id in range(worker_count)
            ]

            results_yielded = 0
            while results_yielded < len(urls):
                try:
                    result = await asyncio.wait_for(result_queue.get(), timeout=60.0)
                    yield result
                    results_yielded += 1
                except TimeoutError:
                    logging.warning("Timeout waiting for result, continuing...")
                    all_done = all(task.done() for task in tasks)
                    if all_done:
                        while not result_queue.empty():
                            try:
                                result = result_queue.get_nowait()
                                yield result
                                results_yielded += 1
                            except asyncio.QueueEmpty:
                                break
                        return

            for task in tasks:
                if not task.done():
                    task.cancel()

            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            # Close the shared session if it was created
            if shared_session is not None:
                await shared_session.close()

    async def _fetch_url_naive(self, url: str) -> RequestResult:
        """Fetch URL with a dedicated session."""
        connector = aiohttp.TCPConnector(
            limit=self._config.max_connections,
            limit_per_host=self._config.max_keepalive_connections,
            keepalive_timeout=self._config.keepalive_expiry,
        )
        timeout = aiohttp.ClientTimeout(total=self._config.timeout)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            return await self._fetch_url(session, url)

    async def _run_naive(self, urls: list[str]) -> list[RequestResult]:
        """Run requests using NAIVE strategy (new session per request).

        Args:
            urls: List of URLs to request.

        Returns:
            List of RequestResult objects.
        """

        async def fetch_with_session(url: str) -> RequestResult:
            """Fetch URL with a dedicated session."""
            return await self._fetch_url_naive(url)

        return await self._run_with_workers(urls, fetch_with_session)

    async def _run_optimized(self, urls: list[str]) -> list[RequestResult]:
        """Run requests using OPTIMIZED strategy (shared session with pooling).

        Args:
            urls: List of URLs to request.

        Returns:
            List of RequestResult objects.
        """
        connector = aiohttp.TCPConnector(
            limit=self._config.max_connections,
            limit_per_host=self._config.max_keepalive_connections,
            keepalive_timeout=self._config.keepalive_expiry,
        )
        timeout = aiohttp.ClientTimeout(total=self._config.timeout)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:

            async def fetch_with_shared_session(url: str) -> RequestResult:
                return await self._fetch_url(session, url)

            return await self._run_with_workers(urls, fetch_with_shared_session)

    async def _run_with_workers(
        self, urls: list[str], fetch: Callable[[str], Awaitable[RequestResult]]
    ) -> list[RequestResult]:
        """Process URLs using a queue-based worker pool."""
        if not urls:
            return []

        results: list[RequestResult] = []
        url_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=len(urls))
        for url in urls:
            url_queue.put_nowait(url)

        async def worker() -> None:
            while True:
                try:
                    url = url_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    start_time = time.perf_counter()
                    result = await fetch(url)
                    results.append(result)
                except Exception as exc:
                    logging.warning(f"Worker failed for {url}: {exc}")
                    latency_ms = (time.perf_counter() - start_time) * 1000
                    results.append(
                        RequestResult(
                            url=url,
                            status_code=0,
                            latency_ms=latency_ms,
                            timestamp=time.time(),
                            attempt=1,
                            error=str(exc),
                        )
                    )
                finally:
                    url_queue.task_done()

        worker_count = min(self._max_concurrent, len(urls))
        tasks = [
            asyncio.create_task(self._worker_wrapper(worker, task_id))
            for task_id in range(worker_count)
        ]

        await url_queue.join()
        await asyncio.gather(*tasks)
        return results

    async def _worker_wrapper(self, worker: Callable[[], Awaitable[None]], task_id: int) -> None:
        """Wrapper to track active worker tasks."""
        task = asyncio.current_task()
        if task:
            self._active_tasks.add(task)
        try:
            await worker()
        finally:
            if task:
                self._active_tasks.discard(task)

    async def _fetch_url(self, session: aiohttp.ClientSession, url: str) -> RequestResult:
        """Fetch a single URL using the provided session.

        Uses per-request semaphore acquisition for bounded concurrency.
        Latency is measured using time.perf_counter() for consistency.

        Args:
            session: aiohttp.ClientSession to use for the request.
            url: URL to fetch.

        Returns:
            RequestResult with the response details or error information.
        """
        async with self._limiter:
            start_time = time.perf_counter()
            attempt_count = 1
            response: aiohttp.ClientResponse | None = None
            last_status_code = 0  # 0 means no HTTP response (connection failure).

            async def make_request() -> aiohttp.ClientResponse:
                nonlocal start_time, last_status_code
                start_time = time.perf_counter()
                response = await session.get(url)
                last_status_code = response.status
                if self._retry_policy is not None and (
                    response.status >= 500 or response.status == 429
                ):
                    retry_after = None
                    if response.status == 429:
                        retry_after = response.headers.get("Retry-After")
                    try:
                        await response.read()
                    except Exception:
                        response.release()
                    message = f"Server error: {response.status}"
                    if response.status == 429:
                        message = f"Rate limited: {response.status}"
                        if retry_after:
                            message = f"{message} Retry-After={retry_after}"
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=message,
                        headers=response.headers,
                    )
                return response

            try:
                if self._retry_policy is not None:
                    retry_result = await self._retry_policy.execute_with_metrics(
                        make_request,
                        circuit_breaker=self._circuit_breaker,
                    )
                    response = retry_result.value
                    attempt_count = retry_result.attempt_count
                else:
                    response = await session.get(url)
                last_status_code = response.status

                latency_ms = (time.perf_counter() - start_time) * 1000

                if self._circuit_breaker is not None:
                    await self._circuit_breaker.record_latency(latency_ms)

                completion_timestamp = time.time()
                return RequestResult(
                    url=url,
                    status_code=response.status,
                    latency_ms=latency_ms,
                    timestamp=completion_timestamp,
                    attempt=attempt_count,
                    error=None,
                )
            except Exception as e:
                elapsed = time.perf_counter() - start_time
                latency_ms = elapsed * 1000

                if self._circuit_breaker is not None:
                    await self._circuit_breaker.record_latency(latency_ms)

                final_attempt = attempt_count
                if self._retry_policy is not None:
                    final_attempt = getattr(e, "attempt_count", attempt_count)

                completion_timestamp = time.time()
                return RequestResult(
                    url=url,
                    status_code=last_status_code,
                    latency_ms=latency_ms,
                    timestamp=completion_timestamp,
                    attempt=final_attempt,
                    error=str(e),
                )
            finally:
                if response is not None:
                    try:
                        await response.read()
                    except Exception:
                        response.release()

    def get_circuit_metrics(self) -> CircuitBreaker | None:
        """Get circuit breaker instance if configured.

        Returns:
            CircuitBreaker if configured, None otherwise.
        """
        return self._circuit_breaker

    def get_retry_metrics(self) -> RetryPolicy | None:
        """Get retry policy instance if configured.

        Returns:
            RetryPolicy if configured, None otherwise.
        """
        return self._retry_policy
