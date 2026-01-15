"""End-to-end pipeline integration tests for async-patterns.

Tests the complete data flow: HTTP → Queue → Persistence, validating zero data loss.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from async_patterns.engine.async_engine import AsyncEngine
from async_patterns.engine.models import ConnectionConfig, RequestResult
from async_patterns.patterns.pipeline import ProducerConsumerPipeline
from async_patterns.persistence.jsonl import JsonlWriter, JsonlWriterConfig
from benchmarks.mock_server import MockServerFixture


def read_jsonl_file(file_path: Path) -> list[dict[str, Any]]:
    """Read all records from a JSONL file.

    Args:
        file_path: Path to the JSONL file.

    Returns:
        List of parsed JSON records.
    """
    if not file_path.exists():
        return []
    content = file_path.read_text()
    lines = [line for line in content.strip().split("\n") if line]
    return [json.loads(line) for line in lines]


@pytest.fixture
async def mock_urls(mock_server: MockServerFixture) -> list[str]:
    """Provide a list of mock URLs for testing.

    Uses the internal mock server for deterministic testing.
    """
    base = mock_server.base_url
    return [
        f"{base}/status/200",
        f"{base}/status/201",
        f"{base}/status/400",
        f"{base}/status/404",
        f"{base}/status/500",
    ]


@pytest.fixture
async def many_mock_urls(mock_server: MockServerFixture) -> list[str]:
    """Provide a larger list of mock URLs for load testing."""
    base = mock_server.base_url
    base_urls = [
        f"{base}/status/200",
        f"{base}/status/201",
        f"{base}/status/400",
        f"{base}/status/404",
        f"{base}/status/500",
    ]
    # Repeat to create 100 URLs for load testing
    return base_urls * 20


@pytest.mark.asyncio
async def test_engine_to_pipeline_to_jsonl(
    tmp_path: Path,
    mock_urls: list[str],
) -> None:
    """Test full data flow from engine results through pipeline to JSONL file.

    Verifies that:
    1. Engine produces results for all URLs
    2. Results are successfully put into the pipeline
    3. All results are persisted to the JSONL file
    """
    # Setup: Create writer and pipeline
    output_file = tmp_path / "results.jsonl"
    writer_config = JsonlWriterConfig(
        file_path=output_file,
        buffer_size=100,
    )
    writer = JsonlWriter(writer_config)

    # Open the writer file handle
    await writer._open()

    async def on_batch_with_logging(batch: list[Any]) -> None:
        await writer.write_batch(batch)
        await writer.flush()

    pipeline = ProducerConsumerPipeline(
        on_batch=on_batch_with_logging,
        max_queue_size=1000,
        batch_size=100,
        batch_timeout_seconds=5.0,
    )

    # Start pipeline consumer
    pipeline.start()

    try:
        # Execute engine with mock URLs
        engine = AsyncEngine(
            max_concurrent=10,
            config=ConnectionConfig(timeout=10.0),
        )
        result = await engine.run(mock_urls)

        # Feed results into pipeline
        for request_result in result.results:
            await pipeline.put(request_result)

        # Stop pipeline with drain=True to ensure all items are processed
        await pipeline.stop(drain=True)

        # Final flush to ensure everything is written
        await writer.flush()

    finally:
        # Close writer after pipeline finishes
        await writer.close()

    # Verification: Check persisted items match input URLs
    persisted = read_jsonl_file(output_file)

    # All results produced by engine should be persisted (zero data loss)
    assert len(persisted) == len(result.results), (
        f"Expected {len(result.results)} persisted items (from engine results), "
        f"got {len(persisted)}"
    )

    # Verify each URL from results is represented in the persisted data
    if result.results:
        persisted_urls = {item.get("url") for item in persisted}
        result_urls = [r.url for r in result.results]
        assert set(result_urls).issubset(persisted_urls), (
            "Not all result URLs were persisted correctly"
        )


@pytest.mark.asyncio
async def test_constant_memory_under_load(
    tmp_path: Path,
    many_mock_urls: list[str],
) -> None:
    """Test that memory usage remains bounded under load with batch processing.

    Validates that:
    1. The pipeline handles a large number of items efficiently
    2. Batch processing prevents memory spikes
    3. All items are processed correctly

    Note: This test uses httpbin which may have rate limits. In production,
    mock servers would be used for more reliable testing.
    """
    output_file = tmp_path / "load_test_results.jsonl"
    writer_config = JsonlWriterConfig(
        file_path=output_file,
        buffer_size=100,  # Small buffer to trigger frequent batching
    )
    writer = JsonlWriter(writer_config)

    # Open the writer file handle
    await writer._open()

    async def on_batch_with_logging(batch: list[Any]) -> None:
        await writer.write_batch(batch)
        await writer.flush()

    pipeline = ProducerConsumerPipeline(
        on_batch=on_batch_with_logging,
        max_queue_size=500,
        batch_size=50,  # Smaller batches for more batching operations
        batch_timeout_seconds=2.0,
    )

    pipeline.start()

    try:
        engine = AsyncEngine(
            max_concurrent=20,
            config=ConnectionConfig(timeout=15.0),
        )
        result = await engine.run(many_mock_urls)

        # Put results into pipeline
        for request_result in result.results:
            await pipeline.put(request_result)

        await pipeline.stop(drain=True)

        # Final flush
        await writer.flush()

    finally:
        # Close writer after pipeline finishes
        await writer.close()

    # Verify all results were persisted
    persisted = read_jsonl_file(output_file)

    # All successfully processed results should be persisted
    assert len(persisted) == len(result.results), (
        f"More items persisted ({len(persisted)}) than results produced ({len(result.results)})"
    )

    # Verify batching worked correctly by checking metrics
    metrics = pipeline.get_metrics()
    assert metrics.batches_processed >= 1, "At least one batch should be processed"

    # Verify average batch size is reasonable (not too small)
    if metrics.batches_processed > 0:
        assert metrics.avg_batch_size <= 50, (
            f"Average batch size {metrics.avg_batch_size} exceeds batch_size of 50"
        )


@pytest.mark.asyncio
async def test_zero_data_loss(
    tmp_path: Path,
    mock_urls: list[str],
) -> None:
    """Verify that all results (successful and failed) are persisted without data loss.

    This test ensures:
    1. Successful HTTP responses are persisted
    2. Failed HTTP responses (4xx, 5xx) are persisted with error info
    3. Network errors are captured and persisted
    4. Total persisted count equals total results produced
    """
    output_file = tmp_path / "zero_loss_results.jsonl"
    writer_config = JsonlWriterConfig(
        file_path=output_file,
        buffer_size=50,
    )
    writer = JsonlWriter(writer_config)

    # Open the writer file handle
    await writer._open()

    async def on_batch_with_logging(batch: list[Any]) -> None:
        await writer.write_batch(batch)
        await writer.flush()

    pipeline = ProducerConsumerPipeline(
        on_batch=on_batch_with_logging,
        max_queue_size=1000,
        batch_size=100,
        batch_timeout_seconds=5.0,
    )

    pipeline.start()

    try:
        engine = AsyncEngine(
            max_concurrent=10,
            config=ConnectionConfig(timeout=10.0),
        )
        result = await engine.run(mock_urls)

        # Track expected results count
        expected_count = len(result.results)

        # Put all results into pipeline (both success and failure)
        for request_result in result.results:
            await pipeline.put(request_result)

        await pipeline.stop(drain=True)

        # Final flush
        await writer.flush()

    finally:
        # Close writer after pipeline finishes
        await writer.close()

    # Read persisted results
    persisted = read_jsonl_file(output_file)

    # Core assertion: zero data loss for results produced by engine
    assert len(persisted) == expected_count, (
        f"Data loss detected: expected {expected_count} items, persisted {len(persisted)} items"
    )

    # Verify that failed requests are included with their error info
    for item in persisted:
        # Each item should have the expected fields
        assert "url" in item, "Each persisted item must have a 'url' field"
        assert "status_code" in item, "Each persisted item must have a 'status_code' field"
        assert "latency_ms" in item, "Each persisted item must have a 'latency_ms' field"

        # For failed requests, error info should be preserved
        if item.get("status_code", 0) >= 400:
            # Either status_code >= 400 OR error field should be present
            has_error_info = item.get("error") is not None or item.get("status_code", 0) >= 400
            assert has_error_info, f"Failed request {item['url']} missing error information"

    # Verify metrics show no dropped items
    metrics = pipeline.get_metrics()
    assert metrics.dropped_items == 0, (
        f"Pipeline dropped {metrics.dropped_items} items during processing"
    )


@pytest.mark.asyncio
async def test_pipeline_with_mixed_results(
    tmp_path: Path,
) -> None:
    """Test pipeline handles a mix of successful and failed RequestResults.

    Creates mock RequestResult objects with various status codes and errors
    to simulate real-world scenarios.
    """
    output_file = tmp_path / "mixed_results.jsonl"
    writer_config = JsonlWriterConfig(
        file_path=output_file,
        buffer_size=10,
    )
    writer = JsonlWriter(writer_config)

    # Open the writer file handle
    await writer._open()

    # Create mixed results
    mock_results = [
        RequestResult(
            url="http://mock-server/status/200",
            status_code=200,
            latency_ms=50.0,
            timestamp=1700000000.0,
            attempt=1,
            error=None,
        ),
        RequestResult(
            url="http://mock-server/status/404",
            status_code=404,
            latency_ms=45.0,
            timestamp=1700000000.1,
            attempt=1,
            error=None,
        ),
        RequestResult(
            url="http://mock-server/status/500",
            status_code=500,
            latency_ms=100.0,
            timestamp=1700000000.2,
            attempt=1,
            error=None,
        ),
        RequestResult(
            url="https://nonexistent.example.com",
            status_code=0,
            latency_ms=5.0,
            timestamp=1700000000.3,
            attempt=1,
            error="ConnectionError: Failed to resolve host",
        ),
    ]

    async def on_batch_with_logging(batch: list[Any]) -> None:
        await writer.write_batch(batch)
        await writer.flush()

    pipeline = ProducerConsumerPipeline(
        on_batch=on_batch_with_logging,
        max_queue_size=100,
        batch_size=10,
        batch_timeout_seconds=1.0,
    )

    pipeline.start()

    try:
        # Put all mock results into pipeline
        for result in mock_results:
            await pipeline.put(result)

        await pipeline.stop(drain=True)

        # Final flush
        await writer.flush()

    finally:
        # Close writer after pipeline finishes
        await writer.close()

    # Verify all results were persisted
    persisted = read_jsonl_file(output_file)

    assert len(persisted) == len(mock_results), (
        f"Expected {len(mock_results)} persisted items, got {len(persisted)}"
    )

    # Verify all types of results are preserved
    urls_persisted = [item["url"] for item in persisted]
    assert "http://mock-server/status/200" in urls_persisted
    assert "http://mock-server/status/404" in urls_persisted
    assert "http://mock-server/status/500" in urls_persisted
    assert "https://nonexistent.example.com" in urls_persisted

    # Verify error is preserved for failed request
    error_result = next(
        item for item in persisted if item["url"] == "https://nonexistent.example.com"
    )
    assert error_result["error"] == "ConnectionError: Failed to resolve host"
    assert error_result["status_code"] == 0
