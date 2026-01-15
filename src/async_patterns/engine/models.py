"""Domain models for the async-patterns benchmark engines.

This module defines the core data structures used across all engine implementations.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from enum import Enum
from typing import Any


class RequestStatus(str, Enum):
    """Status of an individual HTTP request.

    Attributes:
        PENDING: Request has been queued but not yet sent.
        IN_FLIGHT: Request is currently being processed.
        SUCCESS: Request completed with a 2xx status code.
        FAILED: Request failed with a non-2xx status or exception.
        RETRYING: Request failed and is being retried.
    """

    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass(frozen=True, slots=True)
class RequestResult:
    """Result of a single HTTP request.

    This dataclass is immutable (frozen=True) and uses slots for memory efficiency.

    Attributes:
        url: The URL that was requested.
        status_code: HTTP status code (0 if request failed before getting response).
        latency_ms: Time taken for the request in milliseconds.
        timestamp: Unix timestamp when the request completed.
        attempt: Which attempt number this was (1 for first attempt).
        error: Error message if request failed, None otherwise.
    """

    url: str
    status_code: int
    latency_ms: float
    timestamp: float
    attempt: int
    error: str | None


@dataclass(frozen=True, slots=True)
class EngineResult:
    """Aggregate result from a benchmark engine run.

    This dataclass is immutable (frozen=True) and uses slots for memory efficiency.

    Attributes:
        results: List of individual RequestResult objects.
        total_time: Total time taken for all requests in seconds.
        peak_memory_mb: Peak memory usage in megabytes.
    """

    results: list[RequestResult]
    total_time: float
    peak_memory_mb: float

    @property
    def rps(self) -> float:
        """Requests per second throughput.

        Returns:
            Number of requests completed per second.
        """
        if self.total_time == 0:
            return 0.0
        return len(self.results) / self.total_time

    @property
    def success_count(self) -> int:
        """Number of successful requests.

        Returns:
            Count of requests with status code 2xx and no error.
        """
        return sum(1 for r in self.results if r.status_code >= 200 and r.status_code < 300)

    @property
    def error_count(self) -> int:
        """Number of failed requests.

        Returns:
            Count of requests with errors or non-2xx status codes.
        """
        return sum(1 for r in self.results if r.error is not None or r.status_code >= 400)

    @property
    def p50_latency_ms(self) -> float:
        """Median (P50) latency in milliseconds."""
        if not self.results:
            return 0.0
        latencies = sorted(r.latency_ms for r in self.results)
        return statistics.median(latencies)

    @property
    def p95_latency_ms(self) -> float:
        """95th percentile latency in milliseconds."""
        if not self.results:
            return 0.0
        latencies = sorted(r.latency_ms for r in self.results)
        return self._percentile(latencies, 0.95)

    @property
    def p99_latency_ms(self) -> float:
        """99th percentile latency in milliseconds."""
        if not self.results:
            return 0.0
        latencies = sorted(r.latency_ms for r in self.results)
        return self._percentile(latencies, 0.99)

    def _percentile(self, sorted_data: list[float], p: float) -> float:
        """Calculate percentile from sorted data using linear interpolation."""
        n: int = len(sorted_data)
        if n == 0:
            return 0.0
        k: float = (n - 1) * p
        f: int = int(k)
        c: int = f + 1 if f + 1 < n else f
        val_f: float = sorted_data[f]
        val_c: float = sorted_data[c]
        result: float = val_f + (k - f) * (val_c - val_f)
        return result

    @property
    def error_breakdown(self) -> dict[str, int]:
        """Breakdown of errors by type.

        Returns:
            Dictionary mapping error type to count.
            Example: {"ConnectionError": 5, "TimeoutError": 3, "HTTP 500": 2}
        """
        breakdown: dict[str, int] = {}
        for r in self.results:
            if r.error:
                # Extract error type from message
                error_type = r.error.split(":")[0] if ":" in r.error else r.error
                breakdown[error_type] = breakdown.get(error_type, 0) + 1
            elif r.status_code >= 400:
                key = f"HTTP {r.status_code}"
                breakdown[key] = breakdown.get(key, 0) + 1
        return breakdown

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary for serialization."""
        return {
            "total_requests": len(self.results),
            "successful": self.success_count,
            "failed": self.error_count,
            "total_duration_seconds": self.total_time,
            "requests_per_second": self.rps,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "p99_latency_ms": self.p99_latency_ms,
            "peak_memory_mb": self.peak_memory_mb,
            "error_breakdown": self.error_breakdown,
        }

    def to_json(self) -> str:
        """Convert result to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


@dataclass(frozen=True, slots=True)
class ConnectionConfig:
    """Configuration for HTTP connection pooling.

    This dataclass provides a unified configuration interface for all engine
    implementations, ensuring consistent connection management.

    Attributes:
        max_connections: Maximum number of concurrent connections (default: 100).
        timeout: Request timeout in seconds (default: 30.0).
        max_keepalive_connections: Maximum keep-alive connections to maintain (default: 20).
        keepalive_expiry: Seconds before closing idle keep-alive connections (default: 30.0).
    """

    max_connections: int = 100
    timeout: float = 30.0
    max_keepalive_connections: int = 20
    keepalive_expiry: float = 30.0


@dataclass(frozen=True, slots=True)
class RetryConfig:
    """Configuration for retry logic with exponential backoff.

    Implements the formula: delay = min(base_delay × 2^attempt + jitter, max_delay)

    Attributes:
        max_attempts: Total number of attempts including initial request (default: 3).
        base_delay: Base delay in seconds (default: 1.0).
        max_delay: Maximum delay cap in seconds (default: 60.0).
        jitter_factor: Jitter as fraction of delay (default: 0.1).
        retry_budget: Maximum retries allowed per window (default: 10).
        retry_budget_window_seconds: Window size in seconds (default: 60.0).
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter_factor: float = 0.1
    retry_budget: int = 10
    retry_budget_window_seconds: float = 60.0

    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number.

        Args:
            attempt: The attempt number (1-indexed).

        Returns:
            Delay in seconds (without jitter for deterministic testing).
        """
        delay: float = self.base_delay * (2 ** (attempt - 1))
        return float(min(delay, self.max_delay))


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Benchmark execution report with PRD-compliant latency and resource metrics.

    This dataclass is immutable (frozen=True) and uses slots for memory efficiency.

    Attributes:
        total_requests: Total number of requests made.
        successful: Number of successful requests (2xx status codes).
        failed: Number of failed requests.
        total_duration_seconds: Total benchmark duration in seconds.
        requests_per_second: Throughput in requests per second.
        avg_latency_ms: Average latency in milliseconds.
        p50_latency_ms: Median latency in milliseconds.
        p95_latency_ms: 95th percentile latency in milliseconds.
        p99_latency_ms: 99th percentile latency in milliseconds.
        peak_memory_mb: Peak memory usage in megabytes.
        error_breakdown: Dictionary mapping error type to count.
    """

    total_requests: int
    successful: int
    failed: int
    total_duration_seconds: float
    requests_per_second: float
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    peak_memory_mb: float
    error_breakdown: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary for JSON serialization.

        Returns:
            Dictionary representation of the execution report.
        """
        return {
            "total_requests": self.total_requests,
            "successful": self.successful,
            "failed": self.failed,
            "total_duration_seconds": self.total_duration_seconds,
            "requests_per_second": self.requests_per_second,
            "avg_latency_ms": self.avg_latency_ms,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "p99_latency_ms": self.p99_latency_ms,
            "peak_memory_mb": self.peak_memory_mb,
            "error_breakdown": self.error_breakdown,
        }
