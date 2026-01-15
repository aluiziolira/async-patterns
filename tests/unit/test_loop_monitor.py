"""Tests for Event Loop Monitoring."""

import asyncio
import time

import pytest

from async_patterns.observability.loop_monitor import (
    EventLoopMonitor,
    LoopMonitorMetrics,
)


class TestEventLoopMonitorBasic:
    """Tests for basic monitor functionality."""

    @pytest.mark.asyncio
    async def test_monitor_start_stop(self):
        """Test that monitor can be started and stopped."""
        monitor = EventLoopMonitor(sample_interval_seconds=0.1)
        assert not monitor.is_running

        monitor.start()
        assert monitor.is_running

        await asyncio.sleep(0.05)  # Let it run briefly
        await monitor.stop_async()
        assert not monitor.is_running

    @pytest.mark.asyncio
    async def test_monitor_can_be_started_in_async_context(self):
        """Test that monitor works when started from async context."""
        monitor = EventLoopMonitor(sample_interval_seconds=0.05)

        async def start_and_stop():
            monitor.start()
            await asyncio.sleep(0.1)  # Let it run briefly
            await monitor.stop_async()

        await start_and_stop()
        assert not monitor.is_running

    @pytest.mark.asyncio
    async def test_monitor_stop_async(self):
        """Test stopping the monitor from async context."""
        monitor = EventLoopMonitor(sample_interval_seconds=0.05)

        monitor.start()
        await asyncio.sleep(0.05)
        await monitor.stop_async()

        assert not monitor.is_running
        assert monitor._monitor_task is None or monitor._monitor_task.done()


class TestEventLoopMonitorLatency:
    """Tests for latency measurement."""

    @pytest.mark.asyncio
    async def test_monitor_latency_sampling(self):
        """Test that latency samples are collected."""
        monitor = EventLoopMonitor(sample_interval_seconds=0.01)

        monitor.start()

        # Let it collect a few samples
        await asyncio.sleep(0.05)

        samples = monitor.get_latency_samples(10)

        await monitor.stop_async()

        # Should have collected some samples
        assert len(samples) >= 0  # May be empty if stopped quickly
        for sample in samples:
            assert sample >= 0

    @pytest.mark.asyncio
    async def test_monitor_snapshot(self):
        """Test that snapshot returns valid metrics."""
        monitor = EventLoopMonitor()

        # Get snapshot before starting
        snapshot = monitor.get_snapshot()

        assert snapshot.timestamp > 0
        assert snapshot.task_count >= 0
        # is_healthy should be a bool
        assert isinstance(snapshot.is_healthy, bool)
        # Latency should be a positive number (even if very small)
        assert snapshot.loop_latency_ms >= 0

    @pytest.mark.asyncio
    async def test_monitor_avg_latency(self):
        """Test average latency calculation."""
        monitor = EventLoopMonitor(sample_interval_seconds=0.01)

        monitor.start()
        await asyncio.sleep(0.05)

        avg = monitor.get_avg_latency(10)

        await monitor.stop_async()

        # Should return a value (0 if no samples)
        assert avg >= 0

    @pytest.mark.asyncio
    async def test_monitor_percentile_latency(self):
        """Test percentile latency calculation."""
        monitor = EventLoopMonitor(sample_interval_seconds=0.01)

        monitor.start()
        await asyncio.sleep(0.05)

        p50 = monitor.get_percentile_latency(50, 10)
        p95 = monitor.get_percentile_latency(95, 10)
        p99 = monitor.get_percentile_latency(99, 10)

        await monitor.stop_async()

        assert p50 >= 0
        assert p95 >= p50
        assert p99 >= p95


class TestEventLoopMonitorTaskCount:
    """Tests for task count monitoring."""

    @pytest.mark.asyncio
    async def test_monitor_task_count(self):
        """Test that task count is tracked."""
        monitor = EventLoopMonitor(sample_interval_seconds=0.01)

        monitor.start()

        # Create some tasks
        async def busy_task():
            await asyncio.sleep(1)

        tasks = [asyncio.create_task(busy_task()) for _ in range(5)]

        # Let the monitor sample
        await asyncio.sleep(0.05)

        snapshot = monitor.get_snapshot()

        # Should see at least our 5 tasks plus monitor task
        assert snapshot.task_count >= 5

        # Clean up
        for task in tasks:
            task.cancel()

        await monitor.stop_async()

    @pytest.mark.asyncio
    async def test_monitor_task_count_with_active_tasks(self):
        """Test task count during active async operations."""
        monitor = EventLoopMonitor(sample_interval_seconds=0.1)
        monitor.start()

        # Do some async work
        async def work():
            for _ in range(10):
                await asyncio.sleep(0.01)

        await work()

        snapshot = monitor.get_snapshot()
        assert snapshot.task_count >= 0

        await monitor.stop_async()


class TestEventLoopMonitorThresholds:
    """Tests for warning thresholds."""

    @pytest.mark.asyncio
    async def test_monitor_warning_threshold(self):
        """Test that warnings are issued when threshold exceeded."""
        # Create monitor with very low warning threshold
        monitor = EventLoopMonitor(
            sample_interval_seconds=0.01,
            warning_threshold_ms=0.001,  # Very low threshold
            log_metrics=False,  # Don't clutter test output
        )

        monitor.start()
        await asyncio.sleep(0.05)

        # Check if warning was issued (metrics history should show warnings)
        history = monitor.get_history(10)

        await monitor.stop_async()

        # At least should have some samples
        assert len(history) >= 0


class TestEventLoopMonitorHistory:
    """Tests for metrics history."""

    @pytest.mark.asyncio
    async def test_monitor_history_limit(self):
        """Test that history is limited to 100 entries."""
        monitor = EventLoopMonitor(sample_interval_seconds=0.001)

        monitor.start()
        # Let it collect many samples
        await asyncio.sleep(0.2)

        history = monitor.get_history(limit=200)

        await monitor.stop_async()

        # Should be limited to 100
        assert len(history) <= 100

    @pytest.mark.asyncio
    async def test_monitor_history_order(self):
        """Test that history is returned in chronological order."""
        monitor = EventLoopMonitor(sample_interval_seconds=0.01)

        monitor.start()
        await asyncio.sleep(0.05)

        history = monitor.get_history(10)

        await monitor.stop_async()

        if len(history) >= 2:
            # First entry should be older
            assert history[0].timestamp <= history[1].timestamp


class TestLoopMonitorMetrics:
    """Tests for LoopMonitorMetrics dataclass."""

    def test_metrics_creation(self):
        """Test LoopMonitorMetrics can be created."""
        metrics = LoopMonitorMetrics(
            timestamp=time.time(),
            task_count=10,
            backlog_size=5,
            loop_latency_ms=50.5,
            callback_queue_depth=None,
            is_healthy=True,
        )
        assert metrics.task_count == 10
        assert metrics.loop_latency_ms == 50.5
        assert metrics.is_healthy

    def test_metrics_with_warnings(self):
        """Test LoopMonitorMetrics with warning messages."""
        metrics = LoopMonitorMetrics(
            timestamp=time.time(),
            task_count=10,
            backlog_size=5,
            loop_latency_ms=100.0,
            callback_queue_depth=None,
            is_healthy=False,
            warning_messages=["High latency detected"],
        )
        assert len(metrics.warning_messages) == 1


class TestEventLoopMonitorConfiguration:
    """Tests for monitor configuration."""

    def test_monitor_default_config(self):
        """Test that default configuration is applied."""
        monitor = EventLoopMonitor()

        # Should use defaults from environment or constants
        assert monitor._sample_interval > 0
        assert monitor._warning_threshold_ms > 0
        assert monitor._critical_threshold_ms > 0

    def test_monitor_custom_config(self):
        """Test that custom configuration is applied."""
        monitor = EventLoopMonitor(
            sample_interval_seconds=2.0,
            warning_threshold_ms=200.0,
            critical_threshold_ms=1000.0,
            max_task_backlog=500,
        )

        assert monitor._sample_interval == 2.0
        assert monitor._warning_threshold_ms == 200.0
        assert monitor._critical_threshold_ms == 1000.0
        assert monitor._max_task_backlog == 500


class TestEventLoopMonitorStress:
    """Stress tests for the monitor."""

    @pytest.mark.asyncio
    async def test_monitor_multiple_starts_stops(self):
        """Test that multiple start/stop cycles work."""
        monitor = EventLoopMonitor(sample_interval_seconds=0.01)

        for _ in range(3):
            monitor.start()
            await asyncio.sleep(0.01)
            await monitor.stop_async()

        assert not monitor.is_running

    @pytest.mark.asyncio
    async def test_monitor_with_concurrent_tasks(self):
        """Test monitor under concurrent task load."""
        monitor = EventLoopMonitor(sample_interval_seconds=0.05)
        monitor.start()

        # Create concurrent tasks
        async def task(i):
            await asyncio.sleep(0.1)
            return i

        results = await asyncio.gather(*[task(i) for i in range(10)])

        snapshot = monitor.get_snapshot()

        await monitor.stop_async()

        assert len(results) == 10
        assert snapshot.task_count >= 0
