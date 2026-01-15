"""JSONL file writer with buffered async writes."""

import dataclasses
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

import aiofiles

from async_patterns.persistence.base import StorageWriter


@dataclass
class JsonlWriterConfig:
    """Configuration for JsonlWriter.

    Attributes:
        file_path: Path to the JSONL output file.
        buffer_size: Number of records to buffer before auto-flush.
        flush_interval: Seconds between automatic flushes.
    """

    file_path: Path
    buffer_size: int = 100
    flush_interval: float = 5.0


class JsonlWriter(StorageWriter):
    """JSONL file writer with buffered async writes.

    Batches records in memory and flushes to disk when the buffer reaches
    `buffer_size` records or when explicitly flushed.

    Supports serialization of dataclasses like RequestResult via
    `dataclasses.asdict()`.

    Example:
        ```python
        async with JsonlWriter(JsonlWriterConfig("output.jsonl")) as writer:
            await writer.write({"url": "https://example.com", "status": 200})
            await writer.write_batch([...])
        ```
    """

    def __init__(self, config: JsonlWriterConfig) -> None:
        """Initialize the JSONL writer.

        Args:
            config: Writer configuration.
        """
        self._config = config
        self._buffer: list[str] = []
        self._file: Any = None
        self._closed = False
        self._file_descriptor: int | None = None

    async def __aenter__(self) -> Self:
        """Enter async context manager and open the file.

        Returns:
            Self for context manager protocol.
        """
        await self._open()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit async context manager and close the file.

        Args:
            exc_type: Exception type if an error occurred.
            exc_val: Exception value if an error occurred.
            exc_tb: Exception traceback if an error occurred.
        """
        await self.close()

    async def _open(self) -> None:
        """Open the output file for writing."""
        self._file = await aiofiles.open(
            self._config.file_path,
            mode="w",
            encoding="utf-8",
            newline="\n",
        )
        # Get the file descriptor for fsync
        self._file_descriptor = self._file.fileno()

    async def write(self, record: dict[str, Any]) -> None:
        """Write a single record to the JSONL file.

        Serializes to JSON and appends to internal buffer. Auto-flushes when
        buffer reaches `buffer_size`.

        Args:
            record: Dictionary to write as a JSON line.
        """
        if self._closed:
            raise RuntimeError("Cannot write to closed writer")

        if self._file is None:
            await self._open()

        if dataclasses.is_dataclass(record) and not isinstance(record, type):
            record_dict = dataclasses.asdict(record)
        elif hasattr(record, "__dataclass_fields__"):
            record_dict = record.__dict__ if hasattr(record, "__dict__") else dict(record)
        else:
            record_dict = record

        json_line = json.dumps(record_dict, ensure_ascii=False)
        self._buffer.append(json_line)

        if len(self._buffer) >= self._config.buffer_size:
            await self.flush()

    async def write_batch(self, records: list[dict[str, Any]]) -> int:
        """Write multiple records to the JSONL file.

        Adds all records to the buffer and then flushes.

        Args:
            records: List of dictionaries to write.

        Returns:
            Number of records written.
        """
        if self._closed:
            raise RuntimeError("Cannot write to closed writer")

        for record in records:
            await self.write(record)

        return len(records)

    async def flush(self) -> None:
        """Flush all buffered records to disk.

        Writes all buffered JSON lines to the file and clears the buffer.
        Forces write to disk using fsync.
        """
        if not self._file or not self._buffer:
            return

        for line in self._buffer:
            await self._file.write(line + "\n")

        await self._file.flush()
        if self._file_descriptor is not None:
            os.fsync(self._file_descriptor)

        self._buffer.clear()

    async def close(self) -> None:
        """Close the writer and release resources.

        Flushes any remaining buffered data and closes the file handle.
        """
        if self._closed:
            return

        await self.flush()

        self._closed = True

        if self._file:
            await self._file.close()
            self._file = None
            self._file_descriptor = None
