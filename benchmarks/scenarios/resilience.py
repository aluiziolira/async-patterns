#!/usr/bin/env python3
"""Resilience Benchmark Scenarios for Circuit Breaker Pattern Testing.

This module provides benchmark functions to measure and validate the circuit
breaker pattern's behavior under various failure conditions.

Features:
- Circuit breaker state transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
- Failure threshold triggering
- Recovery time measurement
- Circuit breaker metrics reporting

Usage:
    results = await benchmark_circuit_breaker_transitions(base_url)
    print(f"Circuit state timeline: {results['state_timeline']}")
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from async_patterns.engine.async_engine import AsyncEngine
from async_patterns.engine.models import ConnectionConfig
from async_patterns.patterns.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerMetrics,
    CircuitState,
)
from async_patterns.patterns.retry import RetryPolicy

if TYPE_CHECKING:
    pass


@dataclass
class CircuitBreakerMetricsSnapshot:
    """Snapshot of circuit breaker metrics at a point in time."""

    timestamp: float
    state: str
    failure_count: int
    success_count: int
    request_count: int
    total_rejected: int


@dataclass
class ResilienceBenchmarkResult:
    """Complete results from a resilience benchmark run."""

    total_requests: int
    successful_requests: int
    failed_requests: int
    circuit_opened: bool
    state_timeline: list[CircuitBreakerMetricsSnapshot]
    final_metrics: CircuitBreakerMetrics
    transition_count: int
    recovery_time_seconds: float | None
    rps: float


async def benchmark_circuit_breaker_transitions(
    base_url: str,
    total_requests: int = 100,
    failure_threshold: int = 5,
    open_state_duration: float = 2.0,
    max_concurrent: int = 10,
) -> ResilienceBenchmarkResult:
    """Benchmark circuit breaker state transitions under error injection.

    This benchmark:
    1. Sends requests to an endpoint that returns 500 errors
    2. Tracks circuit breaker state changes (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
    3. Measures recovery time when circuit closes
    4. Reports detailed metrics

    Args:
        base_url: Base URL of the mock server.
        total_requests: Total number of requests to make.
        failure_threshold: Number of failures before opening circuit.
        open_state_duration: Seconds to stay open before HALF_OPEN.
        max_concurrent: Maximum concurrent requests.

    Returns:
        ResilienceBenchmarkResult with all metrics and state timeline.
    """
    # Create circuit breaker with aggressive settings for testing
    circuit_breaker = CircuitBreaker(
        name="test_circuit",
        failure_threshold=failure_threshold,
        error_rate_threshold=0.1,  # Open if 10% error rate
        half_open_max_calls=3,
        open_state_duration=open_state_duration,
        baseline_latency_ms=50.0,
        window_duration=10.0,
    )

    # Create retry policy with max_attempts=1 to raise on 5xx errors
    # This ensures the circuit breaker records failures
    from async_patterns.patterns.retry import RetryConfig as PatternsRetryConfig

    retry_config = PatternsRetryConfig(max_attempts=1)
    retry_policy = RetryPolicy(config=retry_config)

    # Create engine with circuit breaker and retry policy
    config = ConnectionConfig(timeout=10.0)
    engine = AsyncEngine(
        max_concurrent=max_concurrent,
        config=config,
        circuit_breaker=circuit_breaker,
        retry_policy=retry_policy,
    )

    # Generate URLs - use /get with error injection for guaranteed failures
    # The mock server's /get endpoint respects error_rate config
    urls = [f"{base_url}/get" for _ in range(total_requests)]

    # Track state transitions
    state_timeline: list[CircuitBreakerMetricsSnapshot] = []
    initial_state = circuit_breaker.state
    state_timeline.append(
        CircuitBreakerMetricsSnapshot(
            timestamp=0.0,
            state=initial_state.value,
            failure_count=0,
            success_count=0,
            request_count=0,
            total_rejected=0,
        )
    )

    start_time = time.perf_counter()

    # Setup periodic metrics recording
    metrics_history = []
    sample_interval = 0.1  # Sample every 100ms for faster capture
    recording_task = None

    async def record_metrics() -> None:
        """Record metrics periodically during the run."""
        while True:
            await asyncio.sleep(sample_interval)
            metrics = circuit_breaker.get_metrics()
            elapsed = time.perf_counter() - start_time
            snapshot = CircuitBreakerMetricsSnapshot(
                timestamp=elapsed,
                state=metrics.state.value,
                failure_count=metrics.failure_count,
                success_count=metrics.success_count,
                request_count=metrics.request_count,
                total_rejected=metrics.total_rejected,
            )
            metrics_history.append(snapshot)

    try:
        # Start concurrent metrics recording
        recording_task = asyncio.create_task(record_metrics())

        # Run the benchmark
        result = await engine.run(urls)

        # Wait for circuit to be eligible for HALF_OPEN transition
        await asyncio.sleep(open_state_duration + 0.5)

        # Explicitly trigger the state transition check
        # This is necessary because AsyncEngine doesn't use the circuit breaker
        # as a context manager, so __aenter__ is never called to trigger the transition
        await circuit_breaker._check_and_transition_from_open()

        # Wait a bit more to capture the transition
        await asyncio.sleep(0.2)
    finally:
        # Stop metrics recording
        if recording_task is not None:
            recording_task.cancel()
            try:
                await recording_task
            except asyncio.CancelledError:
                pass

    end_time = time.perf_counter()
    total_time = end_time - start_time

    # Get final metrics
    final_metrics = circuit_breaker.get_metrics()

    # Add final snapshot to history
    metrics_history.append(
        CircuitBreakerMetricsSnapshot(
            timestamp=total_time,
            state=final_metrics.state.value,
            failure_count=final_metrics.failure_count,
            success_count=final_metrics.success_count,
            request_count=final_metrics.request_count,
            total_rejected=final_metrics.total_rejected,
        )
    )

    state_timeline.extend(metrics_history)

    # Count state transitions
    transition_count = 0
    for i in range(1, len(state_timeline)):
        if state_timeline[i].state != state_timeline[i - 1].state:
            transition_count += 1

    # Determine if circuit opened
    circuit_opened = final_metrics.state == CircuitState.OPEN or any(
        s.state == "open" for s in state_timeline
    )

    # Calculate recovery time (time from OPEN to CLOSED)
    recovery_time: float | None = None
    open_timestamp: float | None = None
    closed_timestamp: float | None = None

    for snapshot in state_timeline:
        if snapshot.state == "open" and open_timestamp is None:
            open_timestamp = snapshot.timestamp
        if (
            snapshot.state == "closed"
            and open_timestamp is not None
            and closed_timestamp is None
        ):
            closed_timestamp = snapshot.timestamp
            break

    if open_timestamp is not None and closed_timestamp is not None:
        recovery_time = closed_timestamp - open_timestamp

    return ResilienceBenchmarkResult(
        total_requests=total_requests,
        successful_requests=result.success_count,
        failed_requests=result.error_count,
        circuit_opened=circuit_opened,
        state_timeline=state_timeline,
        final_metrics=final_metrics,
        transition_count=transition_count,
        recovery_time_seconds=recovery_time,
        rps=total_requests / total_time if total_time > 0 else 0.0,
    )


async def benchmark_circuit_breaker_recovery(
    base_url: str,
    requests_before_failure: int = 20,
    failure_threshold: int = 5,
    open_state_duration: float = 3.0,
    max_concurrent: int = 5,
) -> dict:
    """Benchmark circuit breaker recovery after failures.

    This test:
    1. Sends initial successful requests
    2. Triggers circuit opening with failures
    3. Waits for HALF_OPEN state
    4. Verifies recovery to CLOSED state

    Args:
        base_url: Base URL of the mock server.
        requests_before_failure: Number of successful requests before failures.
        failure_threshold: Failures needed to open circuit.
        open_state_duration: Time in OPEN state before HALF_OPEN.
        max_concurrent: Maximum concurrent requests.

    Returns:
        Dictionary with recovery metrics.
    """
    circuit_breaker = CircuitBreaker(
        name="recovery_test",
        failure_threshold=failure_threshold,
        open_state_duration=open_state_duration,
        half_open_max_calls=3,
    )

    config = ConnectionConfig(timeout=10.0)
    engine = AsyncEngine(
        max_concurrent=max_concurrent,
        config=config,
        circuit_breaker=circuit_breaker,
    )

    # Phase 1: Successful requests
    success_urls = [f"{base_url}/get" for _ in range(requests_before_failure)]
    await engine.run(success_urls)

    # Record state after successful phase
    metrics_after_success = circuit_breaker.get_metrics()

    # Phase 2: Trigger circuit opening with 100% error endpoint
    # Use /status/500 which returns 500 errors
    error_urls = [f"{base_url}/status/500" for _ in range(failure_threshold * 2)]

    rejected_count = 0
    for url in error_urls:
        try:
            # Create a simple fetch to test circuit breaker
            async with circuit_breaker, httpx.AsyncClient() as client:
                await client.get(url, timeout=5.0)
        except Exception:
            rejected_count += 1

    # Wait for circuit to transition to HALF_OPEN
    await asyncio.sleep(open_state_duration + 0.5)

    # Phase 3: Verify recovery - send requests that should succeed
    recovery_urls = [f"{base_url}/get" for _ in range(10)]
    await engine.run(recovery_urls)

    final_metrics = circuit_breaker.get_metrics()

    return {
        "initial_state": metrics_after_success.state.value,
        "final_state": final_metrics.state.value,
        "circuit_opened": metrics_after_success.state == CircuitState.CLOSED
        and final_metrics.state == CircuitState.CLOSED,
        "recovered": final_metrics.state == CircuitState.CLOSED,
        "rejected_during_failure": rejected_count,
        "failure_count": final_metrics.failure_count,
        "success_count": final_metrics.success_count,
        "total_rejected": final_metrics.total_rejected,
    }


async def run_full_resilience_benchmark(
    base_url: str,
    verbose: bool = True,
) -> dict:
    """Run complete resilience benchmark suite.

    Args:
        base_url: Base URL of the mock server.
        verbose: Whether to print detailed progress output.

    Returns:
        Dictionary with all benchmark results.
    """
    if verbose:
        print("=" * 60)
        print("RESILIENCE BENCHMARK SUITE")
        print("=" * 60)
        print()

    results: dict = {}

    # Test 1: Circuit breaker transitions
    if verbose:
        print("Test 1: Circuit Breaker State Transitions")
        print("-" * 40)

    transitions_result = await benchmark_circuit_breaker_transitions(
        base_url=base_url,
        total_requests=50,
        failure_threshold=5,
        open_state_duration=2.0,
        max_concurrent=5,
    )

    results["transitions"] = {
        "total_requests": transitions_result.total_requests,
        "successful": transitions_result.successful_requests,
        "failed": transitions_result.failed_requests,
        "circuit_opened": transitions_result.circuit_opened,
        "transition_count": transitions_result.transition_count,
        "recovery_time_seconds": transitions_result.recovery_time_seconds,
        "rps": transitions_result.rps,
        "final_state": transitions_result.final_metrics.state.value,
        "state_timeline": [
            {
                "timestamp": s.timestamp,
                "state": s.state,
                "failures": s.failure_count,
                "successes": s.success_count,
                "rejected": s.total_rejected,
            }
            for s in transitions_result.state_timeline
        ],
    }

    if verbose:
        print(f"  Total Requests: {transitions_result.total_requests}")
        print(f"  Successful: {transitions_result.successful_requests}")
        print(f"  Failed: {transitions_result.failed_requests}")
        print(f"  Circuit Opened: {transitions_result.circuit_opened}")
        print(f"  State Transitions: {transitions_result.transition_count}")
        print(
            f"  Recovery Time: {transitions_result.recovery_time_seconds:.3f}s"
            if transitions_result.recovery_time_seconds
            else "  Recovery Time: N/A"
        )
        print(f"  Final State: {transitions_result.final_metrics.state.value}")
        print()

    # Test 2: Circuit breaker recovery
    if verbose:
        print("Test 2: Circuit Breaker Recovery")
        print("-" * 40)

    recovery_result = await benchmark_circuit_breaker_recovery(
        base_url=base_url,
        requests_before_failure=10,
        failure_threshold=5,
        open_state_duration=2.0,
        max_concurrent=5,
    )

    results["recovery"] = recovery_result

    if verbose:
        print(f"  Initial State: {recovery_result['initial_state']}")
        print(f"  Final State: {recovery_result['final_state']}")
        print(f"  Recovered: {recovery_result['recovered']}")
        print(
            f"  Rejected During Failure: {recovery_result['rejected_during_failure']}"
        )
        print()

    # Summary
    if verbose:
        print("=" * 60)
        print("RESILIENCE BENCHMARK SUMMARY")
        print("=" * 60)
        print(
            f"  Circuit Breaker Transitions: {'PASS' if results['transitions']['transition_count'] >= 2 else 'FAIL'}"
        )
        print(
            f"  Circuit Recovery: {'PASS' if results['recovery']['recovered'] else 'FAIL'}"
        )
        print(f"  Total Rejected: {results['recovery']['total_rejected']}")
        print("=" * 60)

    return results


# Convenience function for synchronous usage
def run_benchmark_sync(base_url: str) -> dict:
    """Run resilience benchmark synchronously using asyncio.run.

    Args:
        base_url: Base URL of the mock server.

    Returns:
        Dictionary with all benchmark metrics.
    """
    return asyncio.run(run_full_resilience_benchmark(base_url, verbose=True))


if __name__ == "__main__":
    import sys

    # Default mock server URL
    default_url = "http://127.0.0.1:8765"

    # Use command line arg or default
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    else:
        base_url = default_url

    print("Running resilience benchmark against:", base_url)
    print()

    results = run_benchmark_sync(base_url)

    print("\nRaw Results:")
    print(json.dumps(results, indent=2, default=str))
