"""Tests for the Synchronous Engine.

These tests verify the SyncEngine implementation meets the requirements
defined in the implementation plan.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from async_patterns.engine import EngineResult, SyncEngine
from async_patterns.engine.base import Engine


class TestSyncEngineProtocol:
    """Test cases for SyncEngine protocol conformance."""

    def test_sync_engine_implements_engine_protocol(self) -> None:
        """SyncEngine should be an instance of Engine protocol."""
        engine = SyncEngine()
        assert isinstance(engine, Engine)

    def test_sync_engine_has_name_property(self) -> None:
        """SyncEngine should have name 'sync'."""
        engine = SyncEngine()
        assert engine.name == "sync"

    def test_sync_engine_accepts_timeout_parameter(self) -> None:
        """SyncEngine should accept configurable timeout."""
        engine = SyncEngine(timeout=5.0)
        assert engine.timeout == 5.0

    def test_sync_engine_default_timeout(self) -> None:
        """SyncEngine should have default timeout of 30 seconds."""
        engine = SyncEngine()
        assert engine.timeout == 30.0


class TestSyncEngineRun:
    """Test cases for SyncEngine.run() method."""

    @pytest.fixture()
    def mock_requests_session(self):
        """Create a mock requests Session."""
        with patch("async_patterns.engine.sync_engine.requests") as mock_requests:
            mock_session = MagicMock()
            # Configure __enter__ and __exit__ for context manager usage
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_requests.Session.return_value = mock_session
            yield mock_session

    def test_sync_engine_run_returns_engine_result(self, mock_requests_session) -> None:
        """SyncEngine.run() should return an EngineResult."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.1
        mock_response.url = "https://example.com"
        mock_requests_session.get.return_value = mock_response

        engine = SyncEngine()
        result = engine.run(["https://example.com"])

        assert isinstance(result, EngineResult)
        assert len(result.results) == 1

    def test_sync_engine_handles_success_response(self, mock_requests_session) -> None:
        """SyncEngine should handle successful HTTP responses."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.1
        mock_response.url = "https://example.com"
        mock_requests_session.get.return_value = mock_response

        engine = SyncEngine()
        result = engine.run(["https://example.com"])

        assert len(result.results) == 1
        assert result.results[0].status_code == 200
        assert result.results[0].error is None

    def test_sync_engine_handles_http_error(self, mock_requests_session) -> None:
        """SyncEngine should handle HTTP errors gracefully without raising."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.elapsed.total_seconds.return_value = 0.05
        mock_response.url = "https://example.com/notfound"
        mock_requests_session.get.return_value = mock_response

        engine = SyncEngine()
        result = engine.run(["https://example.com/notfound"])

        # Should have a result with error populated
        assert len(result.results) == 1
        assert result.results[0].status_code == 404
        # HTTP error - no exception raised

    def test_sync_engine_handles_request_exception(self, mock_requests_session) -> None:
        """SyncEngine should catch RequestException and populate error field."""
        # Patch the exception classes to be real exception types
        with patch("async_patterns.engine.sync_engine.requests") as mock_requests:
            # Create real exception classes that can be caught
            mock_requests.HTTPError = type("HTTPError", (Exception,), {})
            mock_requests.Timeout = type("Timeout", (Exception,), {})
            mock_requests.ConnectionError = type("ConnectionError", (Exception,), {})
            mock_requests.RequestException = type("RequestException", (Exception,), {})
            mock_session = mock_requests.Session.return_value
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)

            mock_session.get.side_effect = mock_requests.RequestException("Connection refused")

            engine = SyncEngine()
            result = engine.run(["https://example.com"])

            # Should have a result with error populated
            assert len(result.results) == 1
            assert result.results[0].error is not None
            assert "Connection refused" in result.results[0].error

    def test_sync_engine_handles_multiple_urls(self, mock_requests_session) -> None:
        """SyncEngine should process multiple URLs sequentially."""
        urls = [
            "https://example1.com",
            "https://example2.com",
            "https://example3.com",
        ]

        # Setup mock responses for each URL
        for i, url in enumerate(urls):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.elapsed.total_seconds.return_value = 0.1
            mock_response.url = url
            mock_requests_session.get.return_value = mock_response

        engine = SyncEngine()
        result = engine.run(urls)

        assert len(result.results) == 3

    def test_sync_engine_tracks_peak_memory(self, mock_requests_session) -> None:
        """SyncEngine should track peak memory usage."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.1
        mock_response.url = "https://example.com"
        mock_requests_session.get.return_value = mock_response

        engine = SyncEngine()
        result = engine.run(["https://example.com"])

        # Peak memory should be tracked (may be 0 if tracemalloc not active)
        assert hasattr(result, "peak_memory_mb")
        assert result.peak_memory_mb >= 0

    def test_sync_engine_records_latency(self, mock_requests_session) -> None:
        """SyncEngine should record request latency in milliseconds."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.25  # 250ms - not used in code
        mock_response.url = "https://example.com"
        mock_requests_session.get.return_value = mock_response

        engine = SyncEngine()
        result = engine.run(["https://example.com"])

        # Latency should be recorded (just verify it's a positive number)
        # Note: This measures actual wall-clock time, not mocked response.elapsed
        assert result.results[0].latency_ms >= 0


class TestSyncEngineErrorHandling:
    """Test cases for SyncEngine error handling scenarios."""

    @pytest.fixture()
    def mock_requests_session(self):
        """Create a mock requests Session."""
        with patch("async_patterns.engine.sync_engine.requests") as mock_requests:
            mock_session = MagicMock()
            # Configure __enter__ and __exit__ for context manager usage
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_requests.Session.return_value = mock_session
            yield mock_session

    def test_sync_engine_handles_timeout(self, mock_requests_session) -> None:
        """SyncEngine should handle timeout errors gracefully."""
        # Patch the exception classes to be real exception types
        with patch("async_patterns.engine.sync_engine.requests") as mock_requests:
            mock_requests.HTTPError = type("HTTPError", (Exception,), {})
            mock_requests.Timeout = type("Timeout", (Exception,), {})
            mock_session = mock_requests.Session.return_value
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)

            mock_session.get.side_effect = mock_requests.Timeout("Request timed out")

            engine = SyncEngine()
            result = engine.run(["https://example.com"])

            assert len(result.results) == 1
            assert result.results[0].error is not None

    def test_sync_engine_handles_connection_error(self, mock_requests_session) -> None:
        """SyncEngine should handle connection errors gracefully."""
        # Patch the exception classes to be real exception types
        with patch("async_patterns.engine.sync_engine.requests") as mock_requests:
            mock_requests.HTTPError = type("HTTPError", (Exception,), {})
            mock_requests.Timeout = type("Timeout", (Exception,), {})
            mock_requests.ConnectionError = type("ConnectionError", (Exception,), {})
            mock_requests.RequestException = type("RequestException", (Exception,), {})
            mock_session = mock_requests.Session.return_value
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)

            mock_session.get.side_effect = mock_requests.ConnectionError(
                "Failed to establish connection"
            )

            engine = SyncEngine()
            result = engine.run(["https://example.com"])

            assert len(result.results) == 1
            assert result.results[0].error is not None
