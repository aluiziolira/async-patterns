"""AsyncEngine Protocol for async benchmark engines.

This module defines the AsyncEngine protocol that async engine implementations
must follow. Unlike the base Engine protocol, AsyncEngine uses an async run()
method for proper async/await support.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from async_patterns.engine.models import EngineResult


@runtime_checkable
class AsyncEngine(Protocol):
    """Protocol defining the interface for async benchmark engines.

    All async engine implementations must conform to this protocol.
    The @runtime_checkable decorator enables isinstance() checks for protocol conformance.

    Attributes:
        name: A string identifier for the engine type (e.g., "async", "asyncio").

    Example:
        >>> from async_patterns.engine.async_base import AsyncEngine
        >>> from async_patterns.engine import AsyncPattern
        >>> isinstance(AsyncPattern(), AsyncEngine)
        True
    """

    @property
    def name(self) -> str:
        """Name of the async engine implementation.

        Returns:
            A string identifier for the engine (e.g., "async", "asyncio").
        """
        ...

    async def run(self, urls: list[str]) -> EngineResult:
        """Execute HTTP requests asynchronously for the given URLs.

        This method should be implemented as an async function that performs
        concurrent HTTP requests using async/await patterns.

        Args:
            urls: List of URLs to request.

        Returns:
            An EngineResult containing all individual request results and aggregate metrics.
        """
        ...
