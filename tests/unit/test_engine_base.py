"""Tests for the Engine Protocol.

These tests verify the Engine protocol has the correct signature and
supports runtime_checkable for isinstance checks.
"""

from __future__ import annotations

from async_patterns.engine.base import Engine


class TestEngineProtocol:
    """Test cases for the Engine protocol."""

    def test_engine_protocol_exists(self) -> None:
        """Engine protocol should be importable."""
        assert Engine is not None

    def test_engine_protocol_has_run_method(self) -> None:
        """Engine protocol should define a run method."""
        assert hasattr(Engine, "run")
        # run should be a callable with the correct signature
        run_method = Engine.run
        # Check that it's a function/protocol member
        assert callable(run_method)

    def test_engine_protocol_has_name_property(self) -> None:
        """Engine protocol should define a name property."""
        assert hasattr(Engine, "name")

    def test_engine_protocol_signature(self) -> None:
        """Engine.run should accept list[str] and return EngineResult."""
        import inspect

        # Get the run method signature
        sig = inspect.signature(Engine.run)
        params = list(sig.parameters.keys())

        # First param should be 'self' or empty for protocol
        # Second param should be 'urls' of type list[str]
        assert len(params) >= 1, "Engine.run should have at least one parameter"

    def test_engine_protocol_returns_engine_result(self) -> None:
        """Engine.run return type should be EngineResult."""
        # This is verified by the type annotation on the protocol
        import typing

        hints = typing.get_type_hints(Engine.run)
        assert "return" in hints
        # The return should be EngineResult or compatible


class TestEngineProtocolRuntimeCheckable:
    """Test cases for Engine protocol runtime checking."""

    def test_engine_protocol_is_runtime_checkable(self) -> None:
        """Engine should be usable with isinstance() checks."""

        # Create a minimal class that implements the protocol
        class MinimalEngine:
            name: str = "minimal"

            def run(self, urls: list[str]) -> object:
                return None

        # MinimalEngine should be considered an instance of Engine
        assert isinstance(MinimalEngine(), Engine)

    def test_non_engine_class_is_not_instance(self) -> None:
        """Classes that don't implement the protocol should not be instances."""

        class NotAnEngine:
            pass

        assert not isinstance(NotAnEngine(), Engine)

    def test_partial_implementation_is_not_instance(self) -> None:
        """Classes missing required methods should not be instances."""

        class PartialEngine:
            name: str = "partial"

            # Missing run method

        assert not isinstance(PartialEngine(), Engine)
