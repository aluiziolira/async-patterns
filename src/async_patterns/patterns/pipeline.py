"""Producer-Consumer Pipeline pattern for streaming data processing with backpressure."""

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineMetrics:
    """Metrics for tracking pipeline performance."""

    items_processed: int
    batches_processed: int
    items_in_queue: int
    avg_batch_size: float
    processing_time_ms: float
    dropped_items: int = field(default=0)


class PipelineShutdownError(Exception):
    """Raised when pipeline is shutting down."""

    pass


class ProducerConsumerPipeline:
    """
    Producer-consumer pipeline with bounded queue for backpressure.

    Implements a classic producer-consumer pattern using asyncio.Queue with
    bounded size to apply backpressure when consumers cannot keep up.
    Items are processed in configurable batches (by count or timeout).

    Args:
        max_queue_size: Maximum number of items in the queue. Default 1000.
        batch_size: Number of items per batch. Default 100.
        batch_timeout_seconds: Max seconds to wait for batch to fill. Default 5.
        on_batch: Callback function to process batches.
        on_error: Optional callback for error handling.
        drop_on_full: If True, drop new items when queue is full. Default False.

    Example:
        ```python
        async def process_batch(items: list[dict]):
            await database.insert_many(items)

        pipeline = ProducerConsumerPipeline(
            max_queue_size=1000,
            batch_size=100,
            batch_timeout_seconds=5,
            on_batch=process_batch,
        )

        # Start consuming
        asyncio.create_task(pipeline.run())

        # Produce items
        for item in data_generator():
            await pipeline.put(item)
        ```
    """

    def __init__(
        self,
        on_batch: Callable[[list[Any]], asyncio.Future[Any]],
        max_queue_size: int | None = None,
        batch_size: int | None = None,
        batch_timeout_seconds: float | None = None,
        on_error: Callable[[Exception], None] | None = None,
        drop_on_full: bool = False,
    ) -> None:
        self._max_queue_size = max_queue_size or int(os.getenv("PIPELINE_MAX_QUEUE_SIZE", "1000"))
        self._batch_size = batch_size or int(os.getenv("PIPELINE_BATCH_SIZE", "100"))
        self._batch_timeout = batch_timeout_seconds or float(
            os.getenv("PIPELINE_BATCH_TIMEOUT", "5.0")
        )
        self._on_batch = on_batch
        self._on_error = on_error
        self._drop_on_full = drop_on_full

        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._max_queue_size)
        self._running = False
        self._shutdown = False
        self._consumer_task: asyncio.Task[None] | None = None
        self._flush_event = asyncio.Event()

        # Metrics
        self._items_processed = 0
        self._batches_processed = 0
        self._total_processing_time = 0.0
        self._dropped_items = 0
        self._lock = asyncio.Lock()

    @property
    def queue_size(self) -> int:
        """Current number of items in the queue."""
        return self._queue.qsize()

    @property
    def is_running(self) -> bool:
        """Check if the pipeline is running."""
        return self._running

    @property
    def max_queue_size(self) -> int:
        """Maximum queue size."""
        return self._max_queue_size

    async def put(self, item: Any, timeout: float = 1.0) -> bool:
        """Add an item to the pipeline.

        Args:
            item: The item to add.
            timeout: Max seconds to wait if queue is full.

        Returns:
            bool: True if item was added, False if dropped or timed out.

        Raises:
            PipelineShutdownError: If pipeline is shutting down.
        """
        if self._shutdown:
            raise PipelineShutdownError("Pipeline is shutting down")

        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            if self._drop_on_full:
                async with self._lock:
                    self._dropped_items += 1
                return False
            try:
                await asyncio.wait_for(self._queue.put(item), timeout=timeout)
                return True
            except TimeoutError:
                async with self._lock:
                    self._dropped_items += 1
                return False

    async def put_many(self, items: list[Any]) -> int:
        """Add multiple items to the pipeline.

        Args:
            items: List of items to add.

        Returns:
            int: Number of items successfully added.
        """
        added = 0
        for item in items:
            if await self.put(item):
                added += 1
        return added

    async def _process_batch(self, batch: list[Any]) -> None:
        """Process a batch of items.

        Args:
            batch: List of items to process.
        """
        if not batch:
            return

        start_time = asyncio.get_running_loop().time()

        try:
            await self._on_batch(batch)
        except Exception as exc:
            if self._on_error:
                self._on_error(exc)
            else:
                raise

        processing_time = (asyncio.get_running_loop().time() - start_time) * 1000

        async with self._lock:
            self._items_processed += len(batch)
            self._batches_processed += 1
            self._total_processing_time += processing_time

        # Signal flush completion when queue is empty
        if self._queue.empty():
            self._flush_event.set()

    async def _consumer_loop(self) -> None:
        """Main consumer loop that batches and processes items."""
        batch: list[Any] = []
        last_batch_time = asyncio.get_running_loop().time()

        while not self._shutdown:
            try:
                # Wait for items with timeout
                timeout_remaining = self._batch_timeout
                if batch:
                    elapsed = asyncio.get_running_loop().time() - last_batch_time
                    timeout_remaining = max(0, self._batch_timeout - elapsed)

                if timeout_remaining > 0:
                    try:
                        item = await asyncio.wait_for(
                            self._queue.get(),
                            timeout=timeout_remaining,
                        )
                        batch.append(item)
                    except TimeoutError:
                        pass  # Process partial batch on timeout
                else:
                    # No timeout remaining, process what we have
                    try:
                        item = self._queue.get_nowait()
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        pass

                # Process batch if full or timeout
                if batch and (
                    len(batch) >= self._batch_size
                    or asyncio.get_running_loop().time() - last_batch_time >= self._batch_timeout
                ):
                    await self._process_batch(batch)
                    batch = []
                    last_batch_time = asyncio.get_running_loop().time()

            except Exception as exc:
                if self._on_error:
                    self._on_error(exc)
                else:
                    raise

        # Process remaining items during shutdown
        # Drain the queue completely before exiting
        from contextlib import suppress

        # Process any partial batch
        if batch:
            with suppress(Exception):
                await self._process_batch(batch)
            batch = []

        # Drain all remaining items from the queue
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
                # Process batch when it reaches batch_size or queue is empty
                if len(batch) >= self._batch_size:
                    with suppress(Exception):
                        await self._process_batch(batch)
                    batch = []
            except asyncio.QueueEmpty:
                break

        # Process any final partial batch
        if batch:
            with suppress(Exception):
                await self._process_batch(batch)

    def start(self) -> None:
        """Start the pipeline consumer, allowing restarts after shutdown."""
        if self._running:
            return

        self._shutdown = False
        self._running = True
        self._consumer_task = asyncio.create_task(self._consumer_loop())

    async def stop(self, drain: bool = True) -> None:
        """Stop the pipeline gracefully.

        Args:
            drain: If True, process remaining items before stopping.
        """
        if not self._running:
            return

        self._shutdown = True

        if drain:
            # Wait for consumer to process remaining items
            if self._consumer_task:
                await self._consumer_task
        else:
            # Cancel consumer immediately
            if self._consumer_task:
                from contextlib import suppress

                self._consumer_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._consumer_task

        self._running = False

    def get_metrics(self) -> PipelineMetrics:
        """Get current pipeline metrics.

        Returns:
            PipelineMetrics: Current metrics.
        """
        avg_batch_size = (
            self._items_processed / self._batches_processed if self._batches_processed > 0 else 0.0
        )

        return PipelineMetrics(
            items_processed=self._items_processed,
            batches_processed=self._batches_processed,
            items_in_queue=self._queue.qsize(),
            avg_batch_size=avg_batch_size,
            processing_time_ms=self._total_processing_time,
            dropped_items=self._dropped_items,
        )


class BatchedWriter:
    """
    Convenience wrapper for writing batches to a destination.

    Provides a simple interface for writing batches of records with
    automatic batching based on count or time thresholds.

    Args:
        write_func: Async function to write a batch of records.
        batch_size: Number of records per batch. Default 100.
        flush_interval: Seconds between automatic flushes. Default 5.
        max_queue_size: Maximum queued records. Default 1000.

    Example:
        ```python
        async def write_to_db(records: list[dict]):
            await db.insert_many(records)

        writer = BatchedWriter(
            write_func=write_to_db,
            batch_size=100,
            flush_interval=5,
        )

        # Start writer
        writer.start()

        # Queue records for writing
        for record in data_stream:
            await writer.write(record)

        # Stop and flush
        await writer.stop()
        ```
    """

    def __init__(
        self,
        write_func: Callable[[list[Any]], asyncio.Future[Any]],
        batch_size: int = 100,
        flush_interval: float = 5.0,
        max_queue_size: int = 1000,
    ) -> None:
        self._write_func = write_func
        self._pipeline = ProducerConsumerPipeline(
            on_batch=write_func,
            max_queue_size=max_queue_size,
            batch_size=batch_size,
            batch_timeout_seconds=flush_interval,
        )

    @property
    def queue_size(self) -> int:
        """Current number of items queued for writing."""
        return self._pipeline.queue_size

    def start(self) -> None:
        """Start the batched writer."""
        self._pipeline.start()

    async def write(self, record: Any) -> bool:
        """Queue a record for writing.

        Args:
            record: The record to write.

        Returns:
            bool: True if queued, False if dropped.
        """
        return await self._pipeline.put(record)

    async def write_many(self, records: list[Any]) -> int:
        """Queue multiple records for writing.

        Args:
            records: List of records to write.

        Returns:
            int: Number of records queued.
        """
        return await self._pipeline.put_many(records)

    async def flush(self) -> None:
        """Force flush all queued records."""
        # Wait for consumer to drain queue using event-based signaling
        from contextlib import suppress

        while self._pipeline.queue_size > 0:
            self._pipeline._flush_event.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._pipeline._flush_event.wait(),
                    timeout=1.0,
                )

    async def stop(self) -> None:
        """Stop the writer and flush remaining records."""
        await self._pipeline.stop(drain=True)

    def get_metrics(self) -> PipelineMetrics:
        """Get writer metrics."""
        return self._pipeline.get_metrics()
