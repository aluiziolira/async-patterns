"""Base Protocol for benchmark engines.

This module defines the Engine protocol that all engine implementations must follow.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from async_patterns.engine.models import EngineResult


@runtime_checkable
class Engine(Protocol):
    """Protocol defining the interface for benchmark engines.

    All engine implementations (sync, threaded, async) must conform to this protocol.
    The @runtime_checkable decorator enables isinstance() checks for protocol conformance.

    Attributes:
        name: A string identifier for the engine type (e.g., "sync", "threaded").

    Example:
        >>> from async_patterns.engine.base import Engine
        >>> from async_patterns.engine import SyncEngine
        >>> isinstance(SyncEngine(), Engine)
        True
    """

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
