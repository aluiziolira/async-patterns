"""Base protocols for persistence layer."""

from typing import Any, Protocol


class StorageWriter(Protocol):
    """Protocol for persistent storage backends."""

    async def write(self, record: dict[str, Any]) -> None:
        """Write a single record."""
        ...

    async def write_batch(self, records: list[dict[str, Any]]) -> int:
        """Write multiple records. Returns count written."""
        ...

    async def flush(self) -> None:
        """Ensure all buffered data is persisted."""
        ...

    async def close(self) -> None:
        """Close the writer and release resources."""
        ...
