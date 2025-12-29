"""Threaded HTTP Engine.

This module implements the threaded HTTP request engine using ThreadPoolExecutor
and the `requests` library for concurrent data acquisition.
"""

from __future__ import annotations

import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from async_patterns.engine.base import Engine
from async_patterns.engine.models import EngineResult, RequestResult


class ThreadLocalSession(threading.local):
    """Thread-local session manager for connection pooling.

    This class ensures each thread has its own requests Session for
    proper connection pooling while maintaining thread safety.
    """

    def __init__(self) -> None:
        """Initialize the thread-local session."""
        super().__init__()
        self.session: requests.Session | None = None

    def get_session(self) -> requests.Session:
        """Get or create a session for the current thread.

        Returns:
            A requests Session instance.
        """
        if self.session is None:
            self.session = requests.Session()
        return self.session


class ThreadedEngine(Engine):
    """Threaded HTTP request engine.

    This engine performs HTTP requests concurrently using ThreadPoolExecutor.
    It uses thread-local sessions for connection pooling.

    Attributes:
        name: Always returns "threaded".
        max_workers: Maximum number of worker threads (default: 10).
        timeout: Request timeout in seconds (default: 30.0).

    Example:
        >>> engine = ThreadedEngine(max_workers=5)
        >>> result = engine.run(["https://example.com", "https://example.org"])
        >>> print(f"Completed {len(result.results)} requests in {result.total_time:.2f}s")
    """

    def __init__(self, max_workers: int = 10, timeout: float = 30.0) -> None:
        """Initialize the ThreadedEngine.

        Args:
            max_workers: Maximum number of worker threads (default: 10).
            timeout: Request timeout in seconds (default: 30.0).
        """
        self._name = "threaded"
        self._max_workers = max_workers
        self._timeout = timeout
        self._session_manager = ThreadLocalSession()

    @property
    def name(self) -> str:
        """Name of the engine.

        Returns:
            Always returns "threaded".
        """
        return self._name

    @property
    def max_workers(self) -> int:
        """Maximum number of worker threads.

        Returns:
            The max_workers value passed to constructor.
        """
        return self._max_workers

    @property
    def timeout(self) -> float:
        """Request timeout in seconds.

        Returns:
            The timeout value passed to constructor.
        """
        return self._timeout

    def run(self, urls: list[str]) -> EngineResult:
        """Execute HTTP requests for the given URLs concurrently.

        Args:
            urls: List of URLs to request.

        Returns:
            An EngineResult containing all individual request results and aggregate metrics.
        """
        results: list[RequestResult] = []
        start_time = time.perf_counter()

        # Start memory tracking
        tracemalloc.start()

        try:
            with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                # Submit all tasks
                futures = {executor.submit(self._fetch_url, url): url for url in urls}

                # Collect results as they complete
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        result = future.result()
                        results.append(result)
                    except Exception as e:  # pylint: disable=broad-except
                        # Handle any unexpected exceptions
                        error_result = RequestResult(
                            url=url,
                            status_code=0,
                            latency_ms=0,
                            timestamp=time.time(),
                            attempt=1,
                            error=f"Unexpected error: {e}",
                        )
                        results.append(error_result)
        finally:
            # Capture peak memory
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peak_memory_mb = peak / (1024 * 1024)  # Convert to MB

        total_time = time.perf_counter() - start_time

        return EngineResult(
            results=results,
            total_time=total_time,
            peak_memory_mb=peak_memory_mb,
        )

    def _fetch_url(self, url: str) -> RequestResult:
        """Fetch a single URL and return the result.

        This method uses a thread-local session for connection pooling.

        Args:
            url: The URL to fetch.

        Returns:
            A RequestResult containing the request outcome.
        """
        session = self._session_manager.get_session()
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
            timestamp=time.time(),
            attempt=1,
            error=error,
        )
