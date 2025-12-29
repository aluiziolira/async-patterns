#!/usr/bin/env python3
"""Benchmark Runner for Async-Patterns Performance Lab.

This script compares the performance of sync and threaded engines
and outputs results in JSON format for easy processing.

Usage:
    python -m benchmarks.runner [--urls FILE] [--count N] [--output FILE]

Options:
    --urls FILE     JSON file containing list of URLs (default: built-in test URLs)
    --count N       Number of URLs to test (default: 10)
    --output FILE   Output JSON file (default: benchmark_output.json)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

# uvloop integration for 2-4x event loop performance on Linux/macOS
# Windows is not supported by uvloop, so we gracefully fall back to default loop
if sys.platform != "win32":
    try:
        import uvloop

        uvloop.install()
        # Event loop is now using uvloop for improved async performance
    except ImportError:
        # uvloop not installed, use default asyncio event loop
        # Install with: pip install uvloop or poetry install --extras performance
        pass

from async_patterns.engine import EngineResult, SyncEngine, ThreadedEngine

# Default test URLs for benchmarking
DEFAULT_URLS = [
    "https://httpbin.org/get",
    "https://httpbin.org/headers",
    "https://httpbin.org/ip",
    "https://httpbin.org/user-agent",
    "https://httpbin.org/anything",
    "https://httpbin.org/bytes/1024",
    "https://httpbin.org/delay/1",
    "https://httpbin.org/redirect-to?url=https://httpbin.org/get",
    "https://httpbin.org/status/200",
    "https://httpbin.org/status/404",
]


def load_urls_from_file(filepath: str) -> list[str]:
    """Load URLs from a JSON file.

    Args:
        filepath: Path to JSON file containing URLs.

    Returns:
        List of URL strings.
    """
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
        return data.get("urls", data if isinstance(data, list) else [])


def calculate_stats(results: list[EngineResult]) -> dict:
    """Calculate aggregate statistics from benchmark results.

    Args:
        results: List of EngineResult objects.

    Returns:
        Dictionary with aggregate statistics.
    """
    total_time = sum(r.total_time for r in results)
    total_requests = sum(len(r.results) for r in results)
    total_success = sum(r.success_count for r in results)
    total_errors = sum(r.error_count for r in results)
    peak_memory = max(r.peak_memory_mb for r in results)

    latencies = []
    for r in results:
        latencies.extend(result.latency_ms for result in r.results)

    return {
        "runs": len(results),
        "total_time_sec": total_time,
        "total_requests": total_requests,
        "successful_requests": total_success,
        "failed_requests": total_errors,
        "success_rate": total_success / total_requests if total_requests > 0 else 0,
        "peak_memory_mb": peak_memory,
        "latency_stats": {
            "min_ms": min(latencies) if latencies else 0,
            "max_ms": max(latencies) if latencies else 0,
            "mean_ms": mean(latencies) if latencies else 0,
            "std_dev_ms": stdev(latencies) if len(latencies) > 1 else 0,
        },
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

    # Calculate aggregate stats
    stats = calculate_stats(results)

    # Calculate RPS
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


def format_results(sync_result: dict, threaded_result: dict) -> dict:
    """Format final benchmark results with comparison.

    Args:
        sync_result: Sync engine benchmark results.
        threaded_result: Threaded engine benchmark results.

    Returns:
        Formatted comparison results.
    """
    speedup = (
        sync_result["average_time_sec"] / threaded_result["average_time_sec"]
        if threaded_result["average_time_sec"] > 0
        else 0
    )

    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
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


def main() -> int:
    """Main entry point for benchmark runner.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = argparse.ArgumentParser(
        description="Benchmark runner for async-patterns performance comparison"
    )
    parser.add_argument(
        "--urls",
        type=str,
        help="JSON file containing list of URLs",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of URLs to test (default: 10)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of benchmark runs per engine (default: 3)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="benchmark_output.json",
        help="Output JSON file (default: benchmark_output.json)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Load URLs
    if args.urls:
        urls = load_urls_from_file(args.urls)
        if not urls:
            print("Error: No URLs found in file", file=sys.stderr)
            return 1
    else:
        urls = DEFAULT_URLS[: args.count]

    if args.verbose:
        print(f"Testing {len(urls)} URLs")
        print(f"Running {args.runs} iterations per engine")
        print()

    try:
        # Run sync engine benchmark
        sync_engine = SyncEngine()
        sync_result = run_benchmark("sync", sync_engine, urls, runs=args.runs)

        print()

        # Run threaded engine benchmark
        threaded_engine = ThreadedEngine(max_workers=min(len(urls), 10))
        threaded_result = run_benchmark(
            "threaded", threaded_engine, urls, runs=args.runs
        )

        print()

        # Format and output results
        results = format_results(sync_result, threaded_result)

        # Write to file
        output_path = Path(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        print("=" * 50)
        print("BENCHMARK RESULTS")
        print("=" * 50)
        print(
            f"Sync Engine:   {sync_result['average_time_sec']:.3f}s ({sync_result['rps']:.1f} RPS)"
        )
        print(
            f"Threaded Engine: {threaded_result['average_time_sec']:.3f}s ({threaded_result['rps']:.1f} RPS)"
        )
        print(f"Speedup Factor: {results['comparison']['speedup_factor']:.2f}x")
        print(f"Results saved to: {output_path}")
        print("=" * 50)

        return 0

    except Exception as e:
        print(f"Error running benchmark: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
