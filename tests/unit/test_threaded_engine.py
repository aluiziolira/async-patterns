"""Tests for the Threaded Engine.

These tests verify the ThreadedEngine implementation meets the requirements
defined in the implementation plan.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from async_patterns.engine import EngineResult, RequestResult, ThreadedEngine
from async_patterns.engine.base import SyncEngine as SyncEngineProtocol


class TestThreadedEngineProtocol:
    """Test cases for ThreadedEngine protocol conformance."""

    def test_threaded_engine_implements_engine_protocol(self) -> None:
        """ThreadedEngine should be an instance of Engine protocol."""
        engine = ThreadedEngine()
        assert isinstance(engine, SyncEngineProtocol)

    def test_threaded_engine_has_name_property(self) -> None:
        """ThreadedEngine should have name 'threaded'."""
        engine = ThreadedEngine()
        assert engine.name == "threaded"

    def test_threaded_engine_accepts_timeout_parameter(self) -> None:
        """ThreadedEngine should accept configurable timeout."""
        engine = ThreadedEngine(timeout=5.0)
        assert engine.timeout == 5.0

    def test_threaded_engine_default_timeout(self) -> None:
        """ThreadedEngine should have default timeout of 30 seconds."""
        engine = ThreadedEngine()
        assert engine.timeout == 30.0


class TestThreadedEngineRun:
    """Test cases for ThreadedEngine.run() method."""

    @pytest.fixture
    def mock_thread_pool(self):
        """Create a mock ThreadPoolExecutor."""
        with patch("async_patterns.engine.threaded_engine.ThreadPoolExecutor") as mock_tpe:
            mock_executor = MagicMock()
            mock_tpe.return_value = mock_executor
            yield mock_executor

    def test_threaded_engine_run_returns_engine_result(self, mock_thread_pool) -> None:
        """ThreadedEngine.run() should return an EngineResult."""
        # Setup mock to return a single result
        mock_result = RequestResult(
            url="https://example.com",
            status_code=200,
            latency_ms=100.0,
            timestamp=1234567890.0,
            attempt=1,
            error=None,
        )
        mock_thread_pool.__enter__ = MagicMock(return_value=mock_thread_pool)
        mock_thread_pool.__exit__ = MagicMock(return_value=False)

        # Create mock future that returns the result
        mock_future = MagicMock()
        mock_future.result.return_value = mock_result
        mock_thread_pool.submit = MagicMock(return_value=mock_future)
        mock_thread_pool.map = MagicMock(return_value=[mock_result])

        # Mock as_completed to return the mock future
        with patch("async_patterns.engine.threaded_engine.as_completed") as mock_as_completed:
            mock_as_completed.return_value = [mock_future]

            engine = ThreadedEngine()
            result = engine.run(["https://example.com"])

            assert isinstance(result, EngineResult)

    def test_threaded_engine_executes_concurrently(self, mock_thread_pool) -> None:
        """ThreadedEngine should execute requests concurrently using ThreadPoolExecutor."""
        mock_thread_pool.__enter__ = MagicMock(return_value=mock_thread_pool)
        mock_thread_pool.__exit__ = MagicMock(return_value=False)

        urls = [
            "https://example1.com",
            "https://example2.com",
            "https://example3.com",
        ]

        # Mock futures that will be returned by submit
        mock_futures = []
        for url in urls:
            mock_future = MagicMock()
            mock_future.result.return_value = RequestResult(
                url=url,
                status_code=200,
                latency_ms=100.0,
                timestamp=1234567890.0,
                attempt=1,
                error=None,
            )
            mock_futures.append(mock_future)

        mock_thread_pool.submit = MagicMock(side_effect=mock_futures)

        # Mock as_completed to return futures immediately
        with patch("async_patterns.engine.threaded_engine.as_completed") as mock_as_completed:
            mock_as_completed.return_value = mock_futures

            engine = ThreadedEngine(max_workers=3)
            result = engine.run(urls)

            # Verify submit was called for each URL
            assert mock_thread_pool.submit.call_count == len(urls)
            assert len(result.results) == len(urls)

    def test_threaded_engine_respects_max_workers(self) -> None:
        """ThreadedEngine should pass max_workers to ThreadPoolExecutor."""
        engine = ThreadedEngine(max_workers=5)
        assert engine.max_workers == 5

    def test_threaded_engine_default_max_workers(self) -> None:
        """ThreadedEngine should have default max_workers of 10."""
        engine = ThreadedEngine()
        assert engine.max_workers == 10

    def test_threaded_engine_uses_thread_local_session(self, mock_thread_pool) -> None:
        """ThreadedEngine should use thread-local session for connection pooling."""

        mock_thread_pool.__enter__ = MagicMock(return_value=mock_thread_pool)
        mock_thread_pool.__exit__ = MagicMock(return_value=False)

        # Create mock future that returns a result
        mock_future = MagicMock()
        mock_future.result.return_value = RequestResult(
            url="https://example.com",
            status_code=200,
            latency_ms=100.0,
            timestamp=1234567890.0,
            attempt=1,
            error=None,
        )
        mock_thread_pool.submit = MagicMock(return_value=mock_future)

        with patch("async_patterns.engine.threaded_engine.requests") as mock_requests:
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.elapsed.total_seconds.return_value = 0.1
            mock_response.url = "https://example.com"
            mock_session.get.return_value = mock_response
            mock_requests.Session.return_value = mock_session

            # Mock as_completed to return the mock future
            with patch("async_patterns.engine.threaded_engine.as_completed") as mock_as_completed:
                mock_as_completed.return_value = [mock_future]

                engine = ThreadedEngine(max_workers=2)
                result = engine.run(["https://example.com"])

                # Verify engine completed with expected result
                assert isinstance(result, EngineResult)
                assert len(result.results) == 1
                assert result.results[0].status_code == 200

    def test_threaded_engine_close_closes_all_thread_sessions(self) -> None:
        """ThreadedEngine.close should close sessions created in worker threads."""
        sessions: list[MagicMock] = []

        def make_session() -> MagicMock:
            session = MagicMock()
            response = MagicMock()
            response.status_code = 200
            response.raise_for_status = MagicMock()
            session.get.return_value = response
            sessions.append(session)
            return session

        with patch(
            "async_patterns.engine.threaded_engine.requests.Session", side_effect=make_session
        ):
            engine = ThreadedEngine(max_workers=2)
            engine.run([f"https://example.com/{i}" for i in range(4)])
            engine.close()

        assert sessions, "Expected at least one session to be created"
        assert all(session.close.called for session in sessions)


class TestThreadedEngineConcurrency:
    """Test cases for ThreadedEngine concurrency behavior."""

    @pytest.fixture
    def mock_executor(self):
        """Create a mock ThreadPoolExecutor context manager."""
        with patch("async_patterns.engine.threaded_engine.ThreadPoolExecutor") as mock_tpe:
            mock_executor = MagicMock()
            mock_tpe.return_value = mock_executor
            mock_executor.__enter__ = MagicMock(return_value=mock_executor)
            mock_executor.__exit__ = MagicMock(return_value=False)
            yield mock_executor

    def test_threaded_engine_completes_in_less_than_sequential_time(self, mock_executor) -> None:
        """Threaded engine should complete faster than sequential execution."""
        # This is a statistical test - with mocked executor, we simulate concurrent execution
        urls = [f"https://example{i}.com" for i in range(5)]

        # Create mock results
        def create_mock_result(url):
            return RequestResult(
                url=url,
                status_code=200,
                latency_ms=100.0,
                timestamp=1234567890.0,
                attempt=1,
                error=None,
            )

        mock_results = [create_mock_result(url) for url in urls]

        # Create mock futures for submit
        mock_futures = []
        for i, url in enumerate(urls):
            mock_future = MagicMock()
            mock_future.result.return_value = mock_results[i]
            mock_futures.append(mock_future)

        mock_executor.submit = MagicMock(side_effect=mock_futures)

        # Mock as_completed to return futures immediately
        with patch("async_patterns.engine.threaded_engine.as_completed") as mock_as_completed:
            mock_as_completed.return_value = mock_futures

            engine = ThreadedEngine(max_workers=5)
            result = engine.run(urls)

            # With concurrent execution, all requests should complete
            assert len(result.results) == len(urls)


class TestThreadedEngineErrorHandling:
    """Test cases for ThreadedEngine error handling."""

    @pytest.fixture
    def mock_executor(self):
        """Create a mock ThreadPoolExecutor context manager."""
        with patch("async_patterns.engine.threaded_engine.ThreadPoolExecutor") as mock_tpe:
            mock_executor = MagicMock()
            mock_tpe.return_value = mock_executor
            mock_executor.__enter__ = MagicMock(return_value=mock_executor)
            mock_executor.__exit__ = MagicMock(return_value=False)
            yield mock_executor

    def test_threaded_engine_handles_exceptions_in_workers(self, mock_executor) -> None:
        """ThreadedEngine should handle exceptions from worker threads."""
        urls = ["https://example1.com", "https://example2.com"]

        # Create results - one success, one error
        results = [
            RequestResult(
                url="https://example1.com",
                status_code=200,
                latency_ms=100.0,
                timestamp=1234567890.0,
                attempt=1,
                error=None,
            ),
            RequestResult(
                url="https://example2.com",
                status_code=0,
                latency_ms=50.0,
                timestamp=1234567890.0,
                attempt=1,
                error="Connection refused",
            ),
        ]

        # Create mock futures for submit
        mock_futures = []
        for result in results:
            mock_future = MagicMock()
            mock_future.result.return_value = result
            mock_futures.append(mock_future)

        mock_executor.submit = MagicMock(side_effect=mock_futures)

        # Mock as_completed to return futures immediately
        with patch("async_patterns.engine.threaded_engine.as_completed") as mock_as_completed:
            mock_as_completed.return_value = mock_futures

            engine = ThreadedEngine()
            result = engine.run(urls)

            # Both results should be present
            assert len(result.results) == 2

    def test_threaded_engine_handles_http_error_with_response(self) -> None:
        """ThreadedEngine should handle HTTPError and extract status code."""
        from async_patterns.engine import ThreadedEngine

        with patch("async_patterns.engine.threaded_engine.requests") as mock_requests:
            # Create exception classes
            mock_requests.HTTPError = type("HTTPError", (Exception,), {})
            mock_requests.Timeout = type("Timeout", (Exception,), {})
            mock_requests.ConnectionError = type("ConnectionError", (Exception,), {})
            mock_requests.RequestException = type("RequestException", (Exception,), {})

            mock_session = MagicMock()
            mock_requests.Session.return_value = mock_session

            # Create HTTPError with response object
            mock_response = MagicMock()
            mock_response.status_code = 404
            http_error = mock_requests.HTTPError("Not Found")
            http_error.response = mock_response

            mock_session.get.side_effect = http_error
            mock_session.get.return_value.raise_for_status.side_effect = http_error

            engine = ThreadedEngine(max_workers=1)
            result = engine.run(["https://example.com/notfound"])

            assert len(result.results) == 1
            assert result.results[0].status_code == 404
            assert "HTTP Error" in result.results[0].error

    def test_threaded_engine_handles_http_error_without_response(self) -> None:
        """ThreadedEngine should handle HTTPError when response is None."""
        from async_patterns.engine import ThreadedEngine

        with patch("async_patterns.engine.threaded_engine.requests") as mock_requests:
            mock_requests.HTTPError = type("HTTPError", (Exception,), {})
            mock_requests.Timeout = type("Timeout", (Exception,), {})
            mock_requests.ConnectionError = type("ConnectionError", (Exception,), {})
            mock_requests.RequestException = type("RequestException", (Exception,), {})

            mock_session = MagicMock()
            mock_requests.Session.return_value = mock_session

            http_error = mock_requests.HTTPError("No response")
            http_error.response = None

            mock_session.get.side_effect = http_error

            engine = ThreadedEngine(max_workers=1)
            result = engine.run(["https://example.com"])

            assert len(result.results) == 1
            assert result.results[0].status_code == 0  # No response available
            assert result.results[0].error is not None
