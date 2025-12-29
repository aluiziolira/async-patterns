"""Tests for the AsyncEngine Protocol.

These tests verify the AsyncEngine protocol has the correct signature and
supports runtime_checkable for isinstance checks. The AsyncEngine protocol
is similar to Engine but with an async run() method.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


class TestAsyncEngineProtocol:
    """Test cases for the AsyncEngine protocol."""

    def test_async_engine_protocol_exists(self) -> None:
        """AsyncEngine protocol should be importable."""
        from async_patterns.engine.async_base import AsyncEngine

        assert AsyncEngine is not None

    def test_async_engine_protocol_has_run_method(self) -> None:
        """AsyncEngine protocol should define a run method."""
        from async_patterns.engine.async_base import AsyncEngine

        assert hasattr(AsyncEngine, "run")
        # run should be a callable/protocol member
        run_method = AsyncEngine.run
        assert callable(run_method)

    def test_async_engine_protocol_has_name_property(self) -> None:
        """AsyncEngine protocol should define a name property."""
        from async_patterns.engine.async_base import AsyncEngine

        assert hasattr(AsyncEngine, "name")

    def test_async_engine_protocol_signature(self) -> None:
        """AsyncEngine.run should accept list[str] and return EngineResult."""
        import inspect

        from async_patterns.engine.async_base import AsyncEngine

        # Get the run method signature
        sig = inspect.signature(AsyncEngine.run)
        params = list(sig.parameters.keys())

        # First param should be 'self' or empty for protocol
        # Second param should be 'urls' of type list[str]
        assert len(params) >= 1, "AsyncEngine.run should have at least one parameter"

    def test_async_engine_run_is_coroutine_function(self) -> None:
        """AsyncEngine.run should be an async method (coroutine function)."""
        from async_patterns.engine.async_base import AsyncEngine

        # Check that run is a coroutine function
        assert asyncio.iscoroutinefunction(AsyncEngine.run)


class TestAsyncEngineProtocolRuntimeCheckable:
    """Test cases for AsyncEngine protocol runtime checking."""

    def test_async_engine_protocol_is_runtime_checkable(self) -> None:
        """AsyncEngine should be usable with isinstance() checks."""
        from async_patterns.engine.async_base import AsyncEngine

        # Create a minimal class that implements the protocol
        class MinimalAsyncEngine:
            name: str = "minimal_async"

            async def run(self, urls: list[str]) -> Any:
                return None

        # MinimalAsyncEngine should be considered an instance of AsyncEngine
        assert isinstance(MinimalAsyncEngine(), AsyncEngine)

    def test_non_engine_class_is_not_instance(self) -> None:
        """Classes that don't implement the protocol should not be instances."""
        from async_patterns.engine.async_base import AsyncEngine

        class NotAnAsyncEngine:
            pass

        assert not isinstance(NotAnAsyncEngine(), AsyncEngine)

    def test_partial_implementation_is_not_instance(self) -> None:
        """Classes missing required methods should not be instances."""
        from async_patterns.engine.async_base import AsyncEngine

        class PartialAsyncEngine:
            name: str = "partial"

            # Missing run method or run is not async

        assert not isinstance(PartialAsyncEngine(), AsyncEngine)


class TestAsyncEngineProtocolIntegration:
    """Integration tests for AsyncEngine protocol with actual usage."""

    @pytest.mark.asyncio()
    async def test_conforming_async_engine_can_be_used(self) -> None:
        """A class conforming to AsyncEngine can be instantiated and used."""
        from async_patterns.engine.async_base import AsyncEngine
        from async_patterns.engine.models import EngineResult, RequestResult

        # Create a concrete implementation for testing
        class ConcreteAsyncEngine:
            @property
            def name(self) -> str:
                return "concrete_async"

            async def run(self, urls: list[str]) -> EngineResult:
                # Return a mock result
                results = [
                    RequestResult(
                        url=url,
                        status_code=200,
                        latency_ms=100.0,
                        timestamp=0.0,
                        attempt=1,
                        error=None,
                    )
                    for url in urls
                ]
                return EngineResult(
                    results=results,
                    total_time=0.5,
                    peak_memory_mb=10.0,
                )

        engine = ConcreteAsyncEngine()
        # Verify it passes isinstance check
        assert isinstance(engine, AsyncEngine)

        # Verify it can be used
        result = await engine.run(["http://example.com"])
        assert result is not None
        assert len(result.results) == 1
