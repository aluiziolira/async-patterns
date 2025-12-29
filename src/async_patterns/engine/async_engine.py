"""Async HTTP Engine using httpx.AsyncClient with bounded concurrency.

This module implements the AsyncEngine protocol using:
- httpx.AsyncClient for async HTTP requests
- asyncio.TaskGroup for structured concurrency (Python 3.11+)
- SemaphoreLimiter for bounded concurrency control
- tracemalloc for memory profiling

Features:
- PoolingStrategy: NAIVE (new client per request) or OPTIMIZED (shared client)
- Memory profiling with peak memory tracking
- Structured concurrency using TaskGroup
"""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from contextlib import suppress
from enum import Enum
from typing import TYPE_CHECKING

import httpx

from async_patterns.engine.models import ConnectionConfig, EngineResult, RequestResult
from async_patterns.patterns.semaphore import SemaphoreLimiter

if TYPE_CHECKING:
    pass


class PoolingStrategy(Enum):
    """Connection pooling strategy for async HTTP clients.

    Attributes:
        NAIVE: Creates a new httpx.AsyncClient for each request.
                Higher memory usage but cleaner isolation.
        OPTIMIZED: Shares a single httpx.AsyncClient across all requests
                   with connection pooling limits. Lower memory usage.
    """

    NAIVE = "naive"
    OPTIMIZED = "optimized"


class AsyncEngineImpl:
    """Async HTTP engine with bounded concurrency using httpx.AsyncClient.

    Implements the AsyncEngine protocol for concurrent HTTP requests with:
    - Structured concurrency via asyncio.TaskGroup (Python 3.11+)
    - Bounded concurrency control using SemaphoreLimiter
    - Memory profiling with tracemalloc
    - Configurable connection pooling strategy

    Args:
        max_concurrent: Maximum number of concurrent requests (default: 100).
        config: Connection configuration for pooling and timeouts.
        pooling_strategy: Strategy for connection pooling (default: OPTIMIZED).

    Example:
        ```python
        import asyncio
        from async_patterns.engine.async_engine import AsyncEngineImpl

        async def main():
            engine = AsyncEngineImpl(
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
    ) -> None:
        """Initialize the AsyncEngine.

        Args:
            max_concurrent: Maximum concurrent requests allowed.
            config: Connection configuration for HTTP client.
            pooling_strategy: Strategy for connection pooling.

        Raises:
            ValueError: If max_concurrent is less than 1.
        """
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")

        self._max_concurrent = max_concurrent
        self._config = config or ConnectionConfig()
        self._pooling_strategy = pooling_strategy
        self._limiter = SemaphoreLimiter(max_concurrent=max_concurrent)

    @property
    def name(self) -> str:
        """Name of the async engine implementation."""
        return "async_engine"

    async def run(self, urls: list[str]) -> EngineResult:
        """Execute HTTP requests for the given URLs.

        Uses asyncio.TaskGroup for structured concurrency and SemaphoreLimiter
        for bounded concurrency control.

        Args:
            urls: List of URLs to request.

        Returns:
            EngineResult containing all request results and aggregate metrics.
        """
        if not urls:
            return EngineResult(results=[], total_time=0.0, peak_memory_mb=0.0)

        # Start memory tracking
        tracemalloc.start()
        start_time = time.perf_counter()

        try:
            if self._pooling_strategy == PoolingStrategy.NAIVE:
                results = await self._run_naive(urls)
            else:
                results = await self._run_optimized(urls)

            total_time = time.perf_counter() - start_time

            # Capture peak memory
            current, peak = tracemalloc.get_traced_memory()
            peak_memory_mb = peak / (1024 * 1024)

            return EngineResult(
                results=results,
                total_time=total_time,
                peak_memory_mb=peak_memory_mb,
            )
        finally:
            tracemalloc.stop()

    async def _run_naive(self, urls: list[str]) -> list[RequestResult]:
        """Run requests using NAIVE strategy (new client per request).

        Args:
            urls: List of URLs to request.

        Returns:
            List of RequestResult objects.
        """
        results: list[RequestResult] = []

        async def fetch_with_client(url: str) -> RequestResult:
            """Fetch URL with a dedicated client."""
            limits = httpx.Limits(
                max_keepalive_connections=self._config.max_keepalive_connections,
                max_connections=self._config.max_connections,
                keepalive_expiry=self._config.keepalive_expiry,
            )
            timeout = httpx.Timeout(self._config.timeout)

            async with httpx.AsyncClient(
                limits=limits, timeout=timeout, http2=self._config.http2
            ) as client:
                return await self._fetch_url(client, url)

        # Use TaskGroup for structured concurrency
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(fetch_with_client(url)) for url in urls]
        except* Exception:
            # Collect any exceptions that occurred
            for task in tasks:
                if task.exception() is not None:
                    pass

        # Collect results
        for task in tasks:
            if task.done() and not task.cancelled():
                with suppress(Exception):
                    results.append(task.result())

        return results

    async def _run_optimized(self, urls: list[str]) -> list[RequestResult]:
        """Run requests using OPTIMIZED strategy (shared client with pooling).

        Args:
            urls: List of URLs to request.

        Returns:
            List of RequestResult objects.
        """
        results: list[RequestResult] = []

        # Create shared client with connection pooling
        limits = httpx.Limits(
            max_keepalive_connections=self._config.max_keepalive_connections,
            max_connections=self._config.max_connections,
            keepalive_expiry=self._config.keepalive_expiry,
        )
        timeout = httpx.Timeout(self._config.timeout)

        async with (
            httpx.AsyncClient(limits=limits, timeout=timeout, http2=self._config.http2) as client,
        ):
            # Use TaskGroup for structured concurrency
            try:
                async with asyncio.TaskGroup() as tg:
                    tasks = [tg.create_task(self._fetch_url(client, url)) for url in urls]
            except* Exception:
                # Collect any exceptions that occurred
                for task in tasks:
                    if task.exception() is not None:
                        pass

            # Collect results
            for task in tasks:
                if task.done() and not task.cancelled():
                    with suppress(Exception):
                        results.append(task.result())

        return results

    async def _fetch_url(self, client: httpx.AsyncClient, url: str) -> RequestResult:
        """Fetch a single URL using the provided client.

        Uses per-request semaphore acquisition to enforce bounded concurrency.
        Latency is measured using time.perf_counter() for consistency with
        sync/threaded engines, capturing the full request cycle including
        DNS resolution, connection, request send, and response receive.

        Args:
            client: httpx.AsyncClient to use for the request.
            url: URL to fetch.

        Returns:
            RequestResult with the response details or error information.
        """
        async with self._limiter:
            start_time = time.perf_counter()
            timestamp = start_time

            try:
                response = await client.get(url)
                # Use perf_counter for consistent latency measurement across engines
                # This captures full request cycle including DNS, connect, send, receive
                latency_ms = (time.perf_counter() - start_time) * 1000

                return RequestResult(
                    url=url,
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    timestamp=timestamp,
                    attempt=1,
                    error=None,
                )
            except Exception as e:
                elapsed = time.perf_counter() - start_time
                return RequestResult(
                    url=url,
                    status_code=0,
                    latency_ms=elapsed * 1000,
                    timestamp=timestamp,
                    attempt=1,
                    error=str(e),
                )


# Export AsyncEngine alias for backward compatibility with tests
AsyncEngine = AsyncEngineImpl
