"""Unit tests for JsonlWriter."""

import json
import tempfile
from pathlib import Path

import pytest

from async_patterns.engine.models import RequestResult
from async_patterns.persistence.jsonl import JsonlWriter, JsonlWriterConfig


class TestJsonlWriter:
    """Test suite for JsonlWriter class."""

    @pytest.fixture
    def temp_dir(self) -> Path:
        """Create a temporary directory for test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def temp_file(self, temp_dir: Path) -> Path:
        """Create a temporary file path."""
        return temp_dir / "test_output.jsonl"

    @pytest.mark.asyncio
    async def test_write_single_record(self, temp_file: Path) -> None:
        """Write one record and verify file content."""
        config = JsonlWriterConfig(file_path=temp_file, buffer_size=10)
        record = {"url": "https://example.com", "status": 200, "latency_ms": 45.5}

        async with JsonlWriter(config) as writer:
            await writer.write(record)

        # Verify file content
        content = temp_file.read_text()
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 1

        parsed = json.loads(lines[0])
        assert parsed["url"] == "https://example.com"
        assert parsed["status"] == 200
        assert parsed["latency_ms"] == 45.5

    @pytest.mark.asyncio
    async def test_write_batch(self, temp_file: Path) -> None:
        """Write multiple records in single call."""
        config = JsonlWriterConfig(file_path=temp_file, buffer_size=100)
        records = [
            {"url": "https://api.com/1", "status": 200},
            {"url": "https://api.com/2", "status": 201},
            {"url": "https://api.com/3", "status": 404},
        ]

        async with JsonlWriter(config) as writer:
            count = await writer.write_batch(records)
            assert count == 3

        # Verify file content
        content = temp_file.read_text()
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 3

        for i, line in enumerate(lines):
            parsed = json.loads(line)
            assert parsed["url"] == f"https://api.com/{i + 1}"
            if i == 0:
                assert parsed["status"] == 200
            elif i == 1:
                assert parsed["status"] == 201
            else:
                assert parsed["status"] == 404

    @pytest.mark.asyncio
    async def test_buffer_flush_on_size(self, temp_file: Path) -> None:
        """Auto-flush when buffer reaches limit."""
        buffer_size = 5
        config = JsonlWriterConfig(
            file_path=temp_file,
            buffer_size=buffer_size,
        )

        async with JsonlWriter(config) as writer:
            # Write exactly buffer_size records to trigger flush
            for i in range(buffer_size):
                await writer.write({"id": i})

            # Write more records after flush
            for i in range(buffer_size, buffer_size + 2):
                await writer.write({"id": i})

        # Verify all records were written
        content = temp_file.read_text()
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == buffer_size + 2

    @pytest.mark.asyncio
    async def test_manual_flush(self, temp_file: Path) -> None:
        """Manual flush writes buffered records to disk."""
        config = JsonlWriterConfig(
            file_path=temp_file,
            buffer_size=100,  # Large buffer
        )

        async with JsonlWriter(config) as writer:
            await writer.write({"id": 1})
            await writer.write({"id": 2})

            # File should be empty before flush
            assert temp_file.read_text().strip() == ""

            # Manual flush
            await writer.flush()

            # File should now have content
            content = temp_file.read_text()
            lines = [line for line in content.strip().split("\n") if line]
            assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_context_manager_cleanup(self, temp_file: Path) -> None:
        """Proper resource cleanup on exit."""
        config = JsonlWriterConfig(file_path=temp_file)

        async with JsonlWriter(config) as writer:
            await writer.write({"data": "test"})

        # Verify file was created with content
        assert temp_file.exists()

        # Verify content is correct
        content = temp_file.read_text()
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 1

        parsed = json.loads(lines[0])
        assert parsed["data"] == "test"

    @pytest.mark.asyncio
    async def test_write_request_result(self, temp_file: Path) -> None:
        """Serialize RequestResult dataclass."""
        config = JsonlWriterConfig(file_path=temp_file, buffer_size=10)

        # Create a RequestResult instance
        result = RequestResult(
            url="https://httpbin.org/get",
            status_code=200,
            latency_ms=123.45,
            timestamp=1700000000.0,
            attempt=1,
            error=None,
        )

        async with JsonlWriter(config) as writer:
            await writer.write(result)

        # Verify the serialized content
        content = temp_file.read_text()
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 1

        parsed = json.loads(lines[0])
        assert parsed["url"] == "https://httpbin.org/get"
        assert parsed["status_code"] == 200
        assert parsed["latency_ms"] == 123.45
        assert parsed["timestamp"] == 1700000000.0
        assert parsed["attempt"] == 1
        assert parsed["error"] is None

    @pytest.mark.asyncio
    async def test_write_to_closed_writer_raises(self, temp_file: Path) -> None:
        """Writing to a closed writer raises RuntimeError."""
        config = JsonlWriterConfig(file_path=temp_file)
        writer = JsonlWriter(config)

        # Open and close the writer
        async with writer:
            pass

        # Attempting to write after close should raise
        with pytest.raises(RuntimeError, match="Cannot write to closed writer"):
            await writer.write({"data": "test"})

    @pytest.mark.asyncio
    async def test_write_batch_to_closed_writer_raises(self, temp_file: Path) -> None:
        """write_batch to a closed writer raises RuntimeError."""
        config = JsonlWriterConfig(file_path=temp_file)
        writer = JsonlWriter(config)

        # Open and close the writer
        async with writer:
            pass

        # Attempting to write_batch after close should raise
        with pytest.raises(RuntimeError, match="Cannot write to closed writer"):
            await writer.write_batch([{"data": "test"}])

    @pytest.mark.asyncio
    async def test_flush_no_op_when_empty_buffer(self, temp_file: Path) -> None:
        """Flush when buffer is empty is a no-op."""
        config = JsonlWriterConfig(file_path=temp_file)

        async with JsonlWriter(config) as writer:
            # Flush with empty buffer should not raise
            await writer.flush()

        # File should exist but be empty
        assert temp_file.exists()
        assert temp_file.read_text() == ""

    @pytest.mark.asyncio
    async def test_close_idempotent(self, temp_file: Path) -> None:
        """Calling close multiple times is safe."""
        config = JsonlWriterConfig(file_path=temp_file)
        writer = JsonlWriter(config)

        async with writer:
            await writer.write({"data": "test1"})

        # First close
        await writer.close()

        # Second close should be safe (no-op)
        await writer.close()

        # Content should still be correct
        content = temp_file.read_text()
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["data"] == "test1"
