"""Integration tests for connection pooling benchmark.

This module contains benchmark tests to verify the performance improvement
of OPTIMIZED pooling strategy over NAIVE pooling strategy.

Tests use the mock server fixture for deterministic, network-free testing.
"""

from __future__ import annotations

import pytest

from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy
from async_patterns.engine.models import ConnectionConfig
from benchmarks.mock_server import MockServerFixture


@pytest.mark.asyncio
async def test_pooling_strategy_improvement(mock_server: MockServerFixture) -> None:
    """Verify OPTIMIZED pooling is ≥1.3x faster than NAIVE.

    This test benchmarks the OPTIMIZED connection pooling strategy
    against the NAIVE strategy to verify the performance improvement.

    The OPTIMIZED strategy uses a shared aiohttp.ClientSession with connection
    pooling, while the NAIVE strategy creates a new session for each request.

    Note: The 1.3x threshold reflects localhost testing limitations where
    TCP/TLS overhead is minimal. Real-world networks show 3-5x improvements
    due to 50-200ms connection establishment costs.

    Uses mock server for deterministic, fast testing without network access.
    """
    # Generate test URLs using the mock server fixture
    # Using 150 URLs to amplify session creation overhead difference
    test_urls = mock_server.urls(150, "/get")

    # Benchmark NAIVE strategy (new client per request)
    naive_engine = AsyncEngine(
        max_concurrent=10,
        config=ConnectionConfig(),
        pooling_strategy=PoolingStrategy.NAIVE,
    )
    naive_result = await naive_engine.run(test_urls)

    # Benchmark OPTIMIZED strategy (shared client with pooling)
    optimized_engine = AsyncEngine(
        max_concurrent=10,
        config=ConnectionConfig(),
        pooling_strategy=PoolingStrategy.OPTIMIZED,
    )
    optimized_result = await optimized_engine.run(test_urls)

    # Calculate speedup ratio
    speedup = naive_result.total_time / optimized_result.total_time

    # Assert minimum 1.3x speedup improvement
    # Note: Localhost testing limits observable gains. Real networks show 3-5x.
    assert speedup >= 1.3, (
        f"Expected OPTIMIZED to be ≥1.3x faster than NAIVE, "
        f"but got {speedup:.2f}x speedup. "
        f"NAIVE: {naive_result.total_time:.2f}s, "
        f"OPTIMIZED: {optimized_result.total_time:.2f}s"
    )

    # Log results for visibility
    print("\n--- Pooling Benchmark Results (Mock Server) ---")
    print(f"NAIVE:    {naive_result.total_time:.2f}s ({naive_result.rps:.2f} RPS)")
    print(f"OPTIMIZED: {optimized_result.total_time:.2f}s ({optimized_result.rps:.2f} RPS)")
    print(f"Speedup:  {speedup:.2f}x")
