#!/usr/bin/env python3
"""Benchmark Runner for Async-Patterns Performance Lab.

Compares performance of Sync vs Threaded engines and NAIVE vs OPTIMIZED
connection pooling strategies.

Usage:
    python -m benchmarks.runner [--urls FILE] [--count N] [--output FILE] [--pooling]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import subprocess
import sys
import time
import tracemalloc
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any

# uvloop integration for improved event loop performance on Linux/macOS
if sys.platform != "win32":
    try:
        import uvloop

        uvloop.install()
    except ImportError:
        pass

from async_patterns.engine import (
    AsyncEngine,
    ConnectionConfig,
    EngineResult,
    SyncEngine,
    ThreadedEngine,
)
from async_patterns.observability.loop_monitor import EventLoopMonitor
from async_patterns.persistence.jsonl import JsonlWriter, JsonlWriterConfig
from async_patterns.persistence.sqlite import SqliteWriter, SqliteWriterConfig
from benchmarks.mock_server import MockServer, MockServerConfig
from benchmarks.scenarios.pooling import compare_strategies
from benchmarks.scenarios.resilience import run_full_resilience_benchmark
from benchmarks.scenarios.retry import run_full_retry_benchmark


def get_git_sha() -> str | None:
    """Get the current git SHA if in a git repository.

    Returns:
        Git SHA string or None if not in a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


@dataclass(frozen=True, slots=True)
class BenchmarkMetadata:
    """Benchmark execution metadata for reproducibility.

    Attributes:
        timestamp: ISO 8601 timestamp of benchmark start.
        python_version: Python version string.
        platform: Platform information (OS, architecture).
        uvloop_enabled: Whether uvloop is active.
        git_sha: Git commit SHA (if available, else None).
        config: Dictionary of benchmark configuration parameters.
    """

    timestamp: str
    python_version: str
    platform: str
    uvloop_enabled: bool
    git_sha: str | None
    config: dict[str, Any]


def _percentile(sorted_data: list[float], p: float) -> float:
    """Calculate percentile using linear interpolation."""
    if not sorted_data:
        return 0.0
    n = len(sorted_data)
    k = (n - 1) * p
    f = int(k)
    c = f + 1 if f + 1 < n else f
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def calculate_stats(
    results: list[EngineResult], peak_memory_override: float | None = None
) -> dict:
    """Calculate aggregate statistics from benchmark results.

    Args:
        results: List of EngineResult objects.
        peak_memory_override: Optional override for peak memory (useful for duration mode).

    Returns:
        Dictionary with aggregate statistics.
    """
    total_time = sum(r.total_time for r in results)
    total_requests = sum(len(r.results) for r in results)
    total_success = sum(r.success_count for r in results)
    total_errors = sum(r.error_count for r in results)
    peak_memory = (
        peak_memory_override
        if peak_memory_override is not None
        else max(r.peak_memory_mb for r in results)
    )

    latencies = []
    for r in results:
        latencies.extend(result.latency_ms for result in r.results)

    sorted_latencies = sorted(latencies)

    error_breakdown: dict[str, int] = {}
    for r in results:
        for key, value in r.error_breakdown.items():
            error_breakdown[key] = error_breakdown.get(key, 0) + value

    return {
        "runs": len(results),
        "total_time_sec": total_time,
        "total_requests": total_requests,
        "successful_requests": total_success,
        "failed_requests": total_errors,
        "success_rate": total_success / total_requests if total_requests > 0 else 0,
        "peak_memory_mb": peak_memory,
        "latency_stats": {
            "min_ms": min(sorted_latencies) if sorted_latencies else 0,
            "max_ms": max(sorted_latencies) if sorted_latencies else 0,
            "mean_ms": mean(sorted_latencies) if sorted_latencies else 0,
            "median_ms": median(sorted_latencies) if sorted_latencies else 0,
            "p95_ms": _percentile(sorted_latencies, 0.95),
            "p99_ms": _percentile(sorted_latencies, 0.99),
            "std_dev_ms": stdev(sorted_latencies) if len(sorted_latencies) > 1 else 0,
        },
        "error_breakdown": error_breakdown,
    }


def run_benchmark(
    engine_name: str,
    engine: SyncEngine | ThreadedEngine,
    urls: list[str],
    runs: int = 3,
) -> dict:
    """Run benchmark for a specific engine.

    Args:
        engine_name: Name of the engine for reporting.
        engine: Engine instance to benchmark.
        urls: List of URLs to test.
        runs: Number of benchmark runs.

    Returns:
        Dictionary with benchmark results.
    """
    print(f"Running {engine_name} engine benchmark ({runs} runs)...")
    results: list[EngineResult] = []

    for i in range(runs):
        print(f"  Run {i + 1}/{runs}...", end=" ", flush=True)
        start = time.perf_counter()
        result = engine.run(urls)
        elapsed = time.perf_counter() - start
        results.append(result)
        print(f"{elapsed:.3f}s")

    stats = calculate_stats(results)

    total_requests = sum(len(r.results) for r in results)
    total_time = sum(r.total_time for r in results)
    avg_rps = total_requests / total_time if total_time > 0 else 0

    return {
        "engine": engine_name,
        "runs": runs,
        "urls_per_run": len(urls),
        "total_requests": total_requests,
        "average_time_sec": total_time / runs,
        "rps": avg_rps,
        **stats,
    }


async def run_async_benchmark_session(
    engine_name: str,
    urls: list[str],
    runs: int = 3,
    max_concurrent: int = 100,
) -> dict:
    """Run benchmark for the AsyncEngine.

    Args:
        engine_name: Name of the engine for reporting.
        urls: List of URLs to test.
        runs: Number of benchmark runs.
        max_concurrent: Max concurrent requests for AsyncEngine.

    Returns:
        Dictionary with benchmark results.
    """
    print(f"Running {engine_name} engine benchmark ({runs} runs)...")
    results: list[EngineResult] = []

    config = ConnectionConfig()
    engine = AsyncEngine(max_concurrent=max_concurrent, config=config)

    for i in range(runs):
        print(f"  Run {i + 1}/{runs}...", end=" ", flush=True)
        start = time.perf_counter()
        result = await engine.run(urls)
        elapsed = time.perf_counter() - start
        results.append(result)
        print(f"{elapsed:.3f}s")

    stats = calculate_stats(results)

    total_requests = sum(len(r.results) for r in results)
    total_time = sum(r.total_time for r in results)
    avg_rps = total_requests / total_time if total_time > 0 else 0

    return {
        "engine": engine_name,
        "runs": runs,
        "urls_per_run": len(urls),
        "total_requests": total_requests,
        "average_time_sec": total_time / runs,
        "rps": avg_rps,
        **stats,
    }


async def run_duration_benchmark(
    base_url: str,
    duration_sec: float,
    max_concurrent: int = 200,
    monitor_loop: bool = False,
    monitor_sample_interval: float = 1.0,
) -> dict:
    """Run a duration-based benchmark using the async engine.

    High-throughput benchmark measuring RPS under sustained load.

    Args:
        base_url: Base URL of the mock server.
        duration_sec: Duration of the benchmark in seconds.
        max_concurrent: Max concurrent requests for AsyncEngine.
        monitor_loop: Whether to enable event loop monitoring.
        monitor_sample_interval: Sample interval for loop monitoring.

    Returns:
        Dictionary with benchmark results including loop_health if monitoring enabled.
    """
    print(f"Running async engine benchmark for {duration_sec}s...")

    endpoints = ["/get", "/status/200", "/status/201", "/status/202", "/status/204"]
    urls = [f"{base_url}{ep}" for ep in endpoints]

    monitor: EventLoopMonitor | None = None
    if monitor_loop:
        monitor = EventLoopMonitor(
            sample_interval_seconds=monitor_sample_interval,
            log_metrics=False,
            structured_logging=False,
        )
        monitor.start()

    tracemalloc.start()

    config = ConnectionConfig()
    engine = AsyncEngine(max_concurrent=max_concurrent, config=config)

    start_time = time.perf_counter()
    all_results: list[EngineResult] = []

    try:
        while (time.perf_counter() - start_time) < duration_sec:
            result = await engine.run(urls)
            all_results.append(result)
    finally:
        _, peak = tracemalloc.get_traced_memory()
        peak_memory_mb = peak / (1024 * 1024)
        tracemalloc.stop()

        if monitor:
            monitor.stop()

    actual_duration = time.perf_counter() - start_time

    stats = calculate_stats(all_results, peak_memory_override=peak_memory_mb)

    total_requests = sum(len(r.results) for r in all_results)
    avg_rps = total_requests / actual_duration if actual_duration > 0 else 0

    result: dict[str, Any] = {
        "engine": "async",
        "mode": "duration",
        "duration_sec": actual_duration,
        "total_iterations": len(all_results),
        "total_requests": total_requests,
        "rps": avg_rps,
        **stats,
    }

    # Add loop health metrics if monitoring was enabled
    if monitor:
        history = monitor.get_history()
        if history:
            latencies_ms = [m.loop_latency_ms for m in history]
            backlogs = [m.backlog_size for m in history]
            all_warnings: list[str] = []
            for m in history:
                all_warnings.extend(m.warning_messages)

            sorted_latencies_ms = sorted(latencies_ms)
            loop_health: dict[str, Any] = {
                "max_lag_ms": max(latencies_ms) if latencies_ms else 0.0,
                "p95_lag_ms": _percentile(sorted_latencies_ms, 0.95),
                "peak_backlog": max(backlogs) if backlogs else 0,
                "samples": len(history),
                "warnings": all_warnings,
            }
            result["loop_health"] = loop_health

    return result


def format_results(
    sync_result: dict,
    threaded_result: dict,
    async_result: dict | None = None,
    benchmark_config: dict | None = None,
    metadata: BenchmarkMetadata | None = None,
) -> dict:
    """Format final benchmark results with comparison.

    Args:
        sync_result: Sync engine benchmark results.
        threaded_result: Threaded engine benchmark results.
        async_result: Async engine benchmark results (optional).
        benchmark_config: Benchmark configuration parameters.
        metadata: Benchmark metadata for reproducibility.

    Returns:
        Formatted comparison results.
    """
    speedup = (
        sync_result["average_time_sec"] / threaded_result["average_time_sec"]
        if threaded_result["average_time_sec"] > 0
        else 0
    )

    base_result: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "environment": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "uvloop_enabled": sys.platform != "win32" and "uvloop" in sys.modules,
        },
        "config": benchmark_config or {},
        "sync_engine": sync_result,
        "threaded_engine": threaded_result,
        "comparison": {
            "speedup_factor": speedup,
            "threaded_faster": threaded_result["average_time_sec"]
            < sync_result["average_time_sec"],
            "time_saved_sec": sync_result["average_time_sec"]
            - threaded_result["average_time_sec"],
            "rps_improvement": (
                threaded_result["rps"] / sync_result["rps"]
                if sync_result["rps"] > 0
                else 0
            ),
        },
    }

    if metadata:
        base_result["metadata"] = {
            "timestamp": metadata.timestamp,
            "python_version": metadata.python_version,
            "platform": metadata.platform,
            "uvloop_enabled": metadata.uvloop_enabled,
            "git_sha": metadata.git_sha,
            "config": metadata.config,
        }

    if async_result:
        async_vs_sync_speedup = (
            sync_result["average_time_sec"] / async_result["average_time_sec"]
            if async_result["average_time_sec"] > 0
            else 0
        )
        async_vs_threaded_speedup = (
            threaded_result["average_time_sec"] / async_result["average_time_sec"]
            if async_result["average_time_sec"] > 0
            else 0
        )

        base_result["async_engine"] = async_result
        base_result["comparison"]["async_vs_sync_speedup"] = async_vs_sync_speedup
        base_result["comparison"][
            "async_vs_threaded_speedup"
        ] = async_vs_threaded_speedup

    return base_result


async def run_pooling_benchmark(urls: list[str]) -> dict:
    """Run connection pooling benchmark using async engine.

    Args:
        urls: List of URLs to benchmark.

    Returns:
        Dictionary with pooling benchmark results.
    """
    return await compare_strategies(urls)


def generate_mock_urls(base_url: str, count: int) -> list[str]:
    """Generate URLs pointing to the mock server.

    Args:
        base_url: Base URL of the mock server.
        count: Number of URLs to generate.

    Returns:
        List of URLs.
    """
    endpoints = ["/get", "/status/200", "/status/201", "/status/202", "/status/204"]
    urls = []
    for i in range(count):
        endpoint = endpoints[i % len(endpoints)]
        urls.append(f"{base_url}{endpoint}")
    return urls


async def write_results_to_outputs(
    results: dict,
    jsonl_path: Path | None,
    sqlite_path: Path | None,
) -> None:
    """Write benchmark results to output files.

    Args:
        results: Benchmark results dictionary.
        jsonl_path: Path to JSONL output file, or None.
        sqlite_path: Path to SQLite database, or None.
    """
    if jsonl_path:
        async with JsonlWriter(JsonlWriterConfig(jsonl_path)) as writer:
            await writer.write(results)

    if sqlite_path:
        async with SqliteWriter(SqliteWriterConfig(sqlite_path)) as writer:
            await writer.write(results)


async def run_benchmarks_with_mock_server(
    args: argparse.Namespace,
) -> int:
    """Run all benchmarks using the mock server.

    Args:
        args: Parsed command line arguments.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    mock_config = MockServerConfig(
        host="127.0.0.1",
        port=args.mock_port,
        base_latency_ms=args.mock_latency_ms,
        jitter_seed=42,
        error_rate=args.mock_error_rate,
        rate_limit_after=args.mock_rate_limit_after,
    )
    mock_server = MockServer(mock_config)

    if args.verbose:
        print(f"Starting mock server on port {args.mock_port}...")
    await mock_server.start()

    try:
        base_url = mock_server.base_url
        if args.verbose:
            print(f"Mock server running at {base_url}")
            print(f"Mock server latency: {args.mock_latency_ms}ms")
            print()

        if args.duration_sec is not None:
            return await run_duration_benchmark_mode(args, base_url, mock_server)

        if args.run_resilience or args.run_retry:
            return await run_resilience_benchmark_mode(args, base_url, mock_server)

        # Count mode: generate URLs based on request_count
        urls = generate_mock_urls(base_url, args.request_count)
        if args.verbose:
            print(f"Generated {len(urls)} mock URLs")

        metadata = BenchmarkMetadata(
            timestamp=datetime.now(UTC).isoformat(),
            python_version=sys.version.split()[0],
            platform=platform.platform(),
            uvloop_enabled=sys.platform != "win32" and "uvloop" in sys.modules,
            git_sha=get_git_sha(),
            config={
                "runs": args.runs,
                "urls_per_run": len(urls),
                "thread_workers": args.workers,
                "async_max_concurrent": args.concurrency,
                "mock_latency_ms": args.mock_latency_ms,
            },
        )

        loop = asyncio.get_running_loop()
        sync_engine = SyncEngine()
        sync_result = await loop.run_in_executor(
            None, run_benchmark, "sync", sync_engine, urls, args.runs
        )

        print()

        threaded_engine = ThreadedEngine(max_workers=min(len(urls), args.workers))
        threaded_result = await loop.run_in_executor(
            None, run_benchmark, "threaded", threaded_engine, urls, args.runs
        )

        print()

        async_result = await run_async_benchmark_session(
            "async", urls, runs=args.runs, max_concurrent=args.concurrency
        )

        print()

        pooling_result = None
        if args.pooling:
            print("Running pooling comparison benchmark...")
            pooling_result = await run_pooling_benchmark(urls)
            print()

        benchmark_config = {
            "runs": args.runs,
            "urls_per_run": len(urls),
            "thread_workers": args.workers,
            "async_max_concurrent": args.concurrency,
            "pooling_benchmark": args.pooling,
            "source": "mock_server",
            "mock_latency_ms": args.mock_latency_ms,
        }

        results = format_results(
            sync_result,
            threaded_result,
            async_result,
            benchmark_config=benchmark_config,
            metadata=metadata,
        )

        jsonl_path = Path(args.jsonl_out) if args.jsonl_out else None
        sqlite_path = Path(args.sqlite_out) if args.sqlite_out else None
        await write_results_to_outputs(results, jsonl_path, sqlite_path)

        output_path = Path(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        print("=" * 50)
        print("BENCHMARK RESULTS (Mock Server)")
        print("=" * 50)
        print(
            f"Sync Engine:   {sync_result['average_time_sec']:.3f}s ({sync_result['rps']:.1f} RPS)"
        )
        print(
            f"Threaded Engine: {threaded_result['average_time_sec']:.3f}s ({threaded_result['rps']:.1f} RPS)"
        )
        print(f"Speedup Factor: {results['comparison']['speedup_factor']:.2f}x")

        print(
            f"Async Engine:    {async_result['average_time_sec']:.3f}s ({async_result['rps']:.1f} RPS)"
        )
        print(
            f"Async vs Sync Speedup:     {results['comparison']['async_vs_sync_speedup']:.2f}x"
        )
        print(
            f"Async vs Threaded Speedup: {results['comparison']['async_vs_threaded_speedup']:.2f}x"
        )

        if pooling_result:
            results["pooling_comparison"] = pooling_result
            print()
            print("-" * 50)
            print("POOLING COMPARISON (Async Engine)")
            print("-" * 50)
            print(
                f"NAIVE strategy:     {pooling_result['naive_time_sec']:.3f}s "
                f"({pooling_result['naive_rps']:.1f} RPS)"
            )
            print(
                f"OPTIMIZED strategy: {pooling_result['optimized_time_sec']:.3f}s "
                f"({pooling_result['optimized_rps']:.1f} RPS)"
            )
            print(f"Improvement Factor: {pooling_result['improvement_factor']:.2f}x")

        print(f"Results saved to: {output_path}")
        if jsonl_path:
            print(f"JSONL output: {jsonl_path}")
        if sqlite_path:
            print(f"SQLite output: {sqlite_path}")
        print("=" * 50)

        return 0

    finally:
        await mock_server.stop()


async def run_duration_benchmark_mode(
    args: argparse.Namespace,
    base_url: str,
    mock_server: MockServer,
) -> int:
    """Run duration-based benchmark using the async engine.

    Args:
        args: Parsed command line arguments.
        base_url: Base URL of the mock server.
        mock_server: Mock server instance.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    duration_sec = args.duration_sec
    max_concurrent = args.concurrency

    if args.verbose:
        print(f"Running duration-based benchmark for {duration_sec}s")
        print(f"Async concurrency: {max_concurrent}")
        print()

    metadata = BenchmarkMetadata(
        timestamp=datetime.now(UTC).isoformat(),
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        uvloop_enabled=sys.platform != "win32" and "uvloop" in sys.modules,
        git_sha=get_git_sha(),
        config={
            "mode": "duration",
            "duration_sec": duration_sec,
            "async_max_concurrent": max_concurrent,
            "mock_latency_ms": args.mock_latency_ms,
        },
    )

    async_result = await run_duration_benchmark(
        base_url,
        duration_sec,
        max_concurrent=max_concurrent,
        monitor_loop=args.monitor_loop,
        monitor_sample_interval=args.monitor_sample_interval,
    )

    results: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "environment": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "uvloop_enabled": sys.platform != "win32" and "uvloop" in sys.modules,
        },
        "config": {
            "mode": "duration",
            "duration_sec": duration_sec,
            "async_max_concurrent": max_concurrent,
            "source": "mock_server",
            "mock_latency_ms": args.mock_latency_ms,
        },
        "async_engine": async_result,
        "metadata": {
            "timestamp": metadata.timestamp,
            "python_version": metadata.python_version,
            "platform": metadata.platform,
            "uvloop_enabled": metadata.uvloop_enabled,
            "git_sha": metadata.git_sha,
            "config": metadata.config,
        },
    }

    jsonl_path = Path(args.jsonl_out) if args.jsonl_out else None
    sqlite_path = Path(args.sqlite_out) if args.sqlite_out else None
    await write_results_to_outputs(results, jsonl_path, sqlite_path)

    output_path = Path(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("=" * 50)
    print("DURATION BENCHMARK RESULTS (Mock Server)")
    print("=" * 50)
    print(f"Duration:       {async_result['duration_sec']:.3f}s")
    print(f"Total Requests: {async_result['total_requests']}")
    print(f"Throughput:     {async_result['rps']:.1f} RPS")
    print()
    print("Latency Statistics (ms):")
    latency_stats = async_result.get("latency_stats", {})
    print(f"  Mean:   {latency_stats.get('mean_ms', 0):.2f}")
    print(f"  Median: {latency_stats.get('median_ms', 0):.2f}")
    print(f"  P95:    {latency_stats.get('p95_ms', 0):.2f}")
    print(f"  P99:    {latency_stats.get('p99_ms', 0):.2f}")
    print()
    print(f"Peak Memory:    {async_result.get('peak_memory_mb', 0):.2f} MB")
    print(f"Results saved to: {output_path}")
    if jsonl_path:
        print(f"JSONL output: {jsonl_path}")
    if sqlite_path:
        print(f"SQLite output: {sqlite_path}")
    print("=" * 50)

    return 0


async def run_resilience_benchmark_mode(
    args: argparse.Namespace,
    base_url: str,
    mock_server: MockServer,
) -> int:
    """Run resilience/retry benchmarks using the mock server.

    Args:
        args: Parsed command line arguments.
        base_url: Base URL of the mock server.
        mock_server: Mock server instance.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    results: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "environment": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "uvloop_enabled": sys.platform != "win32" and "uvloop" in sys.modules,
        },
        "config": {
            "mock_error_rate": args.mock_error_rate,
            "mock_rate_limit_after": args.mock_rate_limit_after,
        },
    }

    if args.run_resilience:
        print("Running resilience benchmark...")
        resilience_results = await run_full_resilience_benchmark(base_url, verbose=True)
        results["resilience_benchmark"] = resilience_results
        print()

    if args.run_retry:
        print("Running retry strategy benchmark...")
        retry_results = await run_full_retry_benchmark(base_url, verbose=True)
        results["retry_benchmark"] = retry_results
        print()

    output_path = Path(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("=" * 60)
    print("RESILIENCE/RETRY BENCHMARK RESULTS")
    print("=" * 60)

    if args.run_resilience:
        print("\nCircuit Breaker Metrics:")
        if "transitions" in results.get("resilience_benchmark", {}):
            trans = results["resilience_benchmark"]["transitions"]
            print(f"  State Transitions: {trans.get('transition_count', 0)}")
            print(f"  Circuit Opened: {trans.get('circuit_opened', False)}")
            print(f"  Final State: {trans.get('final_state', 'unknown')}")

    if args.run_retry:
        print("\nRetry Metrics:")
        if "exponential_backoff" in results.get("retry_benchmark", {}):
            backoff = results["retry_benchmark"]["exponential_backoff"]
            print(f"  Total Retries: {backoff.get('total_retries', 0)}")
            print(f"  Success After Retry: {backoff.get('successful_after_retry', 0)}")

    print(f"\nResults saved to: {output_path}")
    print("=" * 60)

    return 0


def main() -> int:
    """Main entry point for benchmark runner.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = argparse.ArgumentParser(
        description="Benchmark runner for async-patterns performance comparison"
    )
    parser.add_argument(
        "--duration-sec",
        type=float,
        default=None,
        help="Duration of benchmark in seconds (uses count mode if not specified)",
    )
    parser.add_argument(
        "--request-count",
        type=int,
        default=100,
        help="Number of URL requests in count mode (default: 100)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of benchmark runs per engine (default: 3)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=100,
        help="Max concurrent requests for async engine (default: 100)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Max worker threads for threaded engine (default: 10)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="benchmark_output.json",
        help="Output JSON file (default: benchmark_output.json)",
    )
    parser.add_argument(
        "--pooling",
        action="store_true",
        help="Run connection pooling comparison (NAIVE vs OPTIMIZED)",
    )
    parser.add_argument(
        "--mock-port",
        type=int,
        default=8765,
        help="Port for mock server (default: 8765)",
    )
    parser.add_argument(
        "--mock-latency-ms",
        type=float,
        default=5.0,
        help="Base latency in ms for mock server (default: 5)",
    )
    parser.add_argument(
        "--jsonl-out",
        type=str,
        default=None,
        help="Write results to JSONL file at specified path",
    )
    parser.add_argument(
        "--sqlite-out",
        type=str,
        default=None,
        help="Write results to SQLite database at specified path",
    )
    parser.add_argument(
        "--monitor-loop",
        action="store_true",
        help="Enable event loop monitoring for async benchmarks",
    )
    parser.add_argument(
        "--monitor-sample-interval",
        type=float,
        default=1.0,
        help="Sample interval for event loop monitoring in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--mock-error-rate",
        type=float,
        default=0.0,
        help="Error injection rate for mock server (0-1, default: 0.0)",
    )
    parser.add_argument(
        "--mock-rate-limit-after",
        type=int,
        default=None,
        help="Number of requests before returning 429 (default: None)",
    )
    parser.add_argument(
        "--run-resilience",
        action="store_true",
        help="Run resilience benchmark with circuit breaker pattern",
    )
    parser.add_argument(
        "--run-retry",
        action="store_true",
        help="Run retry strategy benchmark with exponential backoff",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Always use mock server
    try:
        return asyncio.run(run_benchmarks_with_mock_server(args))
    except Exception as e:
        print(f"Error running benchmark: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
