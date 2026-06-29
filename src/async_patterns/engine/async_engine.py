"""Async HTTP Engine using aiohttp for high-performance concurrent requests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from async_patterns.engine._async_execution import (
    AiohttpTransport,
    AsyncHttpExecutor,
    PoolingStrategy,
)
from async_patterns.engine.models import ConnectionConfig, EngineResult, RequestResult
from async_patterns.patterns.circuit_breaker import CircuitBreaker
from async_patterns.patterns.retry import RetryPolicy
from async_patterns.patterns.semaphore import SemaphoreLimiter

__all__ = ["AsyncEngine", "PoolingStrategy"]


class AsyncEngine:
    """Async HTTP engine with bounded concurrency using aiohttp.ClientSession."""

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
        self._executor = AsyncHttpExecutor(
            max_concurrent=max_concurrent,
            config=self._config,
            pooling_strategy=pooling_strategy,
            limiter=self._limiter,
            active_tasks=self._active_tasks,
            transport=AiohttpTransport(aiohttp),
            circuit_breaker=circuit_breaker,
            retry_policy=retry_policy,
        )

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
        """Gracefully shutdown engine, cancelling in-flight requests."""
        self._shutdown = True
        await self._executor.close(timeout=timeout)

    async def run(self, urls: list[str]) -> EngineResult:
        """Execute HTTP requests for the given URLs."""
        return await self._executor.run(urls)

    async def run_streaming(self, urls: list[str]) -> AsyncIterator[RequestResult]:
        """Execute HTTP requests for the given URLs, yielding results as they complete."""
        async for result in self._executor.stream(urls):
            yield result

    async def _fetch_url_naive(self, url: str) -> RequestResult:
        """Compatibility wrapper for tests that exercise private behavior."""
        return await self._executor.fetch_url_naive(url)

    async def _fetch_url(self, session: Any, url: str) -> RequestResult:
        """Compatibility wrapper for tests that exercise private behavior."""
        return await self._executor.fetch_url(session, url)

    def get_circuit_metrics(self) -> CircuitBreaker | None:
        """Get circuit breaker instance if configured."""
        return self._circuit_breaker

    def get_retry_metrics(self) -> RetryPolicy | None:
        """Get retry policy instance if configured."""
        return self._retry_policy
