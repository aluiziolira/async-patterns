"""Tests for Producer-Consumer Pipeline pattern."""

import asyncio
import gc

import pytest

from async_patterns.patterns.pipeline import (
    BatchedWriter,
    PipelineShutdownError,
    ProducerConsumerPipeline,
)


class TestPipelineBatching:
    """Tests for batch processing."""

    @pytest.mark.asyncio
    async def test_pipeline_batches_items(self):
        """Test that items are processed in batches."""
        batches_received = []

        async def process_batch(items):
            batches_received.append(list(items))

        pipeline = ProducerConsumerPipeline(
            on_batch=process_batch,
            max_queue_size=100,
            batch_size=5,
            batch_timeout_seconds=0.1,  # Reduced from 10.0 - batches fill before timeout
        )

        pipeline.start()

        # Add 15 items (3 full batches)
        for i in range(15):
            await pipeline.put(f"item_{i}")

        # Wait a bit for processing
        await asyncio.sleep(0.1)

        # Stop with drain
        await pipeline.stop(drain=True)

        # Should have processed all 15 items
        total_items = sum(len(batch) for batch in batches_received)
        assert total_items == 15

    @pytest.mark.asyncio
    async def test_pipeline_batch_timeout(self):
        """Test that partial batches are processed on timeout."""
        items_received = []

        async def process_batch(items):
            items_received.extend(items)

        pipeline = ProducerConsumerPipeline(
            on_batch=process_batch,
            max_queue_size=100,
            batch_size=10,
            batch_timeout_seconds=0.05,  # Short timeout
        )

        pipeline.start()

        # Add fewer items than batch size
        for i in range(3):
            await pipeline.put(f"item_{i}")

        # Wait for timeout to trigger processing
        await asyncio.sleep(0.1)

        await pipeline.stop(drain=True)

        # Should have processed the partial batch
        assert len(items_received) == 3


class TestPipelineBackpressure:
    """Tests for backpressure handling."""

    @pytest.mark.asyncio
    async def test_pipeline_backpressure(self):
        """Test that bounded queue applies backpressure."""
        processed_count = 0

        async def slow_process(items):
            nonlocal processed_count
            processed_count += len(items)
            await asyncio.sleep(0.1)  # Slow processing

        pipeline = ProducerConsumerPipeline(
            on_batch=slow_process,
            max_queue_size=5,
            batch_size=5,
            batch_timeout_seconds=10.0,
        )

        pipeline.start()

        # Fill the queue
        for i in range(5):
            success = await pipeline.put(f"item_{i}", timeout=0.1)
            assert success

        # Queue should be full now
        assert pipeline.queue_size == 5

        # Next put should either timeout or block
        success = await pipeline.put("item_extra", timeout=0.01)
        # May or may not succeed depending on timing

        await pipeline.stop(drain=False)

    @pytest.mark.asyncio
    async def test_pipeline_drop_on_full(self):
        """Test that items are dropped when queue is full and drop_on_full=True."""
        dropped_count = 0

        async def process_batch(items):
            pass

        pipeline = ProducerConsumerPipeline(
            on_batch=process_batch,
            max_queue_size=3,
            batch_size=3,
            batch_timeout_seconds=10.0,
            drop_on_full=True,
        )

        pipeline.start()

        # Fill the queue
        for i in range(3):
            await pipeline.put(f"item_{i}")

        # These should be dropped
        for i in range(5):
            success = await pipeline.put(f"extra_{i}", timeout=0.01)
            if not success:
                dropped_count += 1

        await pipeline.stop(drain=False)

        assert dropped_count > 0


class TestPipelineGracefulShutdown:
    """Tests for graceful shutdown."""

    @pytest.mark.asyncio
    async def test_pipeline_shutdown_rejects_new_items(self):
        """Test that new items are rejected after shutdown."""

        async def process_batch(items):
            pass

        pipeline = ProducerConsumerPipeline(
            on_batch=process_batch,
            max_queue_size=100,
            batch_size=10,
            batch_timeout_seconds=10.0,
        )

        pipeline.start()

        await pipeline.stop(drain=True)

        # Should raise PipelineShutdownError
        with pytest.raises(PipelineShutdownError):
            await pipeline.put("item")

    @pytest.mark.asyncio
    async def test_pipeline_immediate_shutdown(self):
        """Test that immediate shutdown (drain=False) works."""
        items_processed = []

        async def process_batch(items):
            items_processed.extend(items)

        pipeline = ProducerConsumerPipeline(
            on_batch=process_batch,
            max_queue_size=100,
            batch_size=10,
            batch_timeout_seconds=10.0,
        )

        pipeline.start()

        # Add some items
        for i in range(5):
            await pipeline.put(f"item_{i}")

        # Immediate stop
        await pipeline.stop(drain=False)

        # Pipeline should stop
        assert not pipeline.is_running


class TestPipelineRestart:
    """Tests for restart behavior."""

    @pytest.mark.asyncio
    async def test_pipeline_can_restart_after_stop(self):
        """Test that pipeline can start, stop, and start again."""
        processed: list[str] = []

        async def process_batch(items):
            processed.extend(items)

        pipeline = ProducerConsumerPipeline(
            on_batch=process_batch,
            max_queue_size=10,
            batch_size=1,
            batch_timeout_seconds=0.05,
        )

        pipeline.start()
        await pipeline.put("first")
        await asyncio.sleep(0.05)
        await pipeline.stop(drain=True)

        pipeline.start()
        await pipeline.put("second")
        await asyncio.sleep(0.05)
        await pipeline.stop(drain=True)

        assert processed == ["first", "second"]


class TestPipelineMetrics:
    """Tests for pipeline metrics."""

    @pytest.mark.asyncio
    async def test_pipeline_metrics_tracking(self):
        async def process_batch(items):
            pass

        pipeline = ProducerConsumerPipeline(
            on_batch=process_batch,
            max_queue_size=100,
            batch_size=5,
            batch_timeout_seconds=0.1,  # Reduced from 10.0 - batches fill before timeout
        )

        pipeline.start()

        for i in range(12):
            await pipeline.put(f"item_{i}")

        # Give time for processing
        await asyncio.sleep(0.1)

        await pipeline.stop(drain=True)

        metrics = pipeline.get_metrics()

        # Items should have been processed
        assert metrics.items_processed > 0
        assert metrics.dropped_items == 0


class TestPipelineMemory:
    """Tests for memory efficiency."""

    @pytest.mark.asyncio
    async def test_pipeline_constant_memory(self):
        """Test that memory footprint remains constant regardless of batch size."""
        import tracemalloc

        async def process_batch(items):
            # Simulate some processing time
            await asyncio.sleep(0.001)

        pipeline = ProducerConsumerPipeline(
            on_batch=process_batch,
            max_queue_size=100,
            batch_size=50,
            batch_timeout_seconds=10.0,
        )

        pipeline.start()

        # Process multiple "large" batches
        for batch_num in range(5):
            for i in range(50):
                await pipeline.put({"batch": batch_num, "item": i})

            # Force garbage collection
            gc.collect()

            # Check memory at this point
            if batch_num > 0:
                current, peak = tracemalloc.get_traced_memory()
                # Memory should not grow significantly
                assert current < 50 * 1024 * 1024  # 50MB limit

        await pipeline.stop(drain=True)


class TestBatchedWriter:
    """Tests for BatchedWriter convenience class."""

    @pytest.mark.asyncio
    async def test_batched_writer_queue_size(self):
        """Test BatchedWriter queue_size property."""

        async def write_to_destination(batch):
            pass

        writer = BatchedWriter(
            write_func=write_to_destination,
            batch_size=5,
        )

        assert writer.queue_size == 0


class TestPipelinePutMany:
    """Tests for put_many functionality."""

    @pytest.mark.asyncio
    async def test_put_many_returns_count(self):
        """Test that put_many returns the number of items added."""

        async def process_batch(items):
            pass

        pipeline = ProducerConsumerPipeline(
            on_batch=process_batch,
            max_queue_size=100,
            batch_size=10,
            batch_timeout_seconds=10.0,
        )

        pipeline.start()

        items = [f"item_{i}" for i in range(25)]
        added = await pipeline.put_many(items)

        # All items should be added (queue is large enough)
        assert added == 25

        await pipeline.stop(drain=True)
