"""Pooling Benchmark Scenarios for Connection Pooling Performance Testing.

This module provides benchmark functions to measure and compare the performance
of NAIVE (new client per request) vs OPTIMIZED (shared client with pooling)
connection pooling strategies.

Functions:
    benchmark_naive: Measures time for new client per request.
    benchmark_optimized: Measures time for shared client with connection limits.
    compare_strategies: Compares both strategies and returns performance metrics.

Usage:
    results = compare_strategies(["https://httpbin.org/get"] * 100)
    print(f"Improvement: {results['improvement_factor']:.2f}x")
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy
from async_patterns.engine.models import ConnectionConfig

if TYPE_CHECKING:
    pass


async def benchmark_naive(urls: list[str]) -> float:
    """Measure total time for NAIVE strategy (new client per request).

    The NAIVE strategy creates a new httpx.AsyncClient for each request,
    which has higher overhead but provides complete isolation between requests.

    Args:
        urls: List of URLs to benchmark.

    Returns:
        Total time in seconds for all requests to complete.

    Note:
        This is intentionally slower due to client creation overhead per request.
    """
    if not urls:
        return 0.0

    engine = AsyncEngine(
        max_concurrent=min(len(urls), 100),
        config=ConnectionConfig(),
        pooling_strategy=PoolingStrategy.NAIVE,
    )

    start_time = time.perf_counter()
    await engine.run(urls)
    total_time = time.perf_counter() - start_time

    return total_time


async def benchmark_optimized(urls: list[str]) -> float:
    """Measure total time for OPTIMIZED strategy (shared client with pooling).

    The OPTIMIZED strategy uses a single shared httpx.AsyncClient with connection
    pooling, significantly reducing overhead from repeated client creation.

    Args:
        urls: List of URLs to benchmark.

    Returns:
        Total time in seconds for all requests to complete.

    Note:
        This is typically 3-5x faster than NAIVE due to connection reuse.
    """
    if not urls:
        return 0.0

    engine = AsyncEngine(
        max_concurrent=min(len(urls), 100),
        config=ConnectionConfig(),
        pooling_strategy=PoolingStrategy.OPTIMIZED,
    )

    start_time = time.perf_counter()
    await engine.run(urls)
    total_time = time.perf_counter() - start_time

    return total_time


async def compare_strategies(urls: list[str]) -> dict[str, float]:
    """Compare NAIVE vs OPTIMIZED pooling strategies.

    Benchmarks both strategies and returns performance metrics showing the
    improvement factor from using connection pooling.

    Args:
        urls: List of URLs to benchmark.

    Returns:
        Dictionary containing:
        - naive_rps: Requests per second for NAIVE strategy
        - optimized_rps: Requests per second for OPTIMIZED strategy
        - improvement_factor: Ratio of optimized_rps to naive_rps
        - naive_time_sec: Total time for NAIVE strategy
        - optimized_time_sec: Total time for OPTIMIZED strategy

    Example:
        >>> results = await compare_strategies(["https://example.com"] * 50)
        >>> print(f"Speedup: {results['improvement_factor']:.2f}x")
    """
    if not urls:
        return {
            "naive_rps": 0.0,
            "optimized_rps": 0.0,
            "improvement_factor": 0.0,
            "naive_time_sec": 0.0,
            "optimized_time_sec": 0.0,
        }

    num_urls = len(urls)

    # Benchmark NAIVE strategy
    naive_time = await benchmark_naive(urls)
    naive_rps = num_urls / naive_time if naive_time > 0 else 0.0

    # Benchmark OPTIMIZED strategy
    optimized_time = await benchmark_optimized(urls)
    optimized_rps = num_urls / optimized_time if optimized_time > 0 else 0.0

    # Calculate improvement factor
    improvement_factor = optimized_rps / naive_rps if naive_rps > 0 else 0.0

    return {
        "naive_rps": naive_rps,
        "optimized_rps": optimized_rps,
        "improvement_factor": improvement_factor,
        "naive_time_sec": naive_time,
        "optimized_time_sec": optimized_time,
    }


async def run_full_benchmark(urls: list[str], verbose: bool = True) -> dict[str, float]:
    """Run full pooling benchmark with detailed output.

    Args:
        urls: List of URLs to benchmark.
        verbose: Whether to print detailed progress output.

    Returns:
        Dictionary with all benchmark metrics.
    """
    if verbose:
        print(f"Running pooling benchmark with {len(urls)} URLs...")
        print("-" * 40)

    results = await compare_strategies(urls)

    if verbose:
        print(
            f"NAIVE strategy:     {results['naive_time_sec']:.3f}s ({results['naive_rps']:.1f} RPS)"
        )
        print(
            f"OPTIMIZED strategy: {results['optimized_time_sec']:.3f}s ({results['optimized_rps']:.1f} RPS)"
        )
        print(f"Improvement factor: {results['improvement_factor']:.2f}x")
        print("-" * 40)

    return results


# Convenience function for synchronous usage
def run_benchmark_sync(urls: list[str]) -> dict[str, float]:
    """Run benchmark synchronously using asyncio.run.

    Args:
        urls: List of URLs to benchmark.

    Returns:
        Dictionary with all benchmark metrics.
    """
    return asyncio.run(compare_strategies(urls))


if __name__ == "__main__":
    import asyncio
    import sys

    from benchmarks.mock_server import MockServer, MockServerConfig

    async def main() -> None:
        # Start mock server
        config = MockServerConfig(
            host="127.0.0.1",
            port=8766,
            base_latency_ms=5.0,
            jitter_seed=42,
        )
        server = MockServer(config)
        await server.start()

        try:
            # Generate mock URLs
            base_url = server.base_url
            endpoints = [
                "/get",
                "/status/200",
                "/status/201",
                "/status/202",
                "/status/204",
            ]

            # Use command line args or defaults
            if len(sys.argv) > 1:
                count = int(sys.argv[1])
            else:
                count = 50

            urls = [f"{base_url}{endpoints[i % len(endpoints)]}" for i in range(count)]

            print("=" * 50)
            print("CONNECTION POOLING BENCHMARK")
            print("=" * 50)
            results = await compare_strategies(urls)

            print("\nResults Summary:")
            print(f"  URLs tested:     {len(urls)}")
            print(f"  NAIVE RPS:       {results['naive_rps']:.1f}")
            print(f"  OPTIMIZED RPS:   {results['optimized_rps']:.1f}")
            print(f"  Improvement:     {results['improvement_factor']:.2f}x")
            print("=" * 50)
        finally:
            await server.stop()

    asyncio.run(main())
