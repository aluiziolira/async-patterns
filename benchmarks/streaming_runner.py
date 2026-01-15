"""Streaming Benchmark Runner for memory-efficient benchmark execution.

This module provides StreamingBenchmarkRunner that uses ProducerConsumerPipeline
for memory-efficient processing of benchmark results without accumulating them
in memory.

Architecture:
    [AsyncEngine.run_streaming()] → yield RequestResult → Pipeline.put() → Writer.write_batch()
"""

from __future__ import annotations

import dataclasses
import statistics
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from async_patterns.engine import AsyncEngine, ConnectionConfig
from async_patterns.observability.loop_monitor import EventLoopMonitor
from async_patterns.patterns.pipeline import PipelineMetrics, ProducerConsumerPipeline
from async_patterns.persistence.base import StorageWriter
from async_patterns.persistence.jsonl import JsonlWriter, JsonlWriterConfig
from async_patterns.persistence.sqlite import SqliteWriter, SqliteWriterConfig


@dataclass(frozen=True, slots=True)
class StreamingBenchmarkConfig:
    """Configuration for streaming benchmark execution.

    Attributes:
        max_concurrent: Maximum concurrent requests.
        batch_size: Number of results per batch for persistence.
        batch_timeout_seconds: Max seconds to wait for batch to fill.
        max_queue_size: Maximum queue size for backpressure.
        jsonl_path: Optional path to JSONL output file.
        sqlite_path: Optional path to SQLite database.
        enable_monitor_loop: Whether to enable event loop monitoring.
        monitor_sample_interval: Sample interval for loop monitoring.
    """

    max_concurrent: int = 100
    batch_size: int = 100
    batch_timeout_seconds: float = 5.0
    max_queue_size: int = 1000
    jsonl_path: Path | None = None
    sqlite_path: Path | None = None
    enable_monitor_loop: bool = False
    monitor_sample_interval: float = 1.0


@dataclass(frozen=True, slots=True)
class LoopHealthMetrics:
    """Event loop health metrics from monitoring.

    Attributes:
        max_lag_ms: Maximum event loop latency observed.
        p95_lag_ms: 95th percentile event loop latency.
        peak_backlog: Maximum task backlog observed.
        samples: Number of samples collected.
        warnings: List of warning messages.
    """

    max_lag_ms: float
    p95_lag_ms: float
    peak_backlog: int
    samples: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "max_lag_ms": self.max_lag_ms,
            "p95_lag_ms": self.p95_lag_ms,
            "peak_backlog": self.peak_backlog,
            "samples": self.samples,
            "warnings": self.warnings,
        }


@dataclass(frozen=True, slots=True)
class StreamingBenchmarkResult:
    """Result from a streaming benchmark execution.

    Attributes:
        total_requests: Total number of requests made.
        successful: Number of successful requests.
        failed: Number of failed requests.
        total_duration_seconds: Total benchmark duration in seconds.
        requests_per_second: Throughput in requests per second.
        avg_latency_ms: Average latency in milliseconds.
        p50_latency_ms: Median latency in milliseconds.
        p95_latency_ms: 95th percentile latency in milliseconds.
        p99_latency_ms: 99th percentile latency in milliseconds.
        peak_memory_mb: Peak memory usage in megabytes.
        error_breakdown: Dictionary mapping error type to count.
        loop_health: Event loop health metrics (if monitoring enabled).
        pipeline_metrics: Pipeline metrics (if persistence enabled).
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
    loop_health: LoopHealthMetrics | None = None
    pipeline_metrics: PipelineMetrics | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary for serialization."""
        result: dict[str, Any] = {
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
        if self.loop_health:
            result["loop_health"] = self.loop_health.to_dict()
        if self.pipeline_metrics:
            result["pipeline_metrics"] = dataclasses.asdict(self.pipeline_metrics)
        return result


def _percentile(sorted_data: list[float], p: float) -> float:
    """Calculate percentile using linear interpolation."""
    if not sorted_data:
        return 0.0
    n = len(sorted_data)
    k = (n - 1) * p
    f = int(k)
    c = f + 1 if f + 1 < n else f
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


class StreamingBenchmarkRunner:
    """Memory-efficient benchmark runner using streaming pipeline.

    This runner processes benchmark results through a ProducerConsumerPipeline
    to avoid accumulating results in memory. Results are either:
    - Discarded (no persistence)
    - Written to JSONL file
    - Written to SQLite database
    - Both (dual persistence)

    Attributes:
        config: Benchmark configuration.
        engine: AsyncEngine instance for making requests.

    Example:
        ```python
        import asyncio
        from async_patterns.engine import AsyncEngine
        from benchmarks.streaming_runner import StreamingBenchmarkRunner, StreamingBenchmarkConfig

        async def main():
            config = StreamingBenchmarkConfig(
                max_concurrent=200,
                batch_size=100,
                jsonl_path=Path("results.jsonl"),
                enable_monitor_loop=True,
            )
            runner = StreamingBenchmarkRunner(config)

            urls = [f"http://localhost:8765/get" for _ in range(1000)]
            result = await runner.run(urls)

            print(f"RPS: {result.requests_per_second}")
            print(f"Peak Memory: {result.peak_memory_mb:.2f} MB")

        asyncio.run(main())
        ```
    """

    def __init__(
        self,
        config: StreamingBenchmarkConfig,
        engine: AsyncEngine | None = None,
    ) -> None:
        """Initialize the streaming benchmark runner.

        Args:
            config: Benchmark configuration.
            engine: Optional AsyncEngine instance. If not provided, creates one.
        """
        self._config = config
        self._engine = engine or AsyncEngine(
            max_concurrent=config.max_concurrent,
            config=ConnectionConfig(),
        )

    async def run(self, urls: list[str]) -> StreamingBenchmarkResult:
        """Execute streaming benchmark for the given URLs.

        Results are processed through the pipeline for memory efficiency.
        Optionally writes results to persistence layers.

        Args:
            urls: List of URLs to request.

        Returns:
            StreamingBenchmarkResult with aggregate metrics.
        """
        if not urls:
            return StreamingBenchmarkResult(
                total_requests=0,
                successful=0,
                failed=0,
                total_duration_seconds=0.0,
                requests_per_second=0.0,
                avg_latency_ms=0.0,
                p50_latency_ms=0.0,
                p95_latency_ms=0.0,
                p99_latency_ms=0.0,
                peak_memory_mb=0.0,
                error_breakdown={},
            )

        # Start memory tracking
        tracemalloc.start()
        start_time = time.perf_counter()

        # Initialize event loop monitor if enabled
        monitor: EventLoopMonitor | None = None
        if self._config.enable_monitor_loop:
            monitor = EventLoopMonitor(
                sample_interval_seconds=self._config.monitor_sample_interval,
                log_metrics=False,
                structured_logging=False,
            )
            monitor.start()

        # Initialize writers and pipeline
        writers: list[StorageWriter] = []
        pipeline: ProducerConsumerPipeline | None = None

        async def write_batch(items: list[Any]) -> None:
            """Write a batch of items to all configured writers."""
            for writer in writers:
                await writer.write_batch(items)

        if self._config.jsonl_path or self._config.sqlite_path:
            # Create writers
            if self._config.jsonl_path:
                writers.append(
                    JsonlWriter(
                        JsonlWriterConfig(
                            file_path=self._config.jsonl_path,
                            buffer_size=self._config.batch_size,
                        )
                    )
                )
            if self._config.sqlite_path:
                writers.append(
                    SqliteWriter(
                        SqliteWriterConfig(
                            db_path=self._config.sqlite_path,
                            buffer_size=self._config.batch_size,
                        )
                    )
                )

            # Create pipeline - using type: ignore because the pipeline accepts awaitable functions
            pipeline = ProducerConsumerPipeline(
                on_batch=write_batch,  # type: ignore[arg-type]
                max_queue_size=self._config.max_queue_size,
                batch_size=self._config.batch_size,
                batch_timeout_seconds=self._config.batch_timeout_seconds,
                drop_on_full=False,
            )
            pipeline.start()

        # Collect metrics (not results) for final report
        latencies: list[float] = []
        success_count = 0
        error_count = 0
        error_breakdown: dict[str, int] = {}

        try:
            # Stream results through pipeline
            async for result in self._engine.run_streaming(urls):
                # Track metrics
                latencies.append(result.latency_ms)

                if result.error is None and 200 <= result.status_code < 300:
                    success_count += 1
                else:
                    error_count += 1
                    error_type = (
                        result.error.split(":")[0]
                        if result.error
                        else f"HTTP {result.status_code}"
                    )
                    error_breakdown[error_type] = error_breakdown.get(error_type, 0) + 1

                # Put through pipeline if configured
                if pipeline:
                    await pipeline.put(result)

        finally:
            # Stop pipeline and close writers
            if pipeline:
                await pipeline.stop(drain=True)

            for writer in writers:
                if hasattr(writer, "close"):
                    await writer.close()

            # Stop memory tracking
            _, peak = tracemalloc.get_traced_memory()
            peak_memory_mb = peak / (1024 * 1024)
            tracemalloc.stop()

            # Stop monitor
            if monitor:
                monitor.stop()

        total_time = time.perf_counter() - start_time
        total_requests = len(latencies)

        # Calculate percentiles
        sorted_latencies = sorted(latencies)
        p50 = _percentile(sorted_latencies, 0.50)
        p95 = _percentile(sorted_latencies, 0.95)
        p99 = _percentile(sorted_latencies, 0.99)
        avg_latency = statistics.mean(latencies) if latencies else 0.0

        # Calculate loop health metrics
        loop_health: LoopHealthMetrics | None = None
        if monitor:
            history = monitor.get_history()
            if history:
                latencies_ms = [m.loop_latency_ms for m in history]
                backlogs = [m.backlog_size for m in history]
                all_warnings: list[str] = []
                for m in history:
                    all_warnings.extend(m.warning_messages)

                sorted_latencies_ms = sorted(latencies_ms)
                loop_health = LoopHealthMetrics(
                    max_lag_ms=max(latencies_ms) if latencies_ms else 0.0,
                    p95_lag_ms=_percentile(sorted_latencies_ms, 0.95),
                    peak_backlog=max(backlogs) if backlogs else 0,
                    samples=len(history),
                    warnings=all_warnings,
                )

        # Get pipeline metrics
        pipeline_metrics: PipelineMetrics | None = None
        if pipeline:
            pipeline_metrics = pipeline.get_metrics()

        return StreamingBenchmarkResult(
            total_requests=total_requests,
            successful=success_count,
            failed=error_count,
            total_duration_seconds=total_time,
            requests_per_second=total_requests / total_time if total_time > 0 else 0.0,
            avg_latency_ms=avg_latency,
            p50_latency_ms=p50,
            p95_latency_ms=p95,
            p99_latency_ms=p99,
            peak_memory_mb=peak_memory_mb,
            error_breakdown=error_breakdown,
            loop_health=loop_health,
            pipeline_metrics=pipeline_metrics,
        )


async def run_streaming_benchmark_session(
    urls: list[str],
    config: StreamingBenchmarkConfig,
) -> StreamingBenchmarkResult:
    """Run a streaming benchmark session.

    Args:
        urls: List of URLs to benchmark.
        config: Streaming benchmark configuration.

    Returns:
        StreamingBenchmarkResult with aggregate metrics.
    """
    runner = StreamingBenchmarkRunner(config)
    return await runner.run(urls)
