"""JSONL file writer with buffered async writes."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

import aiofiles

from async_patterns.persistence._buffered import BufferedStorageWriter
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


class JsonlWriter(BufferedStorageWriter, StorageWriter):
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
        super().__init__(buffer_size=config.buffer_size)
        self._config = config
        self._file: Any = None
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

    async def _open_target(self) -> None:
        """Open the output file for writing."""
        self._file = await aiofiles.open(
            self._config.file_path,
            mode="w",
            encoding="utf-8",
            newline="\n",
        )
        # Get the file descriptor for fsync
        self._file_descriptor = self._file.fileno()

    async def _write_records(self, records: list[dict[str, Any]]) -> None:
        """Write normalized records as JSON lines."""
        if not self._file:
            return

        for record in records:
            await self._file.write(json.dumps(record, ensure_ascii=False) + "\n")

        await self._file.flush()
        if self._file_descriptor is not None:
            os.fsync(self._file_descriptor)

    async def _close_target(self) -> None:
        """Close the JSONL file handle."""
        if self._file:
            await self._file.close()
            self._file = None
            self._file_descriptor = None
