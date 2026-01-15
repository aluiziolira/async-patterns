"""Base Protocols for benchmark engines."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from async_patterns.engine.models import EngineResult


@runtime_checkable
class SyncEngine(Protocol):
    """Protocol defining the interface for synchronous benchmark engines."""

    @property
    def name(self) -> str:
        """Name of the engine implementation.

        Returns:
            A string identifier for the engine (e.g., "sync", "threaded", "async").
        """
        ...

    def run(self, urls: list[str]) -> EngineResult:
        """Execute HTTP requests for the given URLs.

        Args:
            urls: List of URLs to request.

        Returns:
            An EngineResult containing all individual request results and aggregate metrics.
        """
        ...


@runtime_checkable
class AsyncEngine(Protocol):
    """Protocol defining the interface for asynchronous benchmark engines."""

    @property
    def name(self) -> str:
        """Name of the engine implementation."""
        ...

    async def run(self, urls: list[str]) -> EngineResult:
        """Execute HTTP requests for the given URLs."""
        ...


type Engine = SyncEngine | AsyncEngine
