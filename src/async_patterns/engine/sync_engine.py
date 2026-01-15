"""Synchronous HTTP Engine using the `requests` library.

This module provides a baseline implementation for performance comparison
with threaded and async engines.
"""

from __future__ import annotations

import time
import tracemalloc

import requests

from async_patterns.engine.base import SyncEngine as SyncEngineProtocol
from async_patterns.engine.models import EngineResult, RequestResult


class SyncEngine(SyncEngineProtocol):
    """Synchronous HTTP request engine using the `requests` library.

    This engine performs HTTP requests sequentially, serving as the baseline
    for performance comparison with threaded and async engines.

    Attributes:
        name: Always returns "sync".
        timeout: Request timeout in seconds (default: 30.0).

    Example:
        >>> engine = SyncEngine()
        >>> result = engine.run(["https://example.com", "https://example.org"])
        >>> print(f"Completed {len(result.results)} requests")
    """

    def __init__(self, timeout: float = 30.0) -> None:
        """Initialize the SyncEngine.

        Args:
            timeout: Request timeout in seconds (default: 30.0).
        """
        self._name = "sync"
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Name of the engine.

        Returns:
            Always returns "sync".
        """
        return self._name

    @property
    def timeout(self) -> float:
        """Request timeout in seconds.

        Returns:
            The timeout value passed to constructor.
        """
        return self._timeout

    def run(self, urls: list[str]) -> EngineResult:
        """Execute HTTP requests for the given URLs sequentially.

        Args:
            urls: List of URLs to request.

        Returns:
            An EngineResult containing all individual request results and aggregate metrics.
        """
        results: list[RequestResult] = []
        start_time = time.perf_counter()

        tracemalloc.start()

        try:
            with requests.Session() as session:
                for url in urls:
                    result = self._fetch_url(session, url)
                    results.append(result)
        finally:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peak_memory_mb = peak / (1024 * 1024)

        total_time = time.perf_counter() - start_time

        return EngineResult(
            results=results,
            total_time=total_time,
            peak_memory_mb=peak_memory_mb,
        )

    def _fetch_url(self, session: requests.Session, url: str) -> RequestResult:
        """Fetch a single URL and return the result.

        Args:
            session: The requests Session to use.
            url: The URL to fetch.

        Returns:
            A RequestResult containing the request outcome.
        """
        start_time = time.perf_counter()
        error: str | None = None
        status_code = 0

        try:
            response = session.get(url, timeout=self._timeout)
            response.raise_for_status()
            status_code = response.status_code
        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response else 0
            error = f"HTTP Error: {e}"
        except requests.Timeout:
            error = "Timeout"
        except requests.ConnectionError:
            error = "Connection Error"
        except requests.RequestException as e:
            error = f"Request Error: {e}"

        latency_ms = (time.perf_counter() - start_time) * 1000

        return RequestResult(
            url=url,
            status_code=status_code,
            latency_ms=latency_ms,
            # Use epoch completion time to keep metrics consistent across engines.
            timestamp=time.time(),
            attempt=1,
            error=error,
        )
