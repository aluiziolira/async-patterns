"""Internal async engine execution module."""

from __future__ import annotations

import asyncio
import logging
import time
import tracemalloc
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import Enum
from typing import Any, Protocol

import aiohttp

from async_patterns.engine.models import ConnectionConfig, EngineResult, RequestResult
from async_patterns.patterns.circuit_breaker import CircuitBreaker
from async_patterns.patterns.retry import RetryPolicy
from async_patterns.patterns.semaphore import SemaphoreLimiter


class PoolingStrategy(Enum):
    """Connection pooling strategy for async HTTP sessions."""

    NAIVE = "naive"
    OPTIMIZED = "optimized"


class AsyncHttpTransport(Protocol):
    """Internal seam for opening async HTTP sessions."""

    def open_session(self, config: ConnectionConfig) -> Any:
        """Open a configured async session context manager."""
        ...


class AiohttpTransport:
    """aiohttp adapter for the async HTTP transport seam."""

    def __init__(self, aiohttp_module: Any = aiohttp) -> None:
        self._aiohttp = aiohttp_module

    def open_session(self, config: ConnectionConfig) -> Any:
        """Open an aiohttp ClientSession with configured pooling limits."""
        connector = self._aiohttp.TCPConnector(
            limit=config.max_connections,
            limit_per_host=config.max_keepalive_connections,
            keepalive_timeout=config.keepalive_expiry,
        )
        timeout = self._aiohttp.ClientTimeout(total=config.timeout)
        return self._aiohttp.ClientSession(connector=connector, timeout=timeout)


class AsyncHttpExecutor:
    """Run async HTTP work behind a small internal module interface."""

    def __init__(
        self,
        *,
        max_concurrent: int,
        config: ConnectionConfig,
        pooling_strategy: PoolingStrategy,
        limiter: SemaphoreLimiter,
        active_tasks: set[asyncio.Task[Any]],
        transport: AsyncHttpTransport,
        circuit_breaker: CircuitBreaker | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._max_concurrent = max_concurrent
        self._config = config
        self._pooling_strategy = pooling_strategy
        self._limiter = limiter
        self._active_tasks = active_tasks
        self._transport = transport
        self._circuit_breaker = circuit_breaker
        self._retry_policy = retry_policy
        self._shutdown = False

    @property
    def active_tasks(self) -> set[asyncio.Task[Any]]:
        """Tasks currently owned by the executor."""
        return self._active_tasks

    async def close(self, timeout: float = 5.0) -> None:
        """Gracefully shut down the executor."""
        self._shutdown = True
        if self._active_tasks:
            _, pending = await asyncio.wait(
                self._active_tasks, timeout=timeout, return_when=asyncio.ALL_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        if self._circuit_breaker:
            await self._circuit_breaker.wait_pending()

    async def run(self, urls: list[str]) -> EngineResult:
        """Execute all URLs and return aggregate engine metrics."""
        if not urls:
            return EngineResult(results=[], total_time=0.0, peak_memory_mb=0.0)

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
            if not was_tracing:
                tracemalloc.stop()

    async def stream(self, urls: list[str]) -> AsyncIterator[RequestResult]:
        """Execute URLs and yield results as each worker finishes."""
        if not urls:
            return

        result_queue: asyncio.Queue[RequestResult] = asyncio.Queue(maxsize=self._max_concurrent)
        url_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=len(urls))
        for url in urls:
            url_queue.put_nowait(url)

        shared_session: Any | None = None
        if self._pooling_strategy == PoolingStrategy.OPTIMIZED:
            shared_session = self._transport.open_session(self._config)

        try:

            async def worker() -> None:
                while True:
                    try:
                        url = url_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return

                    start_time = time.perf_counter()
                    try:
                        if self._pooling_strategy == PoolingStrategy.NAIVE:
                            result = await self.fetch_url_naive(url)
                        else:
                            result = await self.fetch_url(shared_session, url)
                        await result_queue.put(result)
                    except Exception as exc:
                        logging.warning("Worker failed for %s: %s", url, exc)
                        await result_queue.put(self._unexpected_error_result(url, start_time, exc))
                    finally:
                        url_queue.task_done()

            worker_count = min(self._max_concurrent, len(urls))
            tasks = [
                asyncio.create_task(self._worker_wrapper(worker))
                for _task_id in range(worker_count)
            ]

            results_yielded = 0
            while results_yielded < len(urls):
                try:
                    result = await asyncio.wait_for(result_queue.get(), timeout=60.0)
                    yield result
                    results_yielded += 1
                except TimeoutError:
                    logging.warning("Timeout waiting for result, continuing...")
                    if all(task.done() for task in tasks):
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
            if shared_session is not None:
                await shared_session.close()

    async def fetch_url_naive(self, url: str) -> RequestResult:
        """Fetch one URL with a dedicated session."""
        async with self._transport.open_session(self._config) as session:
            return await self.fetch_url(session, url)

    async def fetch_url(self, session: Any, url: str) -> RequestResult:
        """Fetch one URL and normalize the outcome into RequestResult."""
        async with self._limiter:
            start_time = time.perf_counter()
            attempt_count = 1
            response: Any | None = None
            last_status_code = 0

            async def make_request() -> Any:
                nonlocal start_time, last_status_code
                start_time = time.perf_counter()
                retry_response = await session.get(url)
                last_status_code = retry_response.status
                if self._retry_policy is not None and (
                    retry_response.status >= 500 or retry_response.status == 429
                ):
                    retry_after = None
                    if retry_response.status == 429:
                        retry_after = retry_response.headers.get("Retry-After")
                    try:
                        await retry_response.read()
                    except Exception:
                        retry_response.release()
                    message = f"Server error: {retry_response.status}"
                    if retry_response.status == 429:
                        message = f"Rate limited: {retry_response.status}"
                        if retry_after:
                            message = f"{message} Retry-After={retry_after}"
                    raise aiohttp.ClientResponseError(
                        request_info=retry_response.request_info,
                        history=retry_response.history,
                        status=retry_response.status,
                        message=message,
                        headers=retry_response.headers,
                    )
                return retry_response

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
                await self._record_latency(latency_ms)

                return RequestResult(
                    url=url,
                    status_code=response.status,
                    latency_ms=latency_ms,
                    timestamp=time.time(),
                    attempt=attempt_count,
                    error=None,
                )
            except Exception as exc:
                latency_ms = (time.perf_counter() - start_time) * 1000
                await self._record_latency(latency_ms)

                final_attempt = attempt_count
                if self._retry_policy is not None:
                    final_attempt = getattr(exc, "attempt_count", attempt_count)

                return RequestResult(
                    url=url,
                    status_code=last_status_code,
                    latency_ms=latency_ms,
                    timestamp=time.time(),
                    attempt=final_attempt,
                    error=str(exc),
                )
            finally:
                if response is not None:
                    try:
                        await response.read()
                    except Exception:
                        response.release()

    async def _run_naive(self, urls: list[str]) -> list[RequestResult]:
        async def fetch_with_session(url: str) -> RequestResult:
            return await self.fetch_url_naive(url)

        return await self._run_with_workers(urls, fetch_with_session)

    async def _run_optimized(self, urls: list[str]) -> list[RequestResult]:
        async with self._transport.open_session(self._config) as session:

            async def fetch_with_shared_session(url: str) -> RequestResult:
                return await self.fetch_url(session, url)

            return await self._run_with_workers(urls, fetch_with_shared_session)

    async def _run_with_workers(
        self, urls: list[str], fetch: Callable[[str], Awaitable[RequestResult]]
    ) -> list[RequestResult]:
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

                start_time = time.perf_counter()
                try:
                    results.append(await fetch(url))
                except Exception as exc:
                    logging.warning("Worker failed for %s: %s", url, exc)
                    results.append(self._unexpected_error_result(url, start_time, exc))
                finally:
                    url_queue.task_done()

        worker_count = min(self._max_concurrent, len(urls))
        tasks = [
            asyncio.create_task(self._worker_wrapper(worker))
            for _task_id in range(worker_count)
        ]

        await url_queue.join()
        await asyncio.gather(*tasks)
        return results

    async def _worker_wrapper(self, worker: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.current_task()
        if task:
            self._active_tasks.add(task)
        try:
            await worker()
        finally:
            if task:
                self._active_tasks.discard(task)

    async def _record_latency(self, latency_ms: float) -> None:
        if self._circuit_breaker is not None:
            await self._circuit_breaker.record_latency(latency_ms)

    @staticmethod
    def _unexpected_error_result(
        url: str,
        start_time: float,
        exc: BaseException,
    ) -> RequestResult:
        latency_ms = (time.perf_counter() - start_time) * 1000
        return RequestResult(
            url=url,
            status_code=0,
            latency_ms=latency_ms,
            timestamp=time.time(),
            attempt=1,
            error=str(exc),
        )
