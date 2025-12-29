"""Tests for domain models.

These tests verify the RequestResult, RequestStatus, and EngineResult dataclasses
meet the requirements defined in the implementation plan.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from async_patterns.engine import RequestResult, RequestStatus


class TestRequestResult:
    """Test cases for the RequestResult dataclass."""

    def test_request_result_class_exists(self) -> None:
        """RequestResult class should be importable."""
        assert RequestResult is not None

    def test_request_result_is_frozen(self) -> None:
        """RequestResult should be immutable (frozen=True)."""
        result = RequestResult(
            url="https://example.com",
            status_code=200,
            latency_ms=100.0,
            timestamp=1234567890.0,
            attempt=1,
            error=None,
        )
        with pytest.raises(FrozenInstanceError):
            result.status_code = 404

    def test_request_result_has_slots(self) -> None:
        """RequestResult should use __slots__ for memory efficiency."""
        assert hasattr(RequestResult, "__slots__")

    def test_request_result_fields(self) -> None:
        """RequestResult should have all required fields."""
        result = RequestResult(
            url="https://example.com",
            status_code=200,
            latency_ms=100.0,
            timestamp=1234567890.0,
            attempt=1,
            error=None,
        )
        assert result.url == "https://example.com"
        assert result.status_code == 200
        assert result.latency_ms == 100.0
        assert result.timestamp == 1234567890.0
        assert result.attempt == 1
        assert result.error is None

    def test_request_result_with_error(self) -> None:
        """RequestResult should store error information."""
        result = RequestResult(
            url="https://example.com",
            status_code=0,
            latency_ms=50.0,
            timestamp=1234567890.0,
            attempt=1,
            error="Connection timeout",
        )
        assert result.error == "Connection timeout"


class TestRequestStatus:
    """Test cases for the RequestStatus enum."""

    def test_request_status_class_exists(self) -> None:
        """RequestStatus class should be importable."""
        assert RequestStatus is not None

    def test_request_status_has_pending(self) -> None:
        """RequestStatus should have PENDING value."""
        assert hasattr(RequestStatus, "PENDING")
        assert RequestStatus.PENDING.value == "pending"

    def test_request_status_has_in_flight(self) -> None:
        """RequestStatus should have IN_FLIGHT value."""
        assert hasattr(RequestStatus, "IN_FLIGHT")
        assert RequestStatus.IN_FLIGHT.value == "in_flight"

    def test_request_status_has_success(self) -> None:
        """RequestStatus should have SUCCESS value."""
        assert hasattr(RequestStatus, "SUCCESS")
        assert RequestStatus.SUCCESS.value == "success"

    def test_request_status_has_failed(self) -> None:
        """RequestStatus should have FAILED value."""
        assert hasattr(RequestStatus, "FAILED")
        assert RequestStatus.FAILED.value == "failed"

    def test_request_status_has_retrying(self) -> None:
        """RequestStatus should have RETRYING value."""
        assert hasattr(RequestStatus, "RETRYING")
        assert RequestStatus.RETRYING.value == "retrying"

    def test_request_status_values_are_strings(self) -> None:
        """All RequestStatus values should be strings."""
        for member in RequestStatus:
            assert isinstance(member.value, str)


class TestEngineResult:
    """Test cases for the EngineResult dataclass."""

    def test_engine_result_class_exists(self) -> None:
        """EngineResult class should be importable."""
        from async_patterns.engine import EngineResult

        assert EngineResult is not None

    def test_engine_result_fields(self) -> None:
        """EngineResult should have all required fields."""
        from async_patterns.engine import EngineResult

        results = [
            RequestResult(
                url="https://example.com",
                status_code=200,
                latency_ms=100.0,
                timestamp=1234567890.0,
                attempt=1,
                error=None,
            )
        ]
        engine_result = EngineResult(
            results=results,
            total_time=1.5,
            peak_memory_mb=50.5,
        )
        assert engine_result.results == results
        assert engine_result.total_time == 1.5
        assert engine_result.peak_memory_mb == 50.5

    def test_engine_result_has_rps_property(self) -> None:
        """EngineResult should have rps (requests per second) computed property."""
        from async_patterns.engine import EngineResult

        results = [
            RequestResult(
                url=f"https://example{i}.com",
                status_code=200,
                latency_ms=100.0,
                timestamp=1234567890.0,
                attempt=1,
                error=None,
            )
            for i in range(10)
        ]
        engine_result = EngineResult(
            results=results,
            total_time=1.0,
            peak_memory_mb=50.5,
        )
        assert hasattr(engine_result, "rps")
        assert engine_result.rps == 10.0

    def test_engine_result_has_success_count_property(self) -> None:
        """EngineResult should have success_count computed property."""
        from async_patterns.engine import EngineResult

        results = [
            RequestResult(
                url=f"https://example{i}.com",
                status_code=200 if i < 7 else 0,
                latency_ms=100.0,
                timestamp=1234567890.0,
                attempt=1,
                error=None if i < 7 else "Error",
            )
            for i in range(10)
        ]
        engine_result = EngineResult(
            results=results,
            total_time=1.0,
            peak_memory_mb=50.5,
        )
        assert hasattr(engine_result, "success_count")
        assert engine_result.success_count == 7

    def test_engine_result_has_error_count_property(self) -> None:
        """EngineResult should have error_count computed property."""
        from async_patterns.engine import EngineResult

        results = [
            RequestResult(
                url=f"https://example{i}.com",
                status_code=200 if i < 7 else 0,
                latency_ms=100.0,
                timestamp=1234567890.0,
                attempt=1,
                error=None if i < 7 else "Error",
            )
            for i in range(10)
        ]
        engine_result = EngineResult(
            results=results,
            total_time=1.0,
            peak_memory_mb=50.5,
        )
        assert hasattr(engine_result, "error_count")
        assert engine_result.error_count == 3

    def test_engine_result_error_count_http_error_only(self) -> None:
        """error_count should count 4xx/5xx status codes."""
        from async_patterns.engine import EngineResult, RequestResult

        results = [
            RequestResult(
                url="a",
                status_code=404,
                latency_ms=10,
                timestamp=0,
                attempt=1,
                error=None,
            ),
            RequestResult(
                url="b",
                status_code=500,
                latency_ms=10,
                timestamp=0,
                attempt=1,
                error=None,
            ),
            RequestResult(
                url="c",
                status_code=200,
                latency_ms=10,
                timestamp=0,
                attempt=1,
                error=None,
            ),
        ]
        engine_result = EngineResult(results=results, total_time=1.0, peak_memory_mb=10.0)
        assert engine_result.error_count == 2

    def test_engine_result_error_count_exception_only(self) -> None:
        """error_count should count requests with exception errors."""
        from async_patterns.engine import EngineResult, RequestResult

        results = [
            RequestResult(
                url="a",
                status_code=0,
                latency_ms=10,
                timestamp=0,
                attempt=1,
                error="Timeout",
            ),
            RequestResult(
                url="b",
                status_code=200,
                latency_ms=10,
                timestamp=0,
                attempt=1,
                error=None,
            ),
        ]
        engine_result = EngineResult(results=results, total_time=1.0, peak_memory_mb=10.0)
        assert engine_result.error_count == 1

    def test_engine_result_error_count_no_double_count(self) -> None:
        """error_count should not double-count HTTP errors with exception message."""
        from async_patterns.engine import EngineResult, RequestResult

        results = [
            # HTTP 404 with error message - should count as 1, not 2
            RequestResult(
                url="a",
                status_code=404,
                latency_ms=10,
                timestamp=0,
                attempt=1,
                error="HTTP 404",
            ),
            RequestResult(
                url="b",
                status_code=200,
                latency_ms=10,
                timestamp=0,
                attempt=1,
                error=None,
            ),
        ]
        engine_result = EngineResult(results=results, total_time=1.0, peak_memory_mb=10.0)
        assert engine_result.error_count == 1  # Not 2


class TestConnectionConfig:
    """Test cases for ConnectionConfig dataclass."""

    def test_connection_config_exists(self) -> None:
        """ConnectionConfig class should be importable."""
        from async_patterns.engine import ConnectionConfig

        assert ConnectionConfig is not None

    def test_connection_config_default_values(self) -> None:
        """ConnectionConfig should have sensible defaults."""
        from async_patterns.engine import ConnectionConfig

        config = ConnectionConfig()
        assert config.max_connections == 100
        assert config.timeout == 30.0
        assert config.max_keepalive_connections == 20
        assert config.keepalive_expiry == 30.0
        assert config.http2 is True

    def test_connection_config_custom_values(self) -> None:
        """ConnectionConfig should accept custom values."""
        from async_patterns.engine import ConnectionConfig

        config = ConnectionConfig(
            max_connections=50,
            timeout=10.0,
            max_keepalive_connections=10,
            keepalive_expiry=60.0,
            http2=False,
        )
        assert config.max_connections == 50
        assert config.timeout == 10.0
        assert config.max_keepalive_connections == 10
        assert config.keepalive_expiry == 60.0
        assert config.http2 is False

    def test_connection_config_http2_default_enabled(self) -> None:
        """HTTP/2 should be enabled by default for better performance."""
        from async_patterns.engine import ConnectionConfig

        config = ConnectionConfig()
        assert config.http2 is True

    def test_connection_config_http2_can_be_disabled(self) -> None:
        """HTTP/2 can be disabled for HTTP/1.1-only servers."""
        from async_patterns.engine import ConnectionConfig

        config = ConnectionConfig(http2=False)
        assert config.http2 is False

    def test_connection_config_keepalive_expiry_default(self) -> None:
        """Keepalive expiry should default to 30 seconds."""
        from async_patterns.engine import ConnectionConfig

        config = ConnectionConfig()
        assert config.keepalive_expiry == 30.0

    def test_connection_config_keepalive_expiry_custom(self) -> None:
        """Keepalive expiry can be configured for different workload patterns."""
        from async_patterns.engine import ConnectionConfig

        config = ConnectionConfig(keepalive_expiry=120.0)
        assert config.keepalive_expiry == 120.0

    def test_connection_config_is_frozen(self) -> None:
        """ConnectionConfig should be immutable."""
        from dataclasses import FrozenInstanceError

        from async_patterns.engine import ConnectionConfig

        config = ConnectionConfig()
        with pytest.raises(FrozenInstanceError):
            config.max_connections = 200


class TestRetryConfig:
    """Test cases for RetryConfig dataclass."""

    def test_retry_config_default_values(self) -> None:
        """RetryConfig should have sensible defaults."""
        from async_patterns.engine.models import RetryConfig

        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0
        assert config.jitter_factor == 0.1

    def test_retry_config_calculate_delay(self) -> None:
        """RetryConfig should calculate exponential backoff delay."""
        from async_patterns.engine.models import RetryConfig

        config = RetryConfig(base_delay=1.0, max_delay=60.0, jitter_factor=0.0)

        # Attempt 1: 1.0 * 2^0 = 1.0
        assert config.calculate_delay(attempt=1) == 1.0
        # Attempt 2: 1.0 * 2^1 = 2.0
        assert config.calculate_delay(attempt=2) == 2.0
        # Attempt 3: 1.0 * 2^2 = 4.0
        assert config.calculate_delay(attempt=3) == 4.0

    def test_retry_config_respects_max_delay(self) -> None:
        """RetryConfig should cap delay at max_delay."""
        from async_patterns.engine.models import RetryConfig

        config = RetryConfig(base_delay=10.0, max_delay=30.0, jitter_factor=0.0)

        # Attempt 5: 10.0 * 2^4 = 160.0, capped at 30.0
        assert config.calculate_delay(attempt=5) == 30.0


class TestEngineResultPercentiles:
    """Test cases for EngineResult latency percentile properties."""

    def test_engine_result_p50_latency(self) -> None:
        """EngineResult should calculate P50 (median) latency."""
        from async_patterns.engine import EngineResult, RequestResult

        # Create 10 results with latencies 10, 20, 30, ..., 100
        results = [
            RequestResult(
                url=f"url{i}",
                status_code=200,
                latency_ms=i * 10,
                timestamp=0,
                attempt=1,
                error=None,
            )
            for i in range(1, 11)
        ]
        engine_result = EngineResult(results=results, total_time=1.0, peak_memory_mb=10.0)
        # P50 of [10,20,30,40,50,60,70,80,90,100] = 55 (average of 50 and 60)
        assert engine_result.p50_latency_ms == 55.0

    def test_engine_result_p95_latency(self) -> None:
        """EngineResult should calculate P95 latency."""
        from async_patterns.engine import EngineResult, RequestResult

        results = [
            RequestResult(
                url=f"url{i}",
                status_code=200,
                latency_ms=i,
                timestamp=0,
                attempt=1,
                error=None,
            )
            for i in range(1, 101)
        ]
        engine_result = EngineResult(results=results, total_time=1.0, peak_memory_mb=10.0)
        # P95 of 1-100 ≈ 95.05
        assert 94.0 <= engine_result.p95_latency_ms <= 96.0

    def test_engine_result_p99_latency(self) -> None:
        """EngineResult should calculate P99 latency."""
        from async_patterns.engine import EngineResult, RequestResult

        results = [
            RequestResult(
                url=f"url{i}",
                status_code=200,
                latency_ms=i,
                timestamp=0,
                attempt=1,
                error=None,
            )
            for i in range(1, 101)
        ]
        engine_result = EngineResult(results=results, total_time=1.0, peak_memory_mb=10.0)
        # P99 of 1-100 ≈ 99.01
        assert 98.0 <= engine_result.p99_latency_ms <= 100.0

    def test_engine_result_percentiles_empty_results(self) -> None:
        """Percentile properties should return 0 for empty results."""
        from async_patterns.engine import EngineResult

        engine_result = EngineResult(results=[], total_time=0, peak_memory_mb=0)
        assert engine_result.p50_latency_ms == 0.0
        assert engine_result.p95_latency_ms == 0.0
        assert engine_result.p99_latency_ms == 0.0
