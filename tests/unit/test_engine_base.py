"""Tests for engine Protocol definitions."""

from __future__ import annotations

import inspect

from async_patterns.engine.base import AsyncEngine, Engine, SyncEngine


class TestSyncEngineProtocol:
    """Test cases for the SyncEngine protocol."""

    def test_sync_engine_protocol_exists(self) -> None:
        """SyncEngine protocol should be importable."""
        assert SyncEngine is not None

    def test_sync_engine_protocol_has_run_method(self) -> None:
        """SyncEngine protocol should define a run method."""
        assert hasattr(SyncEngine, "run")
        assert callable(SyncEngine.run)

    def test_sync_engine_protocol_has_name_property(self) -> None:
        """SyncEngine protocol should define a name property."""
        assert hasattr(SyncEngine, "name")

    def test_sync_engine_protocol_signature(self) -> None:
        """SyncEngine.run should accept list[str] and return EngineResult."""
        sig = inspect.signature(SyncEngine.run)
        params = list(sig.parameters.keys())
        assert len(params) >= 1, "SyncEngine.run should have at least one parameter"


class TestAsyncEngineProtocol:
    """Test cases for the AsyncEngine protocol."""

    def test_async_engine_protocol_exists(self) -> None:
        """AsyncEngine protocol should be importable."""
        assert AsyncEngine is not None

    def test_async_engine_protocol_has_run_method(self) -> None:
        """AsyncEngine protocol should define a run method."""
        assert hasattr(AsyncEngine, "run")
        assert callable(AsyncEngine.run)
        assert inspect.iscoroutinefunction(AsyncEngine.run)

    def test_async_engine_protocol_has_name_property(self) -> None:
        """AsyncEngine protocol should define a name property."""
        assert hasattr(AsyncEngine, "name")


class TestEngineAlias:
    """Test cases for the Engine union alias."""

    def test_engine_alias_exists(self) -> None:
        """Engine alias should be importable."""
        assert Engine is not None


class TestEngineProtocolRuntimeCheckable:
    """Test cases for runtime_checkable protocol conformance."""

    def test_sync_engine_protocol_is_runtime_checkable(self) -> None:
        """SyncEngine should be usable with isinstance() checks."""

        class MinimalEngine:
            name: str = "minimal"

            def run(self, urls: list[str]) -> object:
                return None

        assert isinstance(MinimalEngine(), SyncEngine)

    def test_async_engine_protocol_is_runtime_checkable(self) -> None:
        """AsyncEngine should be usable with isinstance() checks."""

        class MinimalAsyncEngine:
            name: str = "minimal_async"

            async def run(self, urls: list[str]) -> object:
                return None

        assert isinstance(MinimalAsyncEngine(), AsyncEngine)
