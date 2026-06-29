"""Shared buffering and record normalization for persistence writers."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any, cast

from async_patterns.persistence.base import StorageWriter


class BufferedStorageWriter(StorageWriter):
    """Base module for writers that buffer normalized records before flushing."""

    def __init__(self, buffer_size: int) -> None:
        self._buffer_size = buffer_size
        self._buffer: list[dict[str, Any]] = []
        self._closed = False
        self._opened = False
        self._lock = asyncio.Lock()

    async def write(self, record: Any) -> None:
        """Normalize and buffer one record, flushing when the buffer is full."""
        if self._closed:
            raise RuntimeError("Cannot write to closed writer")

        async with self._lock:
            await self._ensure_open_locked()
            self._buffer.append(normalize_record(record))
            if len(self._buffer) >= self._buffer_size:
                await self._flush_locked()

    async def write_batch(self, records: list[Any]) -> int:
        """Write multiple records. Returns count written."""
        if self._closed:
            raise RuntimeError("Cannot write to closed writer")

        for record in records:
            await self.write(record)
        return len(records)

    async def flush(self) -> None:
        """Flush buffered records to the destination adapter."""
        async with self._lock:
            await self._flush_locked()

    async def close(self) -> None:
        """Flush, close the destination adapter, and make the writer immutable."""
        if self._closed:
            return

        async with self._lock:
            await self._flush_locked()
            self._closed = True
            await self._close_target()
            self._opened = False

    async def _open(self) -> None:
        """Open the destination adapter if needed."""
        async with self._lock:
            await self._ensure_open_locked()

    async def _ensure_open_locked(self) -> None:
        if not self._opened:
            await self._open_target()
            self._opened = True

    async def _flush_locked(self) -> None:
        if not self._opened or not self._buffer:
            return

        records = list(self._buffer)
        await self._write_records(records)
        self._buffer.clear()

    async def _open_target(self) -> None:
        raise NotImplementedError

    async def _write_records(self, records: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    async def _close_target(self) -> None:
        raise NotImplementedError


def normalize_record(record: Any) -> dict[str, Any]:
    """Convert supported record shapes into plain dictionaries."""
    if dataclasses.is_dataclass(record) and not isinstance(record, type):
        return dataclasses.asdict(record)
    if hasattr(record, "__dataclass_fields__"):
        if hasattr(record, "__dict__"):
            return cast(dict[str, Any], record.__dict__)
        return dict(record)
    return cast(dict[str, Any], record)
