"""Event Loop Monitoring for asyncio observability and performance tracking."""

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LoopMonitorMetrics:
    """Metrics for event loop health monitoring."""

    timestamp: float
    task_count: int
    backlog_size: int
    loop_latency_ms: float
    callback_queue_depth: int | None
    is_healthy: bool
    warning_messages: list[str] = field(default_factory=list)


class EventLoopMonitor:
    """Monitor for asyncio event loop health and performance.

    Provides real-time monitoring of event loop metrics:
    - Task backlog size
    - Event loop latency (lag)
    - Callback queue depth
    - Structured JSON logging output

    Args:
        sample_interval_seconds: How often to sample metrics. Default 1.0.
        warning_threshold_ms: Latency threshold for warnings. Default 100ms.
        critical_threshold_ms: Latency threshold for critical alerts. Default 500ms.
        max_task_backlog: Max tasks before warning. Default 1000.
        log_metrics: Whether to log metrics. Default True.
        structured_logging: Whether to use JSON logging. Default True.

    Example:
        ```python
        monitor = EventLoopMonitor(
            sample_interval_seconds=0.5,
            warning_threshold_ms=100,
        )

        # Start monitoring
        monitor.start()

        # Get current snapshot
        metrics = monitor.get_snapshot()

        # Stop when done
        monitor.stop()
        ```
    """

    def __init__(
        self,
        sample_interval_seconds: float | None = None,
        warning_threshold_ms: float | None = None,
        critical_threshold_ms: float | None = None,
        max_task_backlog: int | None = None,
        log_metrics: bool = True,
        structured_logging: bool = True,
    ) -> None:
        self._sample_interval = sample_interval_seconds or float(
            os.getenv("LOOP_MONITOR_SAMPLE_INTERVAL", "1.0")
        )
        self._warning_threshold_ms = warning_threshold_ms or float(
            os.getenv("LOOP_MONITOR_WARNING_THRESHOLD", "100.0")
        )
        self._critical_threshold_ms = critical_threshold_ms or float(
            os.getenv("LOOP_MONITOR_CRITICAL_THRESHOLD", "500.0")
        )
        self._max_task_backlog = max_task_backlog or int(
            os.getenv("LOOP_MONITOR_MAX_TASK_BACKLOG", "1000")
        )
        self._log_metrics = log_metrics
        self._structured_logging = structured_logging

        self._running = False
        self._monitor_task: asyncio.Task[None] | None = None
        self._lock = threading.Lock()

        self._metrics_history: list[LoopMonitorMetrics] = []

        self._current_latency_ms = 0.0
        self._current_task_count = 0
        self._current_backlog = 0
        self._warning_issued = False
        self._critical_issued = False

        self._baseline_loop_time: float | None = None

    @property
    def is_running(self) -> bool:
        """Check if monitoring is active."""
        return self._running

    async def _get_event_loop_latency(self) -> float:
        """Measure event loop latency by yielding control once.

        Returns:
            float: Latency in milliseconds.
        """
        start_time = time.perf_counter()
        await asyncio.sleep(0)
        return (time.perf_counter() - start_time) * 1000

    def _measure_task_backlog(self) -> int:
        """Measure the approximate task backlog size.

        Returns:
            int: Estimated number of pending tasks.
        """
        try:
            loop = asyncio.get_running_loop()
            pending_count = len(asyncio.all_tasks(loop))
            return pending_count
        except RuntimeError:
            return 0

    def _get_callback_queue_depth(self) -> int | None:
        """Estimate callback queue depth.

        Returns:
            int | None: None because asyncio does not expose callback queue depth.
        """
        return None

    def _check_thresholds(self, metrics: LoopMonitorMetrics) -> list[str]:
        """Check if metrics exceed warning thresholds.

        Args:
            metrics: The metrics to check.

        Returns:
            list[str]: List of warning messages.
        """
        warnings = []

        if metrics.loop_latency_ms > self._critical_threshold_ms:
            warnings.append(
                f"CRITICAL: Event loop latency {metrics.loop_latency_ms:.2f}ms "
                f"exceeds critical threshold {self._critical_threshold_ms:.2f}ms"
            )
            if not self._critical_issued:
                self._critical_issued = True
                logger.error(warnings[-1])

        if metrics.loop_latency_ms > self._warning_threshold_ms and not self._warning_issued:
            self._warning_issued = True
            logger.warning(
                f"Event loop latency {metrics.loop_latency_ms:.2f}ms "
                f"exceeds warning threshold {self._warning_threshold_ms:.2f}ms"
            )

        if self._warning_issued and metrics.loop_latency_ms < self._warning_threshold_ms:
            self._warning_issued = False
            self._critical_issued = False

        if metrics.task_count > self._max_task_backlog:
            warnings.append(
                f"WARNING: Task backlog {metrics.task_count} exceeds max {self._max_task_backlog}"
            )
            logger.warning(warnings[-1])

        return warnings

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                latency_ms = await self._get_event_loop_latency()
                task_count = self._measure_task_backlog()

                # Calculate backlog (approximate)
                backlog = max(0, task_count - 1)  # Subtract monitor task

                metrics = LoopMonitorMetrics(
                    timestamp=time.time(),
                    task_count=task_count,
                    backlog_size=backlog,
                    loop_latency_ms=latency_ms,
                    callback_queue_depth=self._get_callback_queue_depth(),
                    is_healthy=latency_ms < self._warning_threshold_ms,
                )

                warnings = self._check_thresholds(metrics)
                metrics = LoopMonitorMetrics(
                    timestamp=metrics.timestamp,
                    task_count=metrics.task_count,
                    backlog_size=metrics.backlog_size,
                    loop_latency_ms=metrics.loop_latency_ms,
                    callback_queue_depth=metrics.callback_queue_depth,
                    is_healthy=metrics.is_healthy,
                    warning_messages=warnings,
                )

                with self._lock:
                    self._current_latency_ms = latency_ms
                    self._current_task_count = task_count
                    self._current_backlog = backlog

                    self._metrics_history.append(metrics)
                    if len(self._metrics_history) > 100:
                        self._metrics_history.pop(0)

                # Log metrics
                if self._log_metrics:
                    if self._structured_logging:
                        log_data = {
                            "event": "loop_monitor_metrics",
                            "timestamp": metrics.timestamp,
                            "task_count": metrics.task_count,
                            "backlog_size": metrics.backlog_size,
                            "loop_latency_ms": round(metrics.loop_latency_ms, 3),
                            "is_healthy": metrics.is_healthy,
                        }
                        logger.info(json.dumps(log_data))
                    else:
                        logger.info(
                            f"Loop Monitor - Tasks: {metrics.task_count}, "
                            f"Backlog: {metrics.backlog_size}, "
                            f"Latency: {metrics.loop_latency_ms:.2f}ms"
                        )

                await asyncio.sleep(self._sample_interval)

            except Exception as exc:
                logger.error(f"Error in monitor loop: {exc}")
                await asyncio.sleep(self._sample_interval)

    def start(self) -> None:
        """Start the event loop monitor."""
        if self._running:
            return

        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Event loop monitor started")

    def stop(self) -> None:
        """Stop the event loop monitor."""
        if not self._running:
            return

        self._running = False

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                loop = self._monitor_task.get_loop()
                with contextlib.suppress(RuntimeError, asyncio.CancelledError):
                    loop.run_until_complete(self._monitor_task)
            else:
                logger.warning("Event loop is running; call stop_async() instead.")
            self._monitor_task = None

        logger.info("Event loop monitor stopped")

    async def stop_async(self) -> None:
        """Stop the monitor from async context."""
        if not self._running:
            return

        self._running = False

        if self._monitor_task:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
            self._monitor_task = None

        logger.info("Event loop monitor stopped")

    def get_snapshot(self) -> LoopMonitorMetrics:
        """Get a snapshot of current metrics.

        Returns:
            LoopMonitorMetrics: Current metrics snapshot.
        """
        if not self._running:
            return LoopMonitorMetrics(
                timestamp=time.time(),
                task_count=self._current_task_count,
                backlog_size=self._current_backlog,
                loop_latency_ms=self._current_latency_ms,
                callback_queue_depth=self._get_callback_queue_depth(),
                is_healthy=self._current_latency_ms < self._warning_threshold_ms,
            )

        if self._running:
            with self._lock:
                latency_ms = self._current_latency_ms
                task_count = self._current_task_count
                backlog = self._current_backlog
        else:
            latency_ms = self._current_latency_ms
            task_count = self._measure_task_backlog()
            backlog = max(0, task_count - 1)

        return LoopMonitorMetrics(
            timestamp=time.time(),
            task_count=task_count,
            backlog_size=backlog,
            loop_latency_ms=latency_ms,
            callback_queue_depth=self._get_callback_queue_depth(),
            is_healthy=latency_ms < self._warning_threshold_ms,
        )

    def get_history(self, limit: int = 100) -> list[LoopMonitorMetrics]:
        """Get metrics history.

        Args:
            limit: Maximum number of entries to return.

        Returns:
            list[LoopMonitorMetrics]: Historical metrics.
        """
        with self._lock:
            return list(self._metrics_history[-limit:])

    def get_latency_samples(self, count: int = 10) -> list[float]:
        """Get recent latency samples.

        Args:
            count: Number of samples to return.

        Returns:
            list[float]: Recent latency values in milliseconds.
        """
        history = self.get_history(count)
        return [m.loop_latency_ms for m in history]

    def get_avg_latency(self, count: int = 10) -> float:
        """Calculate average latency over recent samples.

        Args:
            count: Number of samples to average.

        Returns:
            float: Average latency in milliseconds.
        """
        samples = self.get_latency_samples(count)
        if not samples:
            return 0.0
        return sum(samples) / len(samples)

    def get_percentile_latency(self, percentile: float, count: int = 100) -> float:
        """Calculate percentile latency.

        Args:
            percentile: Percentile to calculate (0-100).
            count: Number of samples to use.

        Returns:
            float: Latency at the given percentile in milliseconds.
        """
        samples = self.get_latency_samples(count)
        if not samples:
            return 0.0
        sorted_samples = sorted(samples)
        index = int(len(sorted_samples) * percentile / 100)
        return sorted_samples[min(index, len(sorted_samples) - 1)]
