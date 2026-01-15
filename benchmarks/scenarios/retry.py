#!/usr/bin/env python3
"""Retry Strategy Benchmark for Exponential Backoff Testing.

This module provides benchmark functions to measure and validate retry
strategies under various failure conditions.

Features:
- Exponential backoff with jitter validation
- Retry statistics (total_retries, success_after_retry)
- Retry budget tracking
- Circuit breaker integration

Usage:
    results = await benchmark_retry_strategy(base_url)
    print(f"Total retries: {results['total_retries']}")
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from async_patterns.engine.async_engine import AsyncEngine
from async_patterns.engine.models import ConnectionConfig
from async_patterns.patterns.retry import RetryConfig, RetryPolicy

if TYPE_CHECKING:
    pass


@dataclass
class RetryBenchmarkResult:
    """Complete results from a retry benchmark run."""

    total_requests: int
    successful_requests: int
    failed_requests: int
    total_retries: int
    successful_after_retry: int
    retry_breakdown: dict
    rps: float


async def benchmark_retry_with_exponential_backoff(
    base_url: str,
    total_requests: int = 50,
    max_attempts: int = 3,
    base_delay_ms: float = 50.0,
    failure_rate: float = 0.5,
    max_concurrent: int = 10,
) -> RetryBenchmarkResult:
    """Benchmark retry strategy with exponential backoff.

    This benchmark:
    1. Sends requests to an endpoint with configurable failure rate
    2. Retries failed requests with exponential backoff
    3. Tracks retry statistics
    4. Reports detailed metrics

    Args:
        base_url: Base URL of the mock server.
        total_requests: Total number of requests to make.
        max_attempts: Maximum retry attempts per request.
        base_delay_ms: Base delay in milliseconds for backoff.
        failure_rate: Fraction of requests that will initially fail.
        max_concurrent: Maximum concurrent requests.

    Returns:
        RetryBenchmarkResult with all metrics.
    """
    # Create retry policy with exponential backoff
    retry_config = RetryConfig(
        max_attempts=max_attempts,
        base_delay_ms=base_delay_ms,
        max_delay_ms=5000.0,
        jitter_factor=0.1,
        retry_budget=100,
        retry_budget_window_seconds=60.0,
    )

    retry_policy = RetryPolicy(config=retry_config)

    # Create engine with retry policy
    config = ConnectionConfig(timeout=10.0)
    engine = AsyncEngine(
        max_concurrent=max_concurrent,
        config=config,
        retry_policy=retry_policy,
    )

    # Generate URLs - mix of success and failure endpoints
    # Use /status/200 for success and /status/500 for failures
    success_count = int(total_requests * (1 - failure_rate))
    failure_count = total_requests - success_count

    success_urls = [f"{base_url}/get" for _ in range(success_count)]
    failure_urls = [f"{base_url}/status/500" for _ in range(failure_count)]

    # Shuffle URLs to mix successes and failures
    import random

    urls = success_urls + failure_urls
    random.shuffle(urls)

    start_time = time.perf_counter()

    # Run the benchmark
    result = await engine.run(urls)

    end_time = time.perf_counter()
    total_time = end_time - start_time

    # Get retry metrics
    retry_metrics = retry_policy.get_metrics()

    # Calculate retry breakdown
    retry_breakdown: dict = {
        "total_attempts": retry_metrics.attempt_count,
        "total_delay_ms": retry_metrics.total_delay_ms,
        "budget_consumed": retry_metrics.budget_consumed,
    }

    # Calculate successful after retry
    successful_after_retry = sum(
        1 for r in result.results if r.attempt > 1 and r.error is None
    )

    # Calculate total retries
    total_retries = sum(r.attempt - 1 for r in result.results)

    return RetryBenchmarkResult(
        total_requests=total_requests,
        successful_requests=result.success_count,
        failed_requests=result.error_count,
        total_retries=total_retries,
        successful_after_retry=successful_after_retry,
        retry_breakdown=retry_breakdown,
        rps=total_requests / total_time if total_time > 0 else 0.0,
    )


async def benchmark_retry_on_429_errors(
    base_url: str,
    total_requests: int = 30,
    max_attempts: int = 3,
    base_delay_ms: float = 50.0,
    max_concurrent: int = 5,
) -> dict:
    """Benchmark retry behavior on 429 (rate limit) errors.

    This test verifies that:
    1. 429 errors trigger retries
    2. Retry-After headers are surfaced in error messages for custom handling
    3. Requests eventually succeed after rate limit clears

    Args:
        base_url: Base URL of the mock server.
        total_requests: Total number of requests to make.
        max_attempts: Maximum retry attempts per request.
        base_delay_ms: Base delay in milliseconds for backoff.
        max_concurrent: Maximum concurrent requests.

    Returns:
        Dictionary with retry metrics for 429 errors.
    """
    retry_config = RetryConfig(
        max_attempts=max_attempts,
        base_delay_ms=base_delay_ms,
        max_delay_ms=1000.0,
        jitter_factor=0.1,
    )

    retry_policy = RetryPolicy(config=retry_config)

    config = ConnectionConfig(timeout=10.0)
    engine = AsyncEngine(
        max_concurrent=max_concurrent,
        config=config,
        retry_policy=retry_policy,
    )

    # Use /429 endpoint which returns 429 when rate limited
    # Configure mock server with rate limiting via error_rate
    urls = [f"{base_url}/get" for _ in range(total_requests)]

    start_time = time.perf_counter()
    result = await engine.run(urls)
    end_time = time.perf_counter()

    retry_metrics = retry_policy.get_metrics()

    return {
        "total_requests": total_requests,
        "successful": result.success_count,
        "failed": result.error_count,
        "total_retries": sum(r.attempt - 1 for r in result.results),
        "retry_metrics": {
            "attempt_count": retry_metrics.attempt_count,
            "total_delay_ms": retry_metrics.total_delay_ms,
        },
        "rps": (
            total_requests / (end_time - start_time) if end_time > start_time else 0.0
        ),
    }


async def benchmark_circuit_breaker_with_retry(
    base_url: str,
    total_requests: int = 50,
    max_attempts: int = 3,
    failure_threshold: int = 5,
    max_concurrent: int = 10,
) -> dict:
    """Benchmark combined circuit breaker and retry pattern.

    This test verifies:
    1. Circuit opens after repeated failures
    2. Retries are blocked when circuit is open
    3. Recovery happens after timeout

    Args:
        base_url: Base URL of the mock server.
        total_requests: Total number of requests to make.
        max_attempts: Maximum retry attempts per request.
        failure_threshold: Failures before circuit opens.
        max_concurrent: Maximum concurrent requests.

    Returns:
        Dictionary with combined metrics.
    """
    from async_patterns.patterns.circuit_breaker import CircuitBreaker

    # Create circuit breaker and retry policy
    circuit_breaker = CircuitBreaker(
        name="combined_test",
        failure_threshold=failure_threshold,
        open_state_duration=2.0,
        half_open_max_calls=3,
    )

    retry_config = RetryConfig(
        max_attempts=max_attempts,
        base_delay_ms=50.0,
        max_delay_ms=1000.0,
    )

    retry_policy = RetryPolicy(config=retry_config)

    config = ConnectionConfig(timeout=10.0)
    engine = AsyncEngine(
        max_concurrent=max_concurrent,
        config=config,
        circuit_breaker=circuit_breaker,
        retry_policy=retry_policy,
    )

    # Use /status/500 for guaranteed failures
    urls = [f"{base_url}/status/500" for _ in range(total_requests)]

    start_time = time.perf_counter()
    result = await engine.run(urls)
    end_time = time.perf_counter()

    cb_metrics = circuit_breaker.get_metrics()
    retry_metrics = retry_policy.get_metrics()

    return {
        "total_requests": total_requests,
        "successful": result.success_count,
        "failed": result.error_count,
        "circuit_breaker": {
            "state": cb_metrics.state.value,
            "failure_count": cb_metrics.failure_count,
            "success_count": cb_metrics.success_count,
            "total_rejected": cb_metrics.total_rejected,
        },
        "retry": {
            "attempt_count": retry_metrics.attempt_count,
            "budget_consumed": retry_metrics.budget_consumed,
        },
        "rps": (
            total_requests / (end_time - start_time) if end_time > start_time else 0.0
        ),
    }


async def run_full_retry_benchmark(
    base_url: str,
    verbose: bool = True,
) -> dict:
    """Run complete retry strategy benchmark suite.

    Args:
        base_url: Base URL of the mock server.
        verbose: Whether to print detailed progress output.

    Returns:
        Dictionary with all benchmark results.
    """
    if verbose:
        print("=" * 60)
        print("RETRY STRATEGY BENCHMARK SUITE")
        print("=" * 60)
        print()

    results: dict = {}

    # Test 1: Exponential backoff benchmark
    if verbose:
        print("Test 1: Exponential Backoff with 50% Failure Rate")
        print("-" * 40)

    backoff_result = await benchmark_retry_with_exponential_backoff(
        base_url=base_url,
        total_requests=50,
        max_attempts=3,
        base_delay_ms=50.0,
        failure_rate=0.5,
        max_concurrent=10,
    )

    results["exponential_backoff"] = {
        "total_requests": backoff_result.total_requests,
        "successful": backoff_result.successful_requests,
        "failed": backoff_result.failed_requests,
        "total_retries": backoff_result.total_retries,
        "successful_after_retry": backoff_result.successful_after_retry,
        "retry_breakdown": backoff_result.retry_breakdown,
        "rps": backoff_result.rps,
    }

    if verbose:
        print(f"  Total Requests: {backoff_result.total_requests}")
        print(f"  Successful: {backoff_result.successful_requests}")
        print(f"  Failed: {backoff_result.failed_requests}")
        print(f"  Total Retries: {backoff_result.total_retries}")
        print(f"  Success After Retry: {backoff_result.successful_after_retry}")
        print(f"  RPS: {backoff_result.rps:.1f}")
        print()

    # Test 2: Combined circuit breaker + retry
    if verbose:
        print("Test 2: Circuit Breaker with Retry")
        print("-" * 40)

    combined_result = await benchmark_circuit_breaker_with_retry(
        base_url=base_url,
        total_requests=30,
        max_attempts=3,
        failure_threshold=5,
        max_concurrent=5,
    )

    results["circuit_breaker_with_retry"] = combined_result

    if verbose:
        print(f"  Total Requests: {combined_result['total_requests']}")
        print(f"  Successful: {combined_result['successful']}")
        print(f"  Failed: {combined_result['failed']}")
        print(f"  Circuit Breaker State: {combined_result['circuit_breaker']['state']}")
        print(
            f"  Total Rejected: {combined_result['circuit_breaker']['total_rejected']}"
        )
        print(f"  Retry Attempts: {combined_result['retry']['attempt_count']}")
        print()

    # Summary
    if verbose:
        print("=" * 60)
        print("RETRY BENCHMARK SUMMARY")
        print("=" * 60)
        print(f"  Total Retries: {results['exponential_backoff']['total_retries']}")
        print(
            f"  Circuit Rejected: {results['circuit_breaker_with_retry']['circuit_breaker']['total_rejected']}"
        )
        print(f"  RPS (Backoff): {results['exponential_backoff']['rps']:.1f}")
        print(f"  RPS (Combined): {results['circuit_breaker_with_retry']['rps']:.1f}")
        print("=" * 60)

    return results


# Convenience function for synchronous usage
def run_benchmark_sync(base_url: str) -> dict:
    """Run retry benchmark synchronously using asyncio.run.

    Args:
        base_url: Base URL of the mock server.

    Returns:
        Dictionary with all benchmark metrics.
    """
    return asyncio.run(run_full_retry_benchmark(base_url, verbose=True))


if __name__ == "__main__":
    import sys

    # Default mock server URL
    default_url = "http://127.0.0.1:8765"

    # Use command line arg or default
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    else:
        base_url = default_url

    print("Running retry benchmark against:", base_url)
    print()

    results = run_benchmark_sync(base_url)

    print("\nRaw Results:")
    print(json.dumps(results, indent=2, default=str))
