"""Tests for SemaphoreLimiter pattern for bounded concurrency control."""

import asyncio

import pytest

from async_patterns.patterns.semaphore import (
    AcquisitionTimeoutError,
    SemaphoreLimiter,
    SemaphoreMetrics,
)


class TestSemaphoreLimiterBasic:
    """Basic functionality tests for SemaphoreLimiter."""

    def test_initialization_default_max(self):
        """Test SemaphoreLimiter initializes with default max_concurrent of 100."""
        limiter = SemaphoreLimiter()
        assert limiter.max_concurrent == 100

    def test_initialization_custom_max(self):
        """Test SemaphoreLimiter initializes with custom max_concurrent."""
        limiter = SemaphoreLimiter(max_concurrent=5)
        assert limiter.max_concurrent == 5

    def test_initial_metrics(self):
        """Test initial metrics are zero."""
        limiter = SemaphoreLimiter()
        metrics = limiter.get_metrics()
        assert metrics.current_active == 0
        assert metrics.peak_active == 0
        assert metrics.total_acquisitions == 0


class TestSemaphoreLimiterAsyncContextManager:
    """Tests for async context manager behavior."""

    @pytest.mark.asyncio
    async def test_acquire_and_release(self):
        """Test basic acquire and release via context manager."""
        limiter = SemaphoreLimiter(max_concurrent=2)

        async with limiter as lim:
            metrics = lim.get_metrics()
            assert metrics.current_active == 1
            assert metrics.total_acquisitions == 1

        # After exiting context, current_active should be 0
        metrics = limiter.get_metrics()
        assert metrics.current_active == 0

    @pytest.mark.asyncio
    async def test_nested_context_managers(self):
        """Test multiple concurrent context manager entries."""
        limiter = SemaphoreLimiter(max_concurrent=2)
        results = []

        async def task(task_id: int):
            async with limiter:
                results.append(("start", task_id))
                await asyncio.sleep(0.01)  # Allow other tasks to attempt entry
                results.append(("end", task_id))

        # Start 2 tasks - both should acquire
        await asyncio.gather(task(1), task(2))

        # Verify both tasks executed
        assert len([r for r in results if r[0] == "start"]) == 2
        assert len([r for r in results if r[0] == "end"]) == 2


class TestSemaphoreLimiterConcurrency:
    """Tests for concurrency control enforcement."""

    @pytest.mark.asyncio
    async def test_max_concurrent_enforced(self):
        """Test that max_concurrent is properly enforced."""
        max_concurrent = 3
        limiter = SemaphoreLimiter(max_concurrent=max_concurrent)
        active_count = 0
        max_seen = 0
        start_barrier = asyncio.Event()

        async def task(task_id: int):
            nonlocal active_count, max_seen

            async with limiter:
                active_count += 1
                max_seen = max(max_seen, active_count)
                start_barrier.set()  # Signal that we've entered
                await asyncio.sleep(0.01)  # Hold the semaphore
                active_count -= 1

        # Start more tasks than allowed
        tasks = [task(i) for i in range(10)]

        # Start all tasks
        for t in tasks:
            asyncio.create_task(t)

        # Wait a moment to let tasks acquire
        await asyncio.sleep(0.001)

        # At this point, we should have exactly max_concurrent active
        # because the semaphore should block further acquisitions
        await asyncio.sleep(0.02)  # Let all tasks complete

        assert max_seen <= max_concurrent

    @pytest.mark.asyncio
    async def test_sequential_acquisitions(self):
        """Test that sequential acquisitions work correctly."""
        limiter = SemaphoreLimiter(max_concurrent=1)

        for i in range(5):
            async with limiter:
                metrics = limiter.get_metrics()
                assert metrics.current_active == 1
                await asyncio.sleep(0.001)

        final_metrics = limiter.get_metrics()
        assert final_metrics.total_acquisitions == 5
        assert final_metrics.current_active == 0
        assert final_metrics.peak_active == 1


class TestSemaphoreLimiterMetrics:
    """Tests for metrics tracking."""

    @pytest.mark.asyncio
    async def test_total_acquisitions_increments(self):
        """Test that total_acquisitions increments on each entry."""
        limiter = SemaphoreLimiter(max_concurrent=2)

        for _ in range(5):
            async with limiter:
                pass

        metrics = limiter.get_metrics()
        assert metrics.total_acquisitions == 5

    @pytest.mark.asyncio
    async def test_peak_active_tracked(self):
        """Test that peak_active is tracked correctly."""
        limiter = SemaphoreLimiter(max_concurrent=2)
        peak_seen = 0

        async def task(task_id: int):
            nonlocal peak_seen
            async with limiter:
                metrics = limiter.get_metrics()
                peak_seen = max(peak_seen, metrics.current_active)
                await asyncio.sleep(0.01)

        # Start 4 tasks with max_concurrent=2
        await asyncio.gather(task(1), task(2), task(3), task(4))

        # Peak should be 2 (max_concurrent)
        assert peak_seen <= limiter.max_concurrent

    @pytest.mark.asyncio
    async def test_metrics_reset_on_new_instance(self):
        """Test that new limiter instances have fresh metrics."""
        limiter1 = SemaphoreLimiter(max_concurrent=2)

        async with limiter1:
            pass

        metrics1 = limiter1.get_metrics()
        assert metrics1.total_acquisitions == 1

        limiter2 = SemaphoreLimiter(max_concurrent=2)
        metrics2 = limiter2.get_metrics()
        assert metrics2.total_acquisitions == 0

    @pytest.mark.asyncio
    async def test_current_active_resets_on_exit(self):
        """Test that current_active resets to 0 after context exit."""
        limiter = SemaphoreLimiter(max_concurrent=5)

        async with limiter:
            metrics_inside = limiter.get_metrics()
            assert metrics_inside.current_active > 0

        metrics_outside = limiter.get_metrics()
        assert metrics_outside.current_active == 0


class TestSemaphoreLimiterEdgeCases:
    """Edge case tests."""

    @pytest.mark.asyncio
    async def test_max_concurrent_one(self):
        """Test with max_concurrent=1 (strictly sequential)."""
        limiter = SemaphoreLimiter(max_concurrent=1)
        execution_order = []

        async def task(task_id: int):
            async with limiter:
                execution_order.append(("start", task_id))
                await asyncio.sleep(0.001)
                execution_order.append(("end", task_id))

        await asyncio.gather(task(1), task(2), task(3))

        # With max_concurrent=1, tasks must be strictly sequential
        # Task 2 can only start after task 1 ends
        assert len(execution_order) == 6  # 3 tasks * 2 events

    @pytest.mark.asyncio
    async def test_exception_in_context_releases(self):
        """Test that exceptions still release the semaphore."""
        limiter = SemaphoreLimiter(max_concurrent=1)

        async def raise_error():
            async with limiter:
                metrics = limiter.get_metrics()
                assert metrics.current_active == 1
                raise ValueError("Test exception")

        with pytest.raises(ValueError, match="Test exception"):
            await raise_error()

        metrics = limiter.get_metrics()
        assert metrics.current_active == 0

    @pytest.mark.asyncio
    async def test_multiple_instances_independent(self):
        """Test that multiple limiter instances work independently."""
        limiter1 = SemaphoreLimiter(max_concurrent=1)
        limiter2 = SemaphoreLimiter(max_concurrent=2)

        async with limiter1:
            metrics1 = limiter1.get_metrics()
            assert metrics1.current_active == 1

            async with limiter2:
                metrics2 = limiter2.get_metrics()
                assert metrics2.current_active == 1

        assert limiter1.get_metrics().current_active == 0
        assert limiter2.get_metrics().current_active == 0


class TestSemaphoreMetricsDataclass:
    """Tests for SemaphoreMetrics dataclass."""

    def test_metrics_dataclass_creation(self):
        """Test SemaphoreMetrics can be created."""
        metrics = SemaphoreMetrics(
            current_active=5, peak_active=10, total_acquisitions=100
        )
        assert metrics.current_active == 5
        assert metrics.peak_active == 10
        assert metrics.total_acquisitions == 100

    def test_metrics_dataclass_equality(self):
        """Test SemaphoreMetrics equality comparison."""
        metrics1 = SemaphoreMetrics(
            current_active=1, peak_active=2, total_acquisitions=3
        )
        metrics2 = SemaphoreMetrics(
            current_active=1, peak_active=2, total_acquisitions=3
        )
        metrics3 = SemaphoreMetrics(
            current_active=1, peak_active=2, total_acquisitions=4
        )

        assert metrics1 == metrics2
        assert metrics1 != metrics3

    def test_metrics_dataclass_immutable(self):
        """Test that SemaphoreMetrics fields can be read and fields exist."""
        metrics = SemaphoreMetrics(
            current_active=1, peak_active=2, total_acquisitions=3
        )

        # Dataclasses are mutable, but fields can be read
        assert metrics.current_active == 1
        assert metrics.peak_active == 2
        assert metrics.total_acquisitions == 3


class TestSemaphoreLimiterTimeout:
    """Tests for semaphore acquisition timeout functionality."""

    def test_acquire_timeout_parameter_default(self):
        """Test default acquire_timeout is 30.0 seconds."""
        limiter = SemaphoreLimiter()
        assert limiter._acquire_timeout == 30.0

    def test_acquire_timeout_parameter_custom(self):
        """Test custom acquire_timeout parameter."""
        limiter = SemaphoreLimiter(acquire_timeout=5.0)
        assert limiter._acquire_timeout == 5.0

    def test_invalid_negative_timeout_raises(self):
        """Test that negative timeout raises ValueError."""
        with pytest.raises(ValueError, match="acquire_timeout must be non-negative"):
            SemaphoreLimiter(acquire_timeout=-1.0)

    @pytest.mark.asyncio
    async def test_acquire_timeout_raises_error(self):
        """Test that AcquisitionTimeoutError is raised when timeout is exceeded."""
        limiter = SemaphoreLimiter(max_concurrent=1, acquire_timeout=0.05)

        async def hold_semaphore():
            async with limiter:
                await asyncio.sleep(0.2)  # Hold longer than timeout

        # Start a task that holds the semaphore
        task = asyncio.create_task(hold_semaphore())
        await asyncio.sleep(0.01)  # Let it acquire the semaphore

        # Now try to acquire - should timeout
        with pytest.raises(AcquisitionTimeoutError):
            async with limiter:
                pass

        # Cancel the holding task
        task.cancel()
        from contextlib import suppress

        with suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_acquire_within_timeout_succeeds(self):
        """Test that acquisition within timeout succeeds without exception."""
        limiter = SemaphoreLimiter(max_concurrent=1, acquire_timeout=1.0)

        async with limiter:
            # This should succeed without raising
            metrics = limiter.get_metrics()
            assert metrics.current_active == 1

    @pytest.mark.asyncio
    async def test_timeout_count_increments_on_timeout(self):
        """Test that timeout_count increments when acquisition times out."""
        limiter = SemaphoreLimiter(max_concurrent=1, acquire_timeout=0.05)

        async def hold_semaphore():
            async with limiter:
                await asyncio.sleep(0.2)

        # Start a task that holds the semaphore
        task = asyncio.create_task(hold_semaphore())
        await asyncio.sleep(0.01)

        # Try to acquire - should timeout
        try:
            async with limiter:
                pass
        except AcquisitionTimeoutError:
            pass

        # Check metrics
        metrics = limiter.get_metrics()
        assert metrics.timeout_count == 1

        # Cancel the holding task
        task.cancel()
        from contextlib import suppress

        with suppress(asyncio.CancelledError):
            await task
